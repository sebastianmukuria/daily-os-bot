"""
Pure helpers for interview watching (no I/O — unit-tested).

is_interview / extract_company detect and label interview calendar events;
due_reminder / due_debrief decide, given 'now', whether an event is in the
~1h-before reminder window or the post-interview debrief window. The bot's
hourly interview_watch job uses these to fire reminders and debrief prompts.
"""

import re
from datetime import datetime

# Scheduling/notes tools that appearing on an invite strongly imply an interview.
INTERVIEW_DOMAINS = {"interviewplanner.com", "goodtime.io", "calendly.com", "metaview.ai"}


def _domain(addr: str) -> str:
    return (addr or "").lower().split("@")[-1].strip(" >").rstrip(">")


def is_interview(summary: str, attendees: list = None) -> bool:
    if "interview" in (summary or "").lower():
        return True
    for a in (attendees or []):
        if _domain(a) in INTERVIEW_DOMAINS:
            return True
    return False


def extract_company(summary: str) -> str:
    """Best-effort company name from an interview title like
    'Interview: Affirm x Sebastian' or 'Affirm <> Sebastian — Interview'."""
    s = re.sub(r"(?i)\binterview\b", "", summary or "")
    s = re.sub(r"[:|]", " ", s)
    # cut at common separators between company and the candidate/round
    s = re.split(r"\s+(?:x|<>|with|and|—|-|@)\s+", s, maxsplit=1)[0]
    return re.sub(r"\s+", " ", s).strip(" -—|:")


def due_reminder(start: datetime, now: datetime, window_min: int = 75) -> bool:
    """True if the interview starts within the next `window_min` minutes."""
    delta_min = (start - now).total_seconds() / 60
    return 0 < delta_min <= window_min


def due_debrief(start: datetime, now: datetime,
                assumed_len_min: int = 60, window_min: int = 180) -> bool:
    """True if the interview likely just ended (between its assumed end and
    `window_min` minutes after the start)."""
    since_start_min = (now - start).total_seconds() / 60
    return assumed_len_min <= since_start_min <= window_min
