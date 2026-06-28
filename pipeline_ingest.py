"""
Pipeline ingestion: turn classified emails into intended actions, and a dry-run
that exercises the whole read path against the real inbox without writing.

plan_email() is pure (match + state machine) and unit-tested. dry_run() does the
live read: fetch candidates -> prefilter -> classify -> plan -> report. It writes
nothing and labels nothing, so it's safe to run against the real mailbox.
"""

import asyncio

from pipeline_classifier import prefilter, classify_email
from pipeline_state import match_application, decide_transition, CONFIDENCE_THRESHOLD

_DECISION_TO_ACTION = {"set": "update", "none": "skip", "confirm": "confirm"}


def plan_email(email: dict, classification: dict, records: list) -> dict:
    """Pure: decide what an email implies for the pipeline.

    Returns {"action": "create"|"update"|"confirm"|"skip", ...context}.
    """
    company = classification.get("company", "")
    role = classification.get("role", "")
    event = classification.get("event_type", "other")
    conf = classification.get("confidence", 0.0)
    thread = email.get("thread_id")

    # 'other' is the classifier's "not a real job event" bucket — never actionable.
    if event == "other":
        return {"action": "skip", "event": event, "confidence": conf,
                "reason": "non-actionable event"}

    m = match_application(company, role, thread, records)

    if m.get("match"):
        rec = m["match"]
        d = decide_transition(rec.get("status", ""), event, conf)
        return {
            "action": _DECISION_TO_ACTION[d["action"]],
            "record": rec, "to_status": d.get("to_status"),
            "event": event, "confidence": conf, "reason": d["reason"],
        }

    if m.get("by") == "ambiguous":
        return {"action": "confirm", "event": event, "confidence": conf,
                "reason": "ambiguous company/role match", "candidates": m["candidates"]}

    # No existing record.
    if conf < CONFIDENCE_THRESHOLD:
        return {"action": "confirm", "company": company, "role": role, "event": event,
                "confidence": conf, "reason": "no match, below confidence threshold"}
    if event == "applied":
        return {"action": "create", "company": company, "role": role,
                "confidence": conf, "reason": "new application"}
    # High confidence, no match, but not an 'applied' event — ask before creating.
    return {"action": "confirm", "company": company, "role": role, "event": event,
            "confidence": conf, "reason": f"no match for {event} event"}


_ALERT_EVENTS = {"rejection", "interview_scheduled", "offer", "screen_invite"}
_EVENT_STATUS = {
    "applied": "Applied", "screen_invite": "Recruiter Screen",
    "interview_scheduled": "Interviewing", "offer": "Offer", "rejection": "Rejected",
}


async def apply_plan(plan: dict, email: dict) -> dict:
    """Execute a create/update/skip plan as Notion writes. Returns
    {"alert": str|None, "confirm": str|None} — text for Telegram. 'confirm' plans
    are NOT written; the user resolves them by replying to the confirm message."""
    import tools

    action = plan["action"]
    link = tools.gmail_message_link(email["id"])
    subject = email.get("subject", "")

    if action == "create":
        await tools._add_application(
            company=plan["company"], role=plan["role"], status="Applied",
            thread_id=email.get("thread_id"), confidence="Auto (unreviewed)",
        )
        return {"alert": f"Logged application — {plan['company']}: {plan['role']} (auto)\n{link}",
                "confirm": None}

    if action == "update":
        rec = plan["record"]
        await tools.apply_pipeline_transition(
            page_id=rec["id"], from_status=rec.get("status"), to_status=plan["to_status"],
            thread_id=email.get("thread_id"), existing_threads=rec.get("thread_ids", []),
            event=plan["event"], trigger=subject,
        )
        if plan["event"] in _ALERT_EVENTS:
            return {"alert": f"{rec['company']} — {rec['role']}: now {plan['to_status']}\n{link}",
                    "confirm": None}
        return {"alert": None, "confirm": None}

    if action == "confirm":
        who = f"{plan.get('company', '?')} — {plan.get('role', '?')}".strip()
        return {"alert": None, "confirm": (
            f"Not sure about this job email:\n{subject}\n"
            f"Looks like: {who} ({plan.get('event', '?')}, conf {plan.get('confidence')})\n"
            f"{plan['reason']}.\n{link}\n\n"
            "Reply to this message telling me what to do (e.g. 'log it as applied' "
            "or 'move Acme Analyst I to interviewing')."
        )}

    return {"alert": None, "confirm": None}  # skip


_ACTIVE = ["Offer", "Final Round", "Interviewing", "Recruiter Screen", "Applied"]


async def build_daily_digest(ghosted_names: list) -> str:
    """One Telegram-ready pipeline summary: active count by stage, today's
    interviews (from Calendar), overdue follow-ups, and anything just ghosted."""
    import tools
    from collections import Counter

    pipe = await tools._get_pipeline()
    apps = pipe["applications"]
    today = tools._today_et()
    active = [a for a in apps if a["status"] in _ACTIVE]
    by = Counter(a["status"] for a in active)

    overdue = [a for a in apps
               if a.get("next_action_due") and a["next_action_due"] <= today
               and a["status"] in _ACTIVE]

    try:
        cal = await asyncio.to_thread(tools._get_calendar_events, 1)
        interviews = [e for e in cal["events"] if "interview" in (e.get("summary") or "").lower()]
    except Exception:
        interviews = []

    lines = [f"Job pipeline — {today}"]
    if active:
        breakdown = ", ".join(f"{s} {by[s]}" for s in _ACTIVE if by.get(s))
        lines.append(f"Active: {len(active)}  ({breakdown})")
    else:
        lines.append("No active applications.")

    if interviews:
        lines.append("\nInterviews today:")
        lines += [f"  • {e['summary']} ({e['start']})" for e in interviews]
    if overdue:
        lines.append("\nOverdue follow-ups:")
        lines += [f"  • {a['company']} — {a.get('next_action') or 'follow up'} (due {a['next_action_due']})"
                  for a in overdue]
    if ghosted_names:
        lines.append("\nNewly ghosted (21d quiet): " + ", ".join(ghosted_names))

    return "\n".join(lines)


async def dry_run(limit: int = 30) -> dict:
    """Read-only pass over the inbox. Returns a report; writes/labels nothing."""
    import tools  # imported here to avoid a circular import at module load

    candidates = await asyncio.to_thread(tools.gmail_fetch_candidates, max_results=limit)
    records = await tools.gather_pipeline_records()

    report = {"scanned": len(candidates), "job_related": 0, "plans": []}
    for email in candidates:
        if not prefilter(email)["job_related"]:
            continue
        report["job_related"] += 1
        classification = await asyncio.to_thread(classify_email, email)
        plan = plan_email(email, classification, records)
        report["plans"].append({
            "subject": email["subject"],
            "from": email["from"],
            "classified": classification,
            "plan": {k: v for k, v in plan.items() if k != "record"},
            "matched": plan.get("record", {}).get("company") if plan.get("record") else None,
        })
    return report
