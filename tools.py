import os
import json
import asyncio
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from notion_client import AsyncClient as NotionAsyncClient
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger("daily_os_bot.tools")

# Full set granted at auth time. Gmail uses 'modify' (read messages + apply the
# JobTracker label; never delete). The token itself records which scopes were
# actually granted — see _load_google_creds.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar", GMAIL_SCOPE]
PROCESSED_LABEL = "JobTracker/Processed"

# These are Notion *data source* IDs (new 2025-09-03 API). Querying happens on
# data sources, not databases. Page creation uses a data_source_id parent.
TASKS_DS_ID = "b550275a-0137-4cab-9231-7950e838eb34"
IDEAS_DS_ID = "b70516f3-6782-4256-837e-85bb5ce11b62"
PROJECTS_DS_ID = "c99375ae-b7d5-4225-bd45-fe7e204c4e9c"
READING_DS_ID = "29aba34c-4a58-4096-8c7f-f649976e7639"
HABITS_DS_ID = "818e9cd3-48a1-413e-9d38-aa74c6b4e480"
JOB_PIPELINE_DS_ID = "551e0098-cf2f-41eb-8dff-437501636fbe"
PIPELINE_EVENTS_DS_ID = "bf426aeb-7a4c-45d5-83ac-7155e28cca79"

PIPELINE_STATUSES = [
    "Applied", "Recruiter Screen", "Interviewing", "Final Round",
    "Offer", "Rejected", "Withdrawn", "Ghosted",
]
# Sort order for pipeline views: most promising / active first, closed last.
PIPELINE_STATUS_ORDER = {s: i for i, s in enumerate(
    ["Offer", "Final Round", "Interviewing", "Recruiter Screen", "Applied",
     "Ghosted", "Rejected", "Withdrawn"]
)}

# Real select option names in the Notion DBs (with emoji prefixes)
ENERGY_MAP = {"High": "⚡ High", "Medium": "🔋 Medium", "Low": "🪫 Low"}
STATUS_MAP = {
    "Not Started": "🔴 Not Started",
    "In Progress": "🟡 In Progress",
    "Done": "🟢 Done",
    "Blocked": "⚫ Blocked",
}
STATUS_NOT_STARTED = STATUS_MAP["Not Started"]
STATUS_DONE = STATUS_MAP["Done"]
PROJECT_STATUS_MAP = {
    "Active": "🟢 Active", "Paused": "⏸️ Paused", "Done": "✅ Done", "Idea": "💡 Idea",
}

ET = "America/New_York"

notion = NotionAsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))

TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def _load_google_creds() -> Credentials:
    """Load Google creds from the GOOGLE_TOKEN_JSON env var (cloud) or token.json (local).

    We deliberately do NOT force a scopes list here — the credentials use whatever
    scopes the token was actually granted (stored in the token). That way adding the
    Gmail scope is purely a re-auth step: no scope-mismatch refresh errors, and
    Calendar keeps working on an older calendar-only token.

    On ephemeral hosts (Railway/Render) the filesystem is wiped on redeploy, so the
    token can't live in a file there — set GOOGLE_TOKEN_JSON instead.
    """
    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if env_token:
        return Credentials.from_authorized_user_info(json.loads(env_token))
    if os.path.exists(TOKEN_PATH):
        return Credentials.from_authorized_user_file(TOKEN_PATH)
    raise RuntimeError(
        "Google not authenticated. Run auth_google.py locally, then either keep "
        "token.json or set GOOGLE_TOKEN_JSON in your environment."
    )


def _google_creds_refreshed() -> Credentials:
    creds = _load_google_creds()
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token to file only when running file-based (local).
        if not os.environ.get("GOOGLE_TOKEN_JSON"):
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
    return creds


def _get_calendar_service():
    return build("calendar", "v3", credentials=_google_creds_refreshed())


def _get_gmail_service():
    creds = _google_creds_refreshed()
    if GMAIL_SCOPE not in (creds.scopes or []):
        raise RuntimeError(
            "Gmail isn't authorized yet. Re-run auth_google.py (it now requests Gmail "
            "too), then update token.json / GOOGLE_TOKEN_JSON."
        )
    return build("gmail", "v1", credentials=creds)


