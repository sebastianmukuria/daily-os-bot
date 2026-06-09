"""
One-time 90-day job-pipeline backfill.

Reconstructs Notion records from ~90 days of Gmail history. Phases:
  COLLECT  fetch newer_than:90d (full history, incl. poller-labeled mail),
           prefilter, classify (cached by message-id), attach ts.
  CLUSTER  group events into per-role applications: thread-id union first,
           then tiered role match; distinct roles (Analyst I vs II) stay split,
           ambiguous merges go to review.
  REPLAY   fold each group's events (chronological) through the SAME state
           machine the live poller uses -> final status; ghost stale Applieds.
  DECIDE   dedupe against current Notion records (thread-id / company+role);
           emit create / update(forward-only) / skip / review.
  WRITE    --apply only: backfill_create / backfill_update with REAL dates.

Usage:
  python3 pipeline_backfill.py                 # dry run — prints the plan, writes nothing
  python3 pipeline_backfill.py --max 200       # cap the fetch (test slice)
  python3 pipeline_backfill.py --apply          # execute auto-writable groups
  python3 pipeline_backfill.py --apply --include-review   # also write review groups (Auto)

The pure functions (norm_company / cluster_events / replay_group / decide_action)
take plain dicts and are unit-tested in test_pipeline_backfill.py.
"""

import os
import re
import sys
import json
import asyncio
from datetime import datetime, timezone

import pytz

from pipeline_classifier import prefilter, classify_email
from pipeline_state import (
    _filter_role_tiered, decide_transition, should_ghost, match_application,
    STAGE_RANK, CONFIDENCE_THRESHOLD,
)

_ET = pytz.timezone("America/New_York")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "backfill_cache.json")

# First event of a group seeds the status from its event type.
SEED_STATUS = {
    "applied": "Applied", "screen_invite": "Recruiter Screen",
    "interview_scheduled": "Interviewing", "offer": "Offer", "rejection": "Rejected",
}
# Flags that force a group into manual review rather than an auto write.
HARD_FLAGS = {
    "ambiguous role match", "empty role, multiple roles at company",
    "empty company", "low-confidence seed", "ambiguous existing match",
}
ACTIVE_STATUSES = {"Recruiter Screen", "Interviewing", "Final Round", "Offer"}

# Known current state to self-validate the reconstruction against (spec §7).
KNOWN_TARGET_STATE = [
    ("affirm", "Data Analyst I", "active"),
    ("affirm", "Data Analyst II", "Rejected"),
    ("spacex", "Starlink Growth BA", "Rejected"),
    ("spacex", "Starlink Growth Sr BA", "Rejected"),
    ("capital group", "Data Product Analyst", "Rejected"),
    ("netflix", "Data Analyst Production Finance", "Applied"),
    ("salesforce", "Data Analytics Senior Analyst", "Applied"),
    ("disney", "Inventory Analytics", "Rejected"),
    ("tiktok", "", "Applied"),
    ("google", "EA AI Safety", "Rejected"),
]

_LEGAL = re.compile(r"\b(inc|llc|ltd|corp|corporation|co)\b\.?", re.I)
_NOISE = re.compile(r"\b(via linkedin|careers|talent|recruiting|team|jobs)\b", re.I)


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(_ET).date().isoformat()


def norm_company(name: str) -> str:
    s = (name or "").lower()
    s = _NOISE.sub("", s)
    s = _LEGAL.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# --- CLUSTER -------------------------------------------------------------

def _new_group(ev: dict, cn: str) -> dict:
    return {"company_norm": cn, "company": ev.get("company") or ev.get("from", ""),
            "role": ev.get("role") or "", "events": [ev],
            "thread_ids": {ev["thread_id"]}, "flags": set()}


def _attach(group: dict, ev: dict) -> None:
    group["events"].append(ev)
    group["thread_ids"].add(ev["thread_id"])
    role = ev.get("role") or ""
    if len(role) > len(group["role"]):  # keep the longest, most-specific role text
        group["role"] = role


