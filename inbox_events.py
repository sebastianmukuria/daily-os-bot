"""
Inbox → Calendar extraction. A cheap prefilter cuts noise, then an LLM extracts
real-world events (flights, hotels, reservations, tickets) from an email as
structured data. build_event_times turns that into calendar start/end strings.

Pure helpers (prefilter, build_event_times) are unit-tested; extract_event is the
single LLM call. The bot's inbox_calendar_sync job orchestrates fetch → extract →
dedup → create.
"""

import os
from datetime import datetime, timedelta

import anthropic

EVENT_MODEL = os.environ.get("EVENT_MODEL", "claude-haiku-4-5")

# Words that suggest a real-world event/booking. Cheap gate before the LLM.
_EVENT_HINTS = [
    "confirmation", "confirmed", "reservation", "reserved", "booking", "booked",
    "itinerary", "flight", "boarding", "hotel", "check-in", "checkin", "airbnb",
    "ticket", "tickets", "your order for", "you're going", "rsvp", "invite",
    "table for", "appointment", "scheduled for", "pickup", "delivery window",
]
_EVENT_NEGATIVES = ["unsubscribe to stop", "% off", "sale ends", "newsletter"]


def looks_like_event(email: dict) -> bool:
    """Cheap pre-LLM gate: does this email plausibly describe a dated event?"""
    text = f"{email.get('subject','')} {email.get('snippet','')}".lower()
    if any(n in text for n in _EVENT_NEGATIVES):  # obvious promo — skip the LLM
        return False
    return any(h in text for h in _EVENT_HINTS)


def build_event_times(date: str, start_time: str, end_time: str, all_day: bool):
    """Turn extracted fields into (start, end, all_day) for _create_calendar_event.
    Timed: ISO datetimes (default +1h end). All-day: date strings."""
    if all_day or not start_time:
        nxt = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
        return date, nxt, True  # Google all-day end is exclusive → next day
    start = f"{date}T{start_time}:00"
    if end_time:
        end = f"{date}T{end_time}:00"
    else:
        dt = datetime.fromisoformat(start) + timedelta(hours=1)
        end = dt.isoformat()
    return start, end, False


EXTRACT_TOOL = {
    "name": "record_event",
    "description": "Record a real-world event/booking extracted from an email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_event": {"type": "boolean", "description": "True only if this email describes a dated real-world event/booking (flight, hotel, reservation, ticket, appointment). Promotions, receipts with no event, and newsletters are False."},
            "title": {"type": "string", "description": "Clean, readable event title (no 'FW:'/'RE:'/confirmation-number noise)"},
            "date": {"type": "string", "description": "Event date YYYY-MM-DD, or empty if no clear date"},
            "start_time": {"type": "string", "description": "24h HH:MM ET, or empty if unknown / all-day"},
            "end_time": {"type": "string", "description": "24h HH:MM ET, or empty"},
            "all_day": {"type": "boolean"},
            "location": {"type": "string", "description": "Venue or address, or empty"},
            "confirmation": {"type": "string", "description": "Confirmation/booking number, or empty"},
            "followup": {"type": "string", "description": "A short follow-up task if the email needs one (e.g. 'Check in for flight'), else empty"},
        },
        "required": ["is_event", "title", "date", "start_time", "end_time", "all_day", "location", "confirmation", "followup"],
    },
}

_SYSTEM = (
    "You extract real-world events and bookings from emails for a personal calendar. "
    "Only genuine dated events count — flights, hotels, restaurant/appointment "
    "reservations, event tickets, confirmed meetings. Promotions, marketing, "
    "newsletters, and plain receipts with no event are NOT events (is_event=false). "
    "Give a clean human-readable title; never guess a date you don't see."
)


def extract_event(email: dict, client: anthropic.Anthropic = None) -> dict:
    client = client or anthropic.Anthropic()
    content = (
        f"From: {email.get('from')}\nSubject: {email.get('subject')}\n\n"
        f"{email.get('snippet', '')}"
    )
    resp = client.messages.create(
        model=EVENT_MODEL, max_tokens=400,
        system=_SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_event"},
        messages=[{"role": "user", "content": content}],
    )
    block = next(b for b in resp.content if b.type == "tool_use")
    return block.input