def gmail_processed_label_id(service) -> str:
    """Get the JobTracker/Processed label id, creating the label if needed."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == PROCESSED_LABEL:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={
            "name": PROCESSED_LABEL,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return created["id"]


def gmail_check() -> dict:
    """Plumbing self-test: confirm Gmail auth works and the processed-label exists.
    Used to validate Phase C before any ingestion logic is built on top."""
    service = _get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    label_id = gmail_processed_label_id(service)
    return {"email": profile.get("emailAddress"), "processed_label_id": label_id}


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def gmail_fetch_candidates(
    query: str = "newer_than:3d -label:JobTracker/Processed",
    max_results: int = 30,
) -> list:
    """Fetch recent, not-yet-processed messages as lightweight dicts
    {id, thread_id, from, to[list], subject, snippet} for the classifier."""
    service = _get_gmail_service()
    listing = service.users().messages().list(
        userId="me", q=query, maxResults=max_results,
    ).execute()

    out = []
    for ref in listing.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject"],
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        out.append({
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "from": _header(headers, "From"),
            "to": [a.strip() for a in _header(headers, "To").split(",") if a.strip()],
            "subject": _header(headers, "Subject"),
            "snippet": msg.get("snippet", ""),
        })
    return out


def gmail_apply_processed_label(message_id: str) -> None:
    service = _get_gmail_service()
    label_id = gmail_processed_label_id(service)
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]},
    ).execute()


def gmail_fetch_all(query: str, max_results: int = 2000) -> list:
    """Like gmail_fetch_candidates but paginates the full result set and includes
    `ts` (internalDate, epoch ms) — needed by the backfill to replay events in
    chronological order. Used for the one-time 90-day scan, not the live poller."""
    service = _get_gmail_service()
    out, token = [], None
    while len(out) < max_results:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=token,
            maxResults=min(100, max_results - len(out)),
        ).execute()
        for ref in resp.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject"],
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            out.append({
                "id": msg["id"],
                "thread_id": msg.get("threadId"),
                "ts": int(msg.get("internalDate", 0)),
                "from": _header(headers, "From"),
                "to": [a.strip() for a in _header(headers, "To").split(",") if a.strip()],
                "subject": _header(headers, "Subject"),
                "snippet": msg.get("snippet", ""),
            })
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


async def backfill_create(company: str, role: str, status: str, thread_ids: list,
                          applied_date: str, last_activity: str,
                          confidence: str = "Auto (unreviewed)") -> str:
    """Create a pipeline record stamped with REAL historical dates (not today)."""
    props: dict = {
        "Company": {"title": [{"text": {"content": company}}]},
        "Role": {"rich_text": [{"text": {"content": role}}]},
        "Status": {"select": {"name": status}},
        "Confidence": {"select": {"name": confidence}},
        "Applied date": {"date": {"start": applied_date}},
        "Last activity": {"date": {"start": last_activity}},
        "Gmail thread IDs": {"rich_text": [{"text": {"content": ", ".join(thread_ids)[:2000]}}]},
    }
    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": JOB_PIPELINE_DS_ID},
        properties=props,
    )
    return page["id"]


async def backfill_update(page_id: str, from_status: str, to_status: str,
                          thread_ids: list, last_activity: str) -> None:
    """Forward-only update with real Last activity; logs ONE net Pipeline Events
    row only if the status actually changed (avoids re-run log spam)."""
    await notion.pages.update(page_id=page_id, properties={
        "Status": {"select": {"name": to_status}},
        "Last activity": {"date": {"start": last_activity}},
        "Gmail thread IDs": {"rich_text": [{"text": {"content": ", ".join(thread_ids)[:2000]}}]},
    })
    if to_status != from_status:
        await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": PIPELINE_EVENTS_DS_ID},
            properties={
                "Event": {"title": [{"text": {"content": f"{from_status or '—'} → {to_status}"}}]},
                "Timestamp": {"date": {"start": last_activity}},
                "From Status": {"rich_text": [{"text": {"content": from_status or ""}}]},
                "To Status": {"rich_text": [{"text": {"content": to_status}}]},
                "Trigger": {"rich_text": [{"text": {"content": "backfill"}}]},
                "Application": {"relation": [{"id": page_id}]},
            },
        )


async def sweep_ghosted(dry: bool = False) -> list:
    """Move Applied records with 21+ days of no activity to Ghosted. Returns the
    list of affected 'Company — Role' strings. dry=True lists without writing."""
    from pipeline_state import should_ghost
    res = await notion.data_sources.query(
        data_source_id=JOB_PIPELINE_DS_ID,
        filter={"property": "Status", "select": {"equals": "Applied"}},
    )
    today = _today_et()
    ghosted = []
    for page in res.get("results", []):
        p = page["properties"]
        if should_ghost("Applied", _date(p, "Last activity"), today):
            label = f"{_title(p, 'Company')} — {_rich_text(p, 'Role')}"
            if not dry:
                # Status only — don't touch threads or Last activity (ghosting isn't activity).
                await notion.pages.update(
                    page_id=page["id"],
                    properties={"Status": {"select": {"name": "Ghosted"}}},
                )
                await notion.pages.create(
                    parent={"type": "data_source_id", "data_source_id": PIPELINE_EVENTS_DS_ID},
                    properties={
                        "Event": {"title": [{"text": {"content": "Applied → Ghosted"}}]},
                        "Timestamp": {"date": {"start": today}},
                        "From Status": {"rich_text": [{"text": {"content": "Applied"}}]},
                        "To Status": {"rich_text": [{"text": {"content": "Ghosted"}}]},
                        "Trigger": {"rich_text": [{"text": {"content": "ghosted sweep (21d no activity)"}}]},
                        "Application": {"relation": [{"id": page["id"]}]},
                    },
                )
            ghosted.append(label)
    return ghosted


async def gather_pipeline_records() -> list:
    """All Job Pipeline records as {id, company, role, status, thread_ids[list]} —
    the input the matcher needs to dedupe incoming emails against existing records."""
    res = await notion.data_sources.query(data_source_id=JOB_PIPELINE_DS_ID)
    records = []
    for page in res.get("results", []):
        p = page["properties"]
        threads = _rich_text(p, "Gmail thread IDs") or ""
        records.append({
            "id": page["id"],
            "company": _title(p, "Company"),
            "role": _rich_text(p, "Role") or "",
            "status": _select(p, "Status"),
            "thread_ids": [t.strip() for t in threads.split(",") if t.strip()],
        })
    return records


TOOLS = [
    {
        "name": "get_tasks",
        "description": (
            "Get tasks from Sebastian's Notion Tasks database. "
            "Returns active (not done) tasks by default, ordered by energy (High → Medium → Low). "
            "Stale tasks (not updated in 3+ days) are flagged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_energy": {
                    "type": "string",
                    "description": "Optional: filter by energy level",
                    "enum": ["High", "Medium", "Low"],
                },
                "include_done": {
                    "type": "boolean",
                    "description": "Set true to include completed tasks. Default: false",
                },
            },
        },
    },
    {
        "name": "create_task",
        "description": (
            "Create a new task in Sebastian's Notion Tasks database. Always enrich it: "
            "infer the energy from how demanding the task is, set the type, and link the "
            "project when it clearly belongs to one. Status starts as Not Started."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name (polished + emoji)"},
                "energy": {
                    "type": "string",
                    "description": "Infer from how cognitively demanding the task is. Default: Medium",
                    "enum": ["High", "Medium", "Low"],
                },
                "type": {
                    "type": "string",
                    "description": "Task type. Use 'Appointment' for things with a set time/place, 'Admin/Inbox' for quick admin. Default: Task",
                    "enum": ["Task", "Appointment", "Admin/Inbox"],
                },
                "project": {
                    "type": "string",
                    "description": "Name (or partial) of the project this task belongs to, to link it. Only set if it clearly fits one of Sebastian's projects.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date in YYYY-MM-DD format (optional)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark a task as done in Sebastian's Notion Tasks database. "
            "Searches by name — partial match is fine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Name or partial name of the task to complete",
                },
            },
            "required": ["task_name"],
        },
    },
    {
        "name": "update_task",
        "description": (
            "Edit an EXISTING task in Sebastian's Notion Tasks database — change its "
            "energy, due date, status, name, or notes. Finds the task by name (partial "
            "match is fine). Only the fields you pass are changed. Use this instead of "
            "creating a new task when Sebastian wants to modify one that already exists "
            "(e.g. 'make that high energy', 'push it to Friday', 'mark it in progress')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Name or partial name of the task to edit",
                },
                "new_name": {"type": "string", "description": "Rename the task (optional)"},
                "energy": {
                    "type": "string",
                    "description": "New energy level (optional)",
                    "enum": ["High", "Medium", "Low"],
                },
                "due_date": {
                    "type": "string",
                    "description": "New due date in YYYY-MM-DD format (optional)",
                },
                "status": {
                    "type": "string",
                    "description": "New status (optional)",
                    "enum": ["Not Started", "In Progress", "Done", "Blocked"],
                },
                "notes": {"type": "string", "description": "Replace the task's notes (optional)"},
            },
            "required": ["task_name"],
        },
    },
    {
        "name": "add_idea",
        "description": "Add a new idea to Sebastian's Notion Ideas database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Idea title"},
                "category": {
                    "type": "string",
                    "description": "Optional category",
                    "enum": ["Project", "Learning", "Writing", "Life", "Other"],
                },
                "notes": {"type": "string", "description": "Additional notes (optional)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_projects",
        "description": (
            "Get projects from Sebastian's Notion Projects database. "
            "Returns active projects by default. Useful for check-ins."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional: filter by status. Default shows Active only.",
                    "enum": ["Active", "Paused", "Done", "Idea"],
                },
            },
        },
    },
    {
        "name": "add_project",
        "description": "Create a new project in Sebastian's Notion Projects database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "description": {"type": "string", "description": "What the project is (optional)"},
                "check_in_frequency": {
                    "type": "string",
                    "description": "How often to check in (optional)",
                    "enum": ["Daily", "Every few days", "Weekly"],
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_reading_list",
        "description": "Get items from Sebastian's Notion Reading List database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional: filter by status",
                    "enum": ["Want to read", "Reading", "Done"],
                },
            },
        },
    },
    {
        "name": "add_reading",
        "description": "Add an item (book, article, paper, video, podcast) to Sebastian's Reading List.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the item"},
                "type": {
                    "type": "string",
                    "description": "Type of item (optional)",
                    "enum": ["Book", "Article", "Paper", "Video", "Podcast"],
                },
                "url": {"type": "string", "description": "Link to the item (optional)"},
                "notes": {"type": "string", "description": "Notes (optional)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_calendar_events",
        "description": "Get upcoming events from Sebastian's Google Calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to look. Default: 7",
                },
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create an event on Sebastian's Google Calendar. "
            "For timed events, pass start_datetime (and optionally end_datetime) as ISO 8601 "
            "in Eastern Time, e.g. '2026-06-09T14:00:00'. If end is omitted, defaults to 1 hour. "
            "For all-day events, set all_day=true and pass dates as 'YYYY-MM-DD'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start_datetime": {
                    "type": "string",
                    "description": "Start. ISO datetime '2026-06-09T14:00:00' or date 'YYYY-MM-DD' if all_day.",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "End (optional). Same format as start. Defaults to +1h for timed events.",
                },
                "all_day": {
                    "type": "boolean",
                    "description": "True for all-day events. Default: false",
                },
                "location": {"type": "string", "description": "Location (optional)"},
                "description": {"type": "string", "description": "Event notes (optional)"},
            },
            "required": ["summary", "start_datetime"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": (
            "Edit an EXISTING Google Calendar event instead of creating a new one. "
            "Use this when Sebastian wants to change a detail of an event that already "
            "exists — e.g. add/change the location, move the time, rename it. Only the "
            "fields you pass are changed; leave the rest out. You need the event's id: "
            "use the id returned when you created it, or call get_calendar_events to find it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The id of the event to edit"},
                "summary": {"type": "string", "description": "New title (optional)"},
                "start_datetime": {
                    "type": "string",
                    "description": "New start (optional). ISO datetime or 'YYYY-MM-DD' if all_day.",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "New end (optional). Same format as start.",
                },
                "all_day": {
                    "type": "boolean",
                    "description": "Set true if the new start/end are all-day dates. Default: false",
                },
                "location": {"type": "string", "description": "New location (optional)"},
                "description": {"type": "string", "description": "New notes (optional)"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "get_habits",
        "description": (
            "Get Sebastian's recurring habits (gym, vitamins, meditation, etc.) with "
            "their cadence, current streak, and whether each is already done today."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_habit",
        "description": (
            "Mark a habit as done for today (updates its streak). Use this when "
            "Sebastian says he did something habitual, e.g. 'took my vitamins', "
            "'went to the gym', 'meditated'. Finds the habit by name (partial match)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "habit_name": {
                    "type": "string",
                    "description": "Name or partial name of the habit to check off",
                },
            },
            "required": ["habit_name"],
        },
    },
    {
        "name": "add_habit",
        "description": "Create a new recurring habit to track.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Habit name (polished + emoji)"},
                "cadence": {
                    "type": "string",
                    "description": "How often. Default: Daily",
                    "enum": ["Daily", "Weekdays", "MWF", "Weekly"],
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_pipeline",
        "description": "Get Sebastian's job applications from the Job Pipeline, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional: filter by status",
                    "enum": PIPELINE_STATUSES,
                },
            },
        },
    },
    {
        "name": "add_application",
        "description": (
            "Log a new job application in the pipeline. Use when Sebastian says he applied "
            "to a role. Records are per-role (one company can have several). Status defaults "
            "to Applied, Confidence to Confirmed (he entered it manually)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name"},
                "role": {"type": "string", "description": "Role title"},
                "status": {"type": "string", "enum": PIPELINE_STATUSES, "description": "Default: Applied"},
                "posting_url": {"type": "string", "description": "Link to the job posting (optional)"},
                "source": {
                    "type": "string",
                    "enum": ["Direct", "LinkedIn", "Referral", "Recruiter inbound"],
                    "description": "How he found/applied (optional)",
                },
                "location": {
                    "type": "string",
                    "enum": ["SoCal", "Remote", "Hybrid", "Relocation"],
                    "description": "Location type (optional)",
                },
                "applied_date": {"type": "string", "description": "YYYY-MM-DD (optional; defaults to today)"},
            },
            "required": ["company", "role"],
        },
    },
    {
        "name": "update_application",
        "description": (
            "Update an existing application — change its status, stage detail, or next action. "
            "Finds it by company (and role if given). Use for 'move X to interviewing', "
            "'got rejected from Y', 'offer from Z'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name (partial ok)"},
                "role": {"type": "string", "description": "Role, to disambiguate if multiple at one company (optional)"},
                "status": {"type": "string", "enum": PIPELINE_STATUSES, "description": "New status (optional)"},
                "stage_detail": {"type": "string", "description": "Free-text stage detail (optional)"},
                "next_action": {"type": "string", "description": "Next action text (optional)"},
                "next_action_due": {"type": "string", "description": "Next action due date YYYY-MM-DD (optional)"},
            },
            "required": ["company"],
        },
    },
    {
        "name": "add_application_note",
        "description": "Append a timestamped note to an application's stage detail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name (partial ok)"},
                "role": {"type": "string", "description": "Role, to disambiguate (optional)"},
                "note": {"type": "string", "description": "The note to append"},
            },
            "required": ["company", "note"],
        },
    },
]

# Server-side tool: Anthropic's API runs the search itself and returns the results
# inline, so there's nothing for us to execute — we just declare it alongside TOOLS.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


async def execute_tool(name: str, inputs: dict) -> Any:
    logger.info("tool call -> %s %s", name, inputs)
    try:
        result = await _dispatch_tool(name, inputs)
        logger.info("tool ok   -> %s", name)
        return result
    except Exception as e:
        # Log the full traceback (visible in Railway logs) and return a clear,
        # specific error so Claude can tell Sebastian exactly what failed and why.
        logger.exception("tool FAIL -> %s", name)
        return {"error": f"{type(e).__name__}: {e}", "tool": name}


async def _dispatch_tool(name: str, inputs: dict) -> Any:
    if name == "get_tasks":
        return await _get_tasks(
            filter_energy=inputs.get("filter_energy"),
            include_done=inputs.get("include_done", False),
        )
    elif name == "create_task":
        return await _create_task(
            name=inputs["name"],
            energy=inputs.get("energy", "Medium"),
            due_date=inputs.get("due_date"),
            type=inputs.get("type", "Task"),
            project=inputs.get("project"),
        )
    elif name == "complete_task":
        return await _complete_task(task_name=inputs["task_name"])
    elif name == "update_task":
        return await _update_task(
            task_name=inputs["task_name"],
            new_name=inputs.get("new_name"),
            energy=inputs.get("energy"),
            due_date=inputs.get("due_date"),
            status=inputs.get("status"),
            notes=inputs.get("notes"),
        )
    elif name == "add_idea":
        return await _add_idea(
            name=inputs["name"],
            category=inputs.get("category"),
            notes=inputs.get("notes"),
        )
    elif name == "get_projects":
        return await _get_projects(status=inputs.get("status", "Active"))
    elif name == "add_project":
        return await _add_project(
            name=inputs["name"],
            description=inputs.get("description"),
            check_in_frequency=inputs.get("check_in_frequency"),
        )
    elif name == "get_reading_list":
        return await _get_reading_list(status=inputs.get("status"))
    elif name == "add_reading":
        return await _add_reading(
            title=inputs["title"],
            type=inputs.get("type"),
            url=inputs.get("url"),
            notes=inputs.get("notes"),
        )
    elif name == "get_calendar_events":
        return await asyncio.to_thread(_get_calendar_events, inputs.get("days_ahead", 7))
    elif name == "create_calendar_event":
        return await asyncio.to_thread(
            _create_calendar_event,
            summary=inputs["summary"],
            start_datetime=inputs["start_datetime"],
            end_datetime=inputs.get("end_datetime"),
            all_day=inputs.get("all_day", False),
            location=inputs.get("location"),
            description=inputs.get("description"),
        )
    elif name == "update_calendar_event":
        return await asyncio.to_thread(
            _update_calendar_event,
            event_id=inputs["event_id"],
            summary=inputs.get("summary"),
            start_datetime=inputs.get("start_datetime"),
            end_datetime=inputs.get("end_datetime"),
            all_day=inputs.get("all_day", False),
            location=inputs.get("location"),
            description=inputs.get("description"),
        )
    elif name == "get_habits":
        return await _get_habits()
    elif name == "log_habit":
        return await _log_habit(habit_name=inputs["habit_name"])
    elif name == "add_habit":
        return await _add_habit(name=inputs["name"], cadence=inputs.get("cadence", "Daily"))
    elif name == "get_pipeline":
        return await _get_pipeline(status=inputs.get("status"))
    elif name == "add_application":
        return await _add_application(
            company=inputs["company"],
            role=inputs["role"],
            status=inputs.get("status", "Applied"),
            posting_url=inputs.get("posting_url"),
            source=inputs.get("source"),
            location=inputs.get("location"),
            applied_date=inputs.get("applied_date"),
        )
    elif name == "update_application":
        return await _update_application(
            company=inputs["company"],
            role=inputs.get("role"),
            status=inputs.get("status"),
            stage_detail=inputs.get("stage_detail"),
            next_action=inputs.get("next_action"),
            next_action_due=inputs.get("next_action_due"),
        )
    elif name == "add_application_note":
        return await _add_application_note(
            company=inputs["company"],
            role=inputs.get("role"),
            note=inputs["note"],
        )
    else:
        return {"error": f"Unknown tool: {name}"}


async def _get_tasks(filter_energy: str = None, include_done: bool = False) -> dict:
    conditions = []
    if not include_done:
        conditions.append({"property": "Status", "select": {"does_not_equal": STATUS_DONE}})
    if filter_energy:
        conditions.append({"property": "Energy", "select": {"equals": ENERGY_MAP[filter_energy]}})

    params: dict = {"data_source_id": TASKS_DS_ID}
    if conditions:
        params["filter"] = {"and": conditions} if len(conditions) > 1 else conditions[0]

    result = await notion.data_sources.query(**params)

    energy_order = {"High": 0, "Medium": 1, "Low": 2}
    today = datetime.now(timezone.utc).date()
    tasks = []

    for page in result.get("results", []):
        props = page["properties"]
        energy = _normalize_energy(_select(props, "Energy"))
        last_edited = datetime.fromisoformat(
            page["last_edited_time"].replace("Z", "+00:00")
        ).date()
        stale = _checkbox(props, "Stale") or (today - last_edited).days >= 3

        tasks.append({
            "name": _title(props, "Task"),
            "energy": energy,
            "status": _select(props, "Status"),
            "due_date": _date(props, "Due Date"),
            "stale": stale,
        })

    tasks.sort(key=lambda t: energy_order.get(t.get("energy") or "", 3))
    return {"tasks": tasks, "count": len(tasks)}


def _fold(s: str) -> str:
    """Lowercase and strip accents so a plain 'resume' query matches 'Résumé'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


