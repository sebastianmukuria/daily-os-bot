"""
Pure logic for the job pipeline: matching a classified email to a record, and
deciding status transitions. No I/O — everything here is deterministic and
unit-tested (test_pipeline_state.py), so it's safe to evolve with confidence.

The ingestion layer (PR F) fetches records from Notion, calls match_application,
then decide_transition, and applies the result (or asks via Telegram).
"""

from datetime import date

CONFIDENCE_THRESHOLD = 0.85  # below this we confirm instead of writing (spec §8)
GHOST_DAYS = 21              # Applied with no activity this long -> Ghosted (spec §4)

# Forward-progression stages, ranked. Rejected / Withdrawn / Ghosted are handled
# separately (terminal or special), so they're not ranked here.
STAGE_RANK = {
    "Applied": 1,
    "Recruiter Screen": 2,
    "Interviewing": 3,
    "Final Round": 4,
    "Offer": 5,
}

# Which stage a progression event implies.
EVENT_TARGET = {
    "applied": "Applied",
    "screen_invite": "Recruiter Screen",
    "interview_scheduled": "Interviewing",
}


def decide_transition(current_status: str, event_type: str, confidence: float,
                      threshold: float = CONFIDENCE_THRESHOLD) -> dict:
    """Decide what to do with an existing record given a classified email.

    Returns {"action": "set"|"none"|"confirm", "to_status"?: str, "reason": str}.
    - Forward-only: progression events never move a record backward.
    - Rejection overrides any stage; offer wins too.
    - Below-confidence events ask for confirmation instead of writing.
    - Terminal records (Rejected/Withdrawn) aren't auto-advanced — they confirm.
    """
    if confidence < threshold:
        return {"action": "confirm", "reason": "below confidence threshold"}

    if event_type == "rejection":
        if current_status == "Rejected":
            return {"action": "none", "reason": "already rejected"}
        return {"action": "set", "to_status": "Rejected", "reason": "rejection email"}

    if event_type == "offer":
        if current_status == "Offer":
            return {"action": "none", "reason": "already at offer"}
        return {"action": "set", "to_status": "Offer", "reason": "offer email"}

    if event_type in EVENT_TARGET:
        if current_status in ("Rejected", "Withdrawn"):
            return {"action": "confirm",
                    "reason": f"{event_type} but record is {current_status}"}
        target = EVENT_TARGET[event_type]
        cur = STAGE_RANK.get(current_status, 0)   # Ghosted -> 0, so it re-advances
        tgt = STAGE_RANK.get(target, 0)
        if tgt > cur:
            return {"action": "set", "to_status": target,
                    "reason": f"{event_type} advances {current_status}->{target}"}
        return {"action": "none", "reason": f"{event_type}: no forward movement"}

    # reschedule, recruiter_inbound, other
    return {"action": "none", "reason": f"{event_type}: no status change"}


def should_ghost(status: str, last_activity_iso: str | None, today_iso: str,
                 days: int = GHOST_DAYS) -> bool:
    """True if an Applied record has had no activity for `days`+ days."""
    if status != "Applied" or not last_activity_iso:
        return False
    gap = (date.fromisoformat(today_iso) - date.fromisoformat(last_activity_iso)).days
    return gap >= days


def _filter_role_tiered(records: list, role: str) -> list:
    """Narrow by role using the tightest tier that hits, so 'Analyst I' doesn't
    also match 'Analyst II' (which a plain substring check would)."""
    r = (role or "").strip().lower()
    if not r:
        return records

    def rr(rec):
        return (rec.get("role") or "").strip().lower()

    exact = [x for x in records if rr(x) == r]
    if exact:
        return exact
    edge = [x for x in records if rr(x).endswith(r) or rr(x).startswith(r)]
    if edge:
        return edge
    return [x for x in records if r in rr(x)]


def match_application(company: str, role: str, thread_id: str, records: list) -> dict:
    """Match a classified email to an existing record.

    records: list of {id, company, role, status, thread_ids(list)}.
    Returns {"match": record|None, "by": "thread"|"company_role"|None, "candidates"?: list}.
    Thread-ID match wins; otherwise company (substring) + role (tiered) match.
    """
    if thread_id:
        for r in records:
            if thread_id in (r.get("thread_ids") or []):
                return {"match": r, "by": "thread"}

    comp = (company or "").strip().lower()
    if not comp:
        return {"match": None, "by": None}

    # Substring either direction: a classifier's verbose name ("LA28-USOPP",
    # "GitHub, Inc.") should still match a terser record ("LA28", "GitHub").
    def company_matches(rec):
        rc = (rec.get("company") or "").strip().lower()
        return bool(rc) and (comp in rc or rc in comp)

    cands = [r for r in records if company_matches(r)]
    if role:
        cands = _filter_role_tiered(cands, role)

    if len(cands) == 1:
        return {"match": cands[0], "by": "company_role"}
    if len(cands) > 1:
        return {"match": None, "by": "ambiguous", "candidates": cands}
    return {"match": None, "by": None}