def cluster_events(events: list) -> list:
    """Group classified email events into per-role application clusters."""
    groups = []
    for ev in sorted(events, key=lambda e: e["ts"]):
        cn = norm_company(ev.get("company", ""))
        if not cn:
            g = _new_group(ev, cn)
            g["flags"].add("empty company")
            groups.append(g)
            continue

        cohort = [g for g in groups if g["company_norm"] == cn]
        # 1. Thread-id is a hard merge signal — overrides role text.
        tg = next((g for g in cohort if ev["thread_id"] in g["thread_ids"]), None)
        if tg:
            _attach(tg, ev)
            continue

        role = ev.get("role") or ""
        if not role:
            if len(cohort) == 1:
                _attach(cohort[0], ev)
            else:
                g = _new_group(ev, cn)
                if len(cohort) > 1:
                    g["flags"].add("empty role, multiple roles at company")
                groups.append(g)
            continue

        # 2. Tiered role match across the company's existing groups.
        recs = [{"role": g["role"], "_g": g} for g in cohort]
        matched = _filter_role_tiered(recs, role)
        if len(matched) == 1:
            _attach(matched[0]["_g"], ev)
        else:
            g = _new_group(ev, cn)
            if len(matched) > 1:
                g["flags"].add("ambiguous role match")
            groups.append(g)
    return groups


# --- REPLAY --------------------------------------------------------------

def replay_group(group: dict, today_iso: str) -> dict:
    evs = sorted(group["events"], key=lambda e: e["ts"])
    status = None
    applied = None
    flags = set(group["flags"])

    for ev in evs:
        et, conf = ev["event_type"], ev["confidence"]
        if status is None:
            if et != "applied" and conf < CONFIDENCE_THRESHOLD:
                flags.add("low-confidence seed")
            status = SEED_STATUS.get(et, "Applied")
            if et == "applied":
                applied = ev["ts"]
        else:
            d = decide_transition(status, et, conf)
            if d["action"] == "set":
                status = d["to_status"]
            elif d["action"] == "confirm":
                flags.add(f"confirm:{et}")
        if et == "applied" and applied is None:
            applied = ev["ts"]

    last = evs[-1]["ts"]
    if applied is None:
        applied = evs[0]["ts"]
        flags.add("inferred applied date")

    last_iso = _ms_to_iso(last)
    if status == "Applied" and should_ghost("Applied", last_iso, today_iso):
        status = "Ghosted"

    return {
        "company": group["company"], "role": group["role"], "status": status,
        "applied_date": _ms_to_iso(applied), "last_activity": last_iso,
        "thread_ids": sorted(group["thread_ids"]), "flags": sorted(flags),
        "timeline": [{"date": _ms_to_iso(e["ts"]), "event": e["event_type"],
                      "conf": e["confidence"], "subject": e["subject"]} for e in evs],
    }


# --- DECIDE --------------------------------------------------------------

def _should_update(from_s: str, to_s: str) -> bool:
    if to_s == from_s:
        return False
    if from_s in ("Rejected", "Withdrawn"):  # don't resurrect a closed record
        return False
    if to_s in ("Rejected", "Offer"):        # authoritative finals
        return True
    return STAGE_RANK.get(to_s, 0) > STAGE_RANK.get(from_s, 0)


def _has_hard_flag(flags: list) -> bool:
    return any(f.startswith("confirm:") or f in HARD_FLAGS for f in flags)


def decide_action(recon: dict, existing: list) -> dict:
    if _has_hard_flag(recon["flags"]):
        return {"action": "review", "recon": recon, "match": None,
                "reason": ", ".join(recon["flags"])}

    matched = None
    for tid in recon["thread_ids"]:
        m = match_application(recon["company"], recon["role"], tid, existing)
        if m.get("match"):
            matched = m["match"]
            break
    if not matched:
        m = match_application(recon["company"], recon["role"], None, existing)
        if m.get("match"):
            matched = m["match"]
        elif m.get("by") == "ambiguous":
            return {"action": "review", "recon": recon, "match": None,
                    "reason": "ambiguous existing match"}

    if matched:
        if _should_update(matched.get("status"), recon["status"]):
            return {"action": "update", "recon": recon, "match": matched,
                    "reason": f"{matched.get('status')} → {recon['status']}"}
        return {"action": "skip", "recon": recon, "match": matched, "reason": "already current"}
    return {"action": "create", "recon": recon, "match": None, "reason": "new application"}