async def _find_by_title(data_source_id: str, title_prop: str, query: str) -> list:
    """Accent- and case-insensitive title search. Notion's `contains` is
    accent-sensitive (so 'resume' misses 'Résumé'), so we fetch and match in
    Python — preferring an exact folded-title match, else any folded substring."""
    res = await notion.data_sources.query(data_source_id=data_source_id, page_size=100)
    q = _fold(query)
    pages = res.get("results", [])
    exact = [p for p in pages if _fold(_title(p["properties"], title_prop)) == q]
    return exact or [p for p in pages if q in _fold(_title(p["properties"], title_prop))]


async def _find_project(name: str) -> dict | None:
    matches = await _find_by_title(PROJECTS_DS_ID, "Name", name)
    return matches[0] if matches else None


async def get_project_names() -> list:
    """Names of active projects — injected into the system prompt so the bot can
    link new tasks to the right project without an extra lookup."""
    res = await notion.data_sources.query(
        data_source_id=PROJECTS_DS_ID,
        filter={"property": "Status", "select": {"equals": PROJECT_STATUS_MAP["Active"]}},
    )
    return [_title(p["properties"], "Name") for p in res.get("results", [])]


async def _create_task(
    name: str,
    energy: str = "Medium",
    due_date: str = None,
    type: str = "Task",
    project: str = None,
) -> dict:
    properties: dict = {
        "Task": {"title": [{"text": {"content": name}}]},
        "Energy": {"select": {"name": ENERGY_MAP.get(energy, ENERGY_MAP["Medium"])}},
        "Status": {"select": {"name": STATUS_NOT_STARTED}},
        "Type": {"select": {"name": type}},
    }
    if due_date:
        properties["Due Date"] = {"date": {"start": due_date}}

    linked_project = None
    if project:
        proj = await _find_project(project)
        if proj:
            properties["Project"] = {"relation": [{"id": proj["id"]}]}
            linked_project = _title(proj["properties"], "Name")

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": TASKS_DS_ID},
        properties=properties,
    )
    return {
        "success": True,
        "id": page["id"],
        "name": name,
        "energy": energy,
        "type": type,
        "project": linked_project,
    }


