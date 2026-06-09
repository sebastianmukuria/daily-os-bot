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