def diff_against_target(recons: list) -> list:
    """Compare reconstructed groups to KNOWN_TARGET_STATE — returns report lines."""
    lines = []
    for comp, role, expected in KNOWN_TARGET_STATE:
        hit = next((r for r in recons
                    if comp in norm_company(r["company"])
                    and (not role or role.lower() in r["role"].lower())), None)
        if not hit:
            lines.append(f"  MISSING  {comp} / {role or '(any)'}  (expected {expected})")
        elif expected == "active":
            ok = hit["status"] in ACTIVE_STATUSES
            lines.append(f"  {'MATCH  ' if ok else 'MISMATCH'} {comp} / {role} -> {hit['status']} (expected active)")
        else:
            ok = hit["status"] == expected
            lines.append(f"  {'MATCH  ' if ok else 'MISMATCH'} {comp} / {role} -> {hit['status']} (expected {expected})")
    return lines


# --- I/O orchestration ---------------------------------------------------

def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


async def collect(limit: int, cache: dict) -> list:
    import tools
    raw = await asyncio.to_thread(tools.gmail_fetch_all, "newer_than:90d", limit)
    job = [m for m in raw if prefilter(m)["job_related"]]
    print(f"fetched {len(raw)} messages, {len(job)} job-related; classifying...")

    sem = asyncio.Semaphore(6)

    async def classify(m):
        if m["id"] in cache:
            c = cache[m["id"]]
        else:
            async with sem:
                c = await asyncio.to_thread(classify_email, m)
            cache[m["id"]] = c
        m.update({"company": c["company"], "role": c["role"],
                  "event_type": c["event_type"], "confidence": c["confidence"]})
        return m

    classified = await asyncio.gather(*[classify(m) for m in job])
    _save_cache(cache)
    return [m for m in classified if m["event_type"] != "other"]


def print_plan(decisions: list, recons: list) -> None:
    tag = {"create": "+ create", "update": "~ update", "skip": "= skip", "review": "? review"}
    counts = {"create": 0, "update": 0, "skip": 0, "review": 0}
    print("\n=== BACKFILL PLAN (dry run — nothing written) ===\n")
    for d in sorted(decisions, key=lambda x: x["action"]):
        r = d["recon"]
        counts[d["action"]] += 1
        print(f"[{tag[d['action']]}] {r['company']} — {r['role'] or '(role?)'} -> {r['status']}"
              f"   applied {r['applied_date']}, last {r['last_activity']}  ({d['reason']})")
        for t in r["timeline"]:
            print(f"        {t['date']}  {t['event']:18} conf {t['conf']}  {t['subject'][:46]}")
    print(f"\ncounts: {counts}")
    print("\n=== vs KNOWN TARGET STATE ===")
    for line in diff_against_target(recons):
        print(line)


async def main(apply: bool, include_review: bool, limit: int) -> None:
    import tools
    cache = _load_cache()
    events = await collect(limit, cache)
    if not events:
        print("No job-related events found.")
        return

    groups = cluster_events(events)
    today_iso = datetime.now(_ET).date().isoformat()
    recons = [replay_group(g, today_iso) for g in groups]
    existing = await tools.gather_pipeline_records()
    decisions = [decide_action(r, existing) for r in recons]

    print_plan(decisions, recons)

    if not apply:
        print("\nDry run only. Re-run with --apply to write (pause the live poller first).")
        return

    print("\n=== APPLYING ===")
    for d in decisions:
        r = d["recon"]
        if d["action"] == "skip":
            continue
        if d["action"] == "review" and not include_review:
            print(f"  ? skipped (review): {r['company']} — {r['role']}")
            continue
        if d["action"] == "update":
            await tools.backfill_update(d["match"]["id"], d["match"].get("status"),
                                        r["status"], r["thread_ids"], r["last_activity"])
            print(f"  ~ updated: {r['company']} — {r['role']} -> {r['status']}")
        else:  # create (or review with --include-review)
            await tools.backfill_create(r["company"], r["role"], r["status"],
                                        r["thread_ids"], r["applied_date"], r["last_activity"])
            print(f"  + created: {r['company']} — {r['role']} ({r['status']})")
    print("Done.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    args = sys.argv[1:]
    limit = 2000
    if "--max" in args:
        limit = int(args[args.index("--max") + 1])
    asyncio.run(main(apply="--apply" in args, include_review="--include-review" in args, limit=limit))