async def _find_task(task_name: str) -> dict | None:
    """Find a task by title — accent- and case-insensitive ('resume' finds 'Résumé')."""
    matches = await _find_by_title(TASKS_DS_ID, "Task", task_name)
    return matches[0] if matches else None


async def _complete_task(task_name: str) -> dict:
    page = await _find_task(task_name)
    if not page:
        return {"success": False, "error": f"No task found matching '{task_name}'"}

    found_name = _title(page["properties"], "Task")
    await notion.pages.update(
        page_id=page["id"],
        properties={"Status": {"select": {"name": STATUS_DONE}}},
    )
    return {"success": True, "completed": found_name}


async def _update_task(
    task_name: str,
    new_name: str = None,
    energy: str = None,
    due_date: str = None,
    status: str = None,
    notes: str = None,
) -> dict:
    """Edit an existing task — only the fields provided are changed."""
    page = await _find_task(task_name)
    if not page:
        return {"success": False, "error": f"No task found matching '{task_name}'"}

    old_name = _title(page["properties"], "Task")
    properties: dict = {}
    if new_name is not None:
        properties["Task"] = {"title": [{"text": {"content": new_name}}]}
    if energy is not None:
        properties["Energy"] = {"select": {"name": ENERGY_MAP.get(energy, energy)}}
    if status is not None:
        properties["Status"] = {"select": {"name": STATUS_MAP.get(status, status)}}
    if due_date is not None:
        properties["Due Date"] = {"date": {"start": due_date}}
    if notes is not None:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    if not properties:
        return {"success": False, "error": "No changes specified."}

    await notion.pages.update(page_id=page["id"], properties=properties)
    return {"success": True, "updated": new_name or old_name}


