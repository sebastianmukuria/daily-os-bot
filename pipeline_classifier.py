"""
Two-pass email classifier for the job pipeline.

1. prefilter(email)  — cheap, deterministic, no LLM. Decides whether an email is
   job-related at all, using sender domains, recipient signals, and recruiting
   language. Filters out the inbox noise (and the known false positives:
   apartment-rental "applications", GitHub OAuth "applications").
2. classify_email(email) — LLM pass (Sonnet) for anything the prefilter passes.
   Returns structured {company, role, event_type, confidence} via forced tool use.

Run the fixtures eval: `python3 test_classifier.py`
"""

import os
import anthropic

# Classification runs unattended at volume (the 20-min poller + the one-time
# backfill). On an entry API tier that volume saturated the Sonnet rate limit and
# starved other Sonnet work (e.g. the scheduled briefings), so it runs on Haiku —
# a separate, more generous pool, and verified equally accurate on the fixtures.
# Override with CLASSIFIER_MODEL=claude-sonnet-4-6 on a higher tier if desired.
CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5")

EVENT_TYPES = [
    "applied", "screen_invite", "interview_scheduled", "reschedule",
    "rejection", "offer", "recruiter_inbound", "other",
]

# --- Heuristic prefilter knowledge (from real inbox patterns, spec §3) ---
NEGATIVE_DOMAINS = {"entrata.com", "github.com"}
NON_JOB_SUBJECT_HINTS = ["application payment", "oauth application", "third-party application"]
ATS_DOMAINS = {
    "greenhouse-mail.io", "us.greenhouse-mail.io", "myworkday.com", "jobs.netflix.com",
    "jobs.lever.co", "jobs.ashbyhq.com", "icims.com", "smartrecruiters.com",
}
KNOWN_JOB_SENDERS = {
    "jobs-noreply@linkedin.com", "no-reply@affirm.com",
    "no-reply-recruiting@spacex.com", "careers@jobs.netflix.com",
}
INTERVIEW_RECIPIENT_DOMAINS = {
    "interviewplanner.com", "metaview.ai", "goodtime.io", "calendly.com",
}
# The prefilter should be GENEROUS — a false negative drops a real application
# silently, while a false positive just gets correctly rejected by the LLM. So
# cover the common application-confirmation phrasings broadly.
RECRUITING_PHRASES = [
    # application confirmations
    "applying to", "for applying", "thanks for applying", "thank you for applying",
    "your application", "received your application", "received your resume",
    "we received your", "we've received your", "application received",
    "application was sent",
    # progression / outcome
    "moving forward", "not move forward", "pleased to offer", "would like to offer",
    "extend an offer", "next steps", "candidacy", "recruiter", "recruiting",
    "schedule a", "interview",
]


def _domain(addr: str) -> str:
    return addr.lower().split("@")[-1].strip(" >").rstrip(">")


def prefilter(email: dict) -> dict:
    """Decide if an email is job-related. email = {from, to[list], subject, snippet}."""
    sender = email.get("from", "").lower()
    domain = _domain(sender)
    subject = email.get("subject", "").lower()
    body = email.get("snippet", "").lower()
    recipients = " ".join(email.get("to", [])).lower()
    text = f"{subject} {body}"

    # Hard negatives first — "application" alone is never a signal.
    if domain in NEGATIVE_DOMAINS:
        return {"job_related": False, "reason": "non-job sender domain"}
    if any(h in subject for h in NON_JOB_SUBJECT_HINTS):
        return {"job_related": False, "reason": "non-job 'application' keyword"}

    # Strong positives.
    if domain in ATS_DOMAINS or sender in KNOWN_JOB_SENDERS:
        return {"job_related": True, "reason": "ATS / known job sender"}
    if any(d in recipients for d in INTERVIEW_RECIPIENT_DOMAINS):
        return {"job_related": True, "reason": "interview-scheduling recipient"}
    if "interview:" in subject:
        return {"job_related": True, "reason": "interview subject"}
    if any(ph in text for ph in RECRUITING_PHRASES):
        return {"job_related": True, "reason": "recruiting language"}

    return {"job_related": False, "reason": "no job signal"}


CLASSIFY_TOOL = {
    "name": "record_classification",
    "description": "Record the structured classification of a job-search email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "Hiring company, or empty if unclear"},
            "role": {"type": "string", "description": "Role title, or empty if unclear"},
            "event_type": {"type": "string", "enum": EVENT_TYPES},
            "confidence": {"type": "number", "description": "0.0 to 1.0"},
        },
        "required": ["company", "role", "event_type", "confidence"],
    },
}

_SYSTEM = (
    "You classify job-search emails for a personal pipeline tracker. Extract the hiring "
    "company, the role title, the event type, and your confidence (0-1). Only genuine job "
    "applications count: apartment-rental 'applications' and software OAuth 'applications' "
    "are NOT job-related — classify those as event_type 'other' with low confidence. "
    "Be precise; leave a field empty rather than guessing."
)


def classify_email(email: dict, client: anthropic.Anthropic = None) -> dict:
    """LLM pass — returns {company, role, event_type, confidence}."""
    client = client or anthropic.Anthropic()
    content = (
        f"From: {email.get('from')}\n"
        f"To: {', '.join(email.get('to', []))}\n"
        f"Subject: {email.get('subject')}\n\n"
        f"{email.get('snippet', '')}"
    )
    resp = client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=400,
        system=_SYSTEM,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "record_classification"},
        messages=[{"role": "user", "content": content}],
    )
    block = next(b for b in resp.content if b.type == "tool_use")
    return block.input