async def _add_idea(name: str, category: str = None, notes: str = None) -> dict:
    properties: dict = {"Idea": {"title": [{"text": {"content": name}}]}}
    if category:
        properties["Category"] = {"select": {"name": category}}
    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": IDEAS_DS_ID},
        properties=properties,
    )
    return {"success": True, "id": page["id"], "name": name}


async def _get_projects(status: str = "Active") -> dict:
    params: dict = {"data_source_id": PROJECTS_DS_ID}
    if status:
        params["filter"] = {
            "property": "Status",
            "select": {"equals": PROJECT_STATUS_MAP.get(status, status)},
        }
    result = await notion.data_sources.query(**params)

    projects = []
    for page in result.get("results", []):
        props = page["properties"]
        projects.append({
            "name": _title(props, "Name"),
            "status": _select(props, "Status"),
            "description": _rich_text(props, "Description"),
            "check_in_frequency": _select(props, "Check-in Frequency"),
            "next_check_in": _date(props, "Next Check-in"),
            "last_check_in": _date(props, "Last Check-in"),
        })
    return {"projects": projects, "count": len(projects)}


async def _add_project(name: str, description: str = None, check_in_frequency: str = None) -> dict:
    properties: dict = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Status": {"select": {"name": PROJECT_STATUS_MAP["Active"]}},
    }
    if description:
        properties["Description"] = {"rich_text": [{"text": {"content": description}}]}
    if check_in_frequency:
        properties["Check-in Frequency"] = {"select": {"name": check_in_frequency}}

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": PROJECTS_DS_ID},
        properties=properties,
    )
    return {"success": True, "id": page["id"], "name": name}


async def _get_reading_list(status: str = None) -> dict:
    params: dict = {"data_source_id": READING_DS_ID}
    if status:
        params["filter"] = {"property": "Status", "select": {"equals": status}}
    result = await notion.data_sources.query(**params)

    items = []
    for page in result.get("results", []):
        props = page["properties"]
        items.append({
            "title": _title(props, "Title"),
            "type": _select(props, "Type"),
            "status": _select(props, "Status"),
            "url": props.get("URL", {}).get("url"),
            "notes": _rich_text(props, "Notes"),
        })
    return {"items": items, "count": len(items)}


async def _add_reading(title: str, type: str = None, url: str = None, notes: str = None) -> dict:
    properties: dict = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Status": {"select": {"name": "Want to read"}},
    }
    if type:
        properties["Type"] = {"select": {"name": type}}
    if url:
        properties["URL"] = {"url": url}
    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": READING_DS_ID},
        properties=properties,
    )
    return {"success": True, "id": page["id"], "title": title}


def _create_calendar_event(
    summary: str,
    start_datetime: str,
    end_datetime: str = None,
    all_day: bool = False,
    location: str = None,
    description: str = None,
) -> dict:
    service = _get_calendar_service()

    if all_day:
        body = {
            "summary": summary,
            "start": {"date": start_datetime},
            "end": {"date": end_datetime or start_datetime},
        }
    else:
        if not end_datetime:
            dt = datetime.fromisoformat(start_datetime)
            end_datetime = (dt + timedelta(hours=1)).isoformat()
        body = {
            "summary": summary,
            "start": {"dateTime": start_datetime, "timeZone": ET},
            "end": {"dateTime": end_datetime, "timeZone": ET},
        }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    event = service.events().insert(calendarId="primary", body=body).execute()
    return {
        "success": True,
        "id": event.get("id"),  # needed to edit this event later
        "summary": summary,
        "start": start_datetime,
        "link": event.get("htmlLink"),
    }


def _update_calendar_event(
    event_id: str,
    summary: str = None,
    start_datetime: str = None,
    end_datetime: str = None,
    all_day: bool = False,
    location: str = None,
    description: str = None,
) -> dict:
    """Patch an existing event — only the fields provided are changed."""
    service = _get_calendar_service()

    body: dict = {}
    if summary is not None:
        body["summary"] = summary
    if location is not None:
        body["location"] = location
    if description is not None:
        body["description"] = description
    if start_datetime:
        body["start"] = (
            {"date": start_datetime} if all_day
            else {"dateTime": start_datetime, "timeZone": ET}
        )
    if end_datetime:
        body["end"] = (
            {"date": end_datetime} if all_day
            else {"dateTime": end_datetime, "timeZone": ET}
        )

    event = service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
    return {
        "success": True,
        "id": event.get("id"),
        "summary": event.get("summary"),
        "link": event.get("htmlLink"),
    }


# Calendars that are noise for briefings/queries — skip these when reading events.
_SKIP_CAL_SUFFIXES = ("#holiday@group.v.calendar.google.com",
                      "#contacts@group.v.calendar.google.com")


def _event_calendar_ids(service) -> list:
    """All calendars worth reading events from — primary plus shared ones (e.g.
    work calendars shared into the account) — minus holiday/birthday noise.
    Returns [(calendar_id, display_name)]. Falls back to primary on error."""
    try:
        cals = service.calendarList().list().execute().get("items", [])
    except Exception:
        logger.warning("calendarList fetch failed; using primary only")
        return [("primary", "Personal")]
    out = []
    for c in cals:
        cid = c.get("id", "")
        if cid.endswith(_SKIP_CAL_SUFFIXES):
            continue
        out.append((cid, c.get("summaryOverride") or c.get("summary", "")))
    return out or [("primary", "Personal")]


def get_calendar_events_window(hours_back: int = 0, days_ahead: int = 2) -> list:
    """Events from `hours_back` ago to `days_ahead` ahead, across all calendars
    (primary + shared work calendars), with id + attendee emails — used by the
    interview watcher (recent-past events for debriefs, attendees for detection)."""
    service = _get_calendar_service()
    et = pytz.timezone(ET)
    now = datetime.now(et)
    time_min = (now - timedelta(hours=hours_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    out = []
    for cid, cname in _event_calendar_ids(service):
        try:
            result = service.events().list(
                calendarId=cid, timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", maxResults=50,
            ).execute()
        except Exception:
            logger.warning("calendar fetch failed for %s", cid)
            continue
        for e in result.get("items", []):
            start = e["start"].get("dateTime")  # skip all-day (no dateTime)
            if not start:
                continue
            out.append({
                "id": e["id"],
                "summary": e.get("summary", ""),
                "start": start,
                "location": e.get("location", ""),
                "attendees": [a.get("email", "") for a in e.get("attendees", [])],
                "calendar": cname,
            })
    out.sort(key=lambda x: x["start"])
    return out


def _get_calendar_events(days_ahead: int = 7) -> dict:
    service = _get_calendar_service()
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    events = []
    for cid, cname in _event_calendar_ids(service):
        try:
            res = service.events().list(
                calendarId=cid, timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", maxResults=20,
            ).execute()
        except Exception:
            logger.warning("calendar fetch failed for %s", cid)
            continue
        for event in res.get("items", []):
            start = event["start"].get("dateTime", event["start"].get("date", ""))
            events.append({
                "id": event["id"],  # use this to edit the event via update_calendar_event
                "summary": event.get("summary", "(no title)"),
                "start": start,
                "location": event.get("location", ""),
                "calendar": cname,  # which calendar it came from (e.g. work vs personal)
            })

    events.sort(key=lambda x: x["start"])
    return {"events": events, "count": len(events)}


# --- Habits ---

async def _find_habit(name: str) -> dict | None:
    matches = await _find_by_title(HABITS_DS_ID, "Name", name)
    return matches[0] if matches else None


async def _get_habits() -> dict:
    res = await notion.data_sources.query(
        data_source_id=HABITS_DS_ID,
        filter={"property": "Active", "checkbox": {"equals": True}},
    )
    today = datetime.now(pytz.timezone(ET)).date().isoformat()
    habits = []
    for page in res.get("results", []):
        props = page["properties"]
        last = _date(props, "Last Done")
        habits.append({
            "name": _title(props, "Name"),
            "cadence": _select(props, "Cadence"),
            "streak": props.get("Streak", {}).get("number") or 0,
            "last_done": last,
            "done_today": last == today,
        })
    return {"habits": habits, "count": len(habits)}


async def _log_habit(habit_name: str) -> dict:
    page = await _find_habit(habit_name)
    if not page:
        return {"success": False, "error": f"No habit found matching '{habit_name}'"}

    props = page["properties"]
    name = _title(props, "Name")
    last = _date(props, "Last Done")
    streak = props.get("Streak", {}).get("number") or 0

    today = datetime.now(pytz.timezone(ET)).date()
    today_s = today.isoformat()
    if last == today_s:
        return {"success": True, "habit": name, "already_done_today": True, "streak": streak}

    yesterday_s = (today - timedelta(days=1)).isoformat()
    new_streak = streak + 1 if last == yesterday_s else 1
    await notion.pages.update(
        page_id=page["id"],
        properties={
            "Last Done": {"date": {"start": today_s}},
            "Streak": {"number": new_streak},
        },
    )
    return {"success": True, "habit": name, "streak": new_streak}


async def _add_habit(name: str, cadence: str = "Daily") -> dict:
    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": HABITS_DS_ID},
        properties={
            "Name": {"title": [{"text": {"content": name}}]},
            "Cadence": {"select": {"name": cadence}},
            "Active": {"checkbox": True},
            "Streak": {"number": 0},
        },
    )
    return {"success": True, "id": page["id"], "name": name, "cadence": cadence}


# --- Job Pipeline ---

def _today_et() -> str:
    return datetime.now(pytz.timezone(ET)).date().isoformat()


def _filter_by_role(pages: list, role: str = None) -> list:
    """Narrow company matches by role using the tightest tier that hits, so that
    'Analyst I' doesn't also match 'Analyst II' (substring overlap)."""
    if not role:
        return pages
    r = role.strip().lower()

    def role_of(pg):
        return (_rich_text(pg["properties"], "Role") or "").strip().lower()

    exact = [p for p in pages if role_of(p) == r]
    if exact:
        return exact
    edge = [p for p in pages if role_of(p).endswith(r) or role_of(p).startswith(r)]
    if edge:
        return edge
    return [p for p in pages if r in role_of(p)]


async def _find_applications(company: str, role: str = None) -> list:
    res = await notion.data_sources.query(
        data_source_id=JOB_PIPELINE_DS_ID,
        filter={"property": "Company", "title": {"contains": company}},
    )
    return _filter_by_role(res.get("results", []), role)


def _ambiguous_result(matches: list) -> dict:
    return {
        "success": False,
        "ambiguous": True,
        "error": "Multiple matching applications — ask Sebastian which role.",
        "matches": [
            {"company": _title(p["properties"], "Company"),
             "role": _rich_text(p["properties"], "Role"),
             "status": _select(p["properties"], "Status")}
            for p in matches
        ],
    }


async def _get_pipeline(status: str = None) -> dict:
    params: dict = {"data_source_id": JOB_PIPELINE_DS_ID}
    if status:
        params["filter"] = {"property": "Status", "select": {"equals": status}}
    res = await notion.data_sources.query(**params)

    apps = []
    for page in res.get("results", []):
        p = page["properties"]
        apps.append({
            "company": _title(p, "Company"),
            "role": _rich_text(p, "Role"),
            "status": _select(p, "Status"),
            "stage_detail": _rich_text(p, "Stage detail"),
            "next_action": _rich_text(p, "Next action"),
            "next_action_due": _date(p, "Next action due"),
            "applied_date": _date(p, "Applied date"),
            "last_activity": _date(p, "Last activity"),
            "location": _select(p, "Location"),
        })
    apps.sort(key=lambda a: (PIPELINE_STATUS_ORDER.get(a.get("status") or "", 99), a.get("company") or ""))
    return {"applications": apps, "count": len(apps)}


async def _add_application(
    company: str,
    role: str,
    status: str = "Applied",
    posting_url: str = None,
    source: str = None,
    location: str = None,
    applied_date: str = None,
    thread_id: str = None,
    confidence: str = "Confirmed",
) -> dict:
    today = _today_et()
    props: dict = {
        "Company": {"title": [{"text": {"content": company}}]},
        "Role": {"rich_text": [{"text": {"content": role}}]},
        "Status": {"select": {"name": status}},
        "Confidence": {"select": {"name": confidence}},
        "Applied date": {"date": {"start": applied_date or today}},
        "Last activity": {"date": {"start": today}},
    }
    if posting_url:
        props["Posting URL"] = {"url": posting_url}
    if source:
        props["Source"] = {"select": {"name": source}}
    if location:
        props["Location"] = {"select": {"name": location}}
    if thread_id:
        props["Gmail thread IDs"] = {"rich_text": [{"text": {"content": thread_id}}]}

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": JOB_PIPELINE_DS_ID},
        properties=props,
    )
    return {"success": True, "id": page["id"], "company": company, "role": role, "status": status}


def gmail_message_link(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


async def apply_pipeline_transition(
    page_id: str, from_status: str, to_status: str,
    thread_id: str, existing_threads: list, event: str, trigger: str,
) -> None:
    """Update a record's status (+ thread id, + Last activity) and append a row to
    the Pipeline Events log for funnel analytics."""
    today = _today_et()
    threads = list(existing_threads or [])
    if thread_id and thread_id not in threads:
        threads.append(thread_id)

    await notion.pages.update(page_id=page_id, properties={
        "Status": {"select": {"name": to_status}},
        "Last activity": {"date": {"start": today}},
        "Gmail thread IDs": {"rich_text": [{"text": {"content": ", ".join(threads)[:2000]}}]},
    })
    await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": PIPELINE_EVENTS_DS_ID},
        properties={
            "Event": {"title": [{"text": {"content": f"{from_status or '—'} → {to_status}"}}]},
            "Timestamp": {"date": {"start": today}},
            "From Status": {"rich_text": [{"text": {"content": from_status or ""}}]},
            "To Status": {"rich_text": [{"text": {"content": to_status}}]},
            "Trigger": {"rich_text": [{"text": {"content": (trigger or "")[:2000]}}]},
            "Application": {"relation": [{"id": page_id}]},
        },
    )


async def _update_application(
    company: str,
    role: str = None,
    status: str = None,
    stage_detail: str = None,
    next_action: str = None,
    next_action_due: str = None,
) -> dict:
    matches = await _find_applications(company, role)
    if not matches:
        return {"success": False, "error": f"No application found for '{company}'"}
    if len(matches) > 1:
        return _ambiguous_result(matches)
    page = matches[0]

    props: dict = {"Last activity": {"date": {"start": _today_et()}}}
    if status is not None:
        props["Status"] = {"select": {"name": status}}
    if stage_detail is not None:
        props["Stage detail"] = {"rich_text": [{"text": {"content": stage_detail}}]}
    if next_action is not None:
        props["Next action"] = {"rich_text": [{"text": {"content": next_action}}]}
    if next_action_due is not None:
        props["Next action due"] = {"date": {"start": next_action_due}}

    await notion.pages.update(page_id=page["id"], properties=props)
    p = page["properties"]
    return {
        "success": True,
        "company": _title(p, "Company"),
        "role": _rich_text(p, "Role"),
        "status": status or _select(p, "Status"),
    }


async def _add_application_note(company: str, note: str, role: str = None) -> dict:
    matches = await _find_applications(company, role)
    if not matches:
        return {"success": False, "error": f"No application found for '{company}'"}
    if len(matches) > 1:
        return _ambiguous_result(matches)
    page = matches[0]

    p = page["properties"]
    existing = _rich_text(p, "Stage detail") or ""
    line = f"[{_today_et()}] {note}"
    new_detail = f"{existing}\n{line}" if existing else line
    await notion.pages.update(
        page_id=page["id"],
        properties={
            "Stage detail": {"rich_text": [{"text": {"content": new_detail[:2000]}}]},
            "Last activity": {"date": {"start": _today_et()}},
        },
    )
    return {"success": True, "company": _title(p, "Company"), "note_added": note}


# --- Property helpers ---

def _title(props: dict, key: str) -> str:
    items = props.get(key, {}).get("title", [])
    return items[0].get("plain_text", "") if items else "(untitled)"


def _select(props: dict, key: str) -> str | None:
    sel = props.get(key, {}).get("select")
    return sel["name"] if sel else None


def _rich_text(props: dict, key: str) -> str | None:
    items = props.get(key, {}).get("rich_text", [])
    return "".join(t.get("plain_text", "") for t in items) or None


def _checkbox(props: dict, key: str) -> bool:
    return bool(props.get(key, {}).get("checkbox", False))


def _date(props: dict, key: str) -> str | None:
    d = props.get(key, {}).get("date")
    return d["start"] if d else None


def _normalize_energy(value: str | None) -> str | None:
    """Map '⚡ High' / '🔋 Medium' / '🪫 Low' back to plain High/Medium/Low."""
    if not value:
        return None
    for plain in ("High", "Medium", "Low"):
        if plain in value:
            return plain
    return value
