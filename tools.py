import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from notion_client import AsyncClient as NotionAsyncClient
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger("daily_os_bot.tools")

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# These are Notion *data source* IDs (new 2025-09-03 API). Querying happens on
# data sources, not databases. Page creation uses a data_source_id parent.
TASKS_DS_ID = "b550275a-0137-4cab-9231-7950e838eb34"
IDEAS_DS_ID = "b70516f3-6782-4256-837e-85bb5ce11b62"
PROJECTS_DS_ID = "c99375ae-b7d5-4225-bd45-fe7e204c4e9c"
READING_DS_ID = "29aba34c-4a58-4096-8c7f-f649976e7639"

# Real select option names in the Notion DBs (with emoji prefixes)
ENERGY_MAP = {"High": "⚡ High", "Medium": "🔋 Medium", "Low": "🪫 Low"}
STATUS_NOT_STARTED = "🔴 Not Started"
STATUS_DONE = "🟢 Done"
PROJECT_STATUS_MAP = {
    "Active": "🟢 Active", "Paused": "⏸️ Paused", "Done": "✅ Done", "Idea": "💡 Idea",
}

ET = "America/New_York"

notion = NotionAsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))

TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def _load_calendar_creds() -> Credentials:
    """Load Google creds from the GOOGLE_TOKEN_JSON env var (cloud) or token.json (local).

    On ephemeral hosts (Railway/Render) the filesystem is wiped on redeploy, so the
    token can't live in a file there — set GOOGLE_TOKEN_JSON instead. Locally,
    auth_google.py writes token.json.
    """
    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if env_token:
        return Credentials.from_authorized_user_info(json.loads(env_token), GOOGLE_SCOPES)
    if os.path.exists(TOKEN_PATH):
        return Credentials.from_authorized_user_file(TOKEN_PATH, GOOGLE_SCOPES)
    raise RuntimeError(
        "Google Calendar not authenticated. Run auth_google.py locally, then either keep "
        "token.json or set GOOGLE_TOKEN_JSON in your environment."
    )


def _get_calendar_service():
    creds = _load_calendar_creds()
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token to file only when running file-based (local).
        if not os.environ.get("GOOGLE_TOKEN_JSON"):
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


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
        "description": "Create a new task in Sebastian's Notion Tasks database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name"},
                "energy": {
                    "type": "string",
                    "description": "Energy level required. Default: Medium",
                    "enum": ["High", "Medium", "Low"],
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
]


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
        )
    elif name == "complete_task":
        return await _complete_task(task_name=inputs["task_name"])
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


async def _create_task(name: str, energy: str = "Medium", due_date: str = None) -> dict:
    properties: dict = {
        "Task": {"title": [{"text": {"content": name}}]},
        "Energy": {"select": {"name": ENERGY_MAP.get(energy, ENERGY_MAP["Medium"])}},
        "Status": {"select": {"name": STATUS_NOT_STARTED}},
    }
    if due_date:
        properties["Due Date"] = {"date": {"start": due_date}}

    page = await notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": TASKS_DS_ID},
        properties=properties,
    )
    return {"success": True, "id": page["id"], "name": name, "energy": energy}


async def _complete_task(task_name: str) -> dict:
    result = await notion.data_sources.query(
        data_source_id=TASKS_DS_ID,
        filter={"property": "Task", "title": {"contains": task_name}},
    )
    if not result["results"]:
        return {"success": False, "error": f"No task found matching '{task_name}'"}

    page = result["results"][0]
    found_name = _title(page["properties"], "Task")
    await notion.pages.update(
        page_id=page["id"],
        properties={"Status": {"select": {"name": STATUS_DONE}}},
    )
    return {"success": True, "completed": found_name}


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


def _get_calendar_events(days_ahead: int = 7) -> dict:
    service = _get_calendar_service()
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=days_ahead)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        )
        .execute()
    )

    events = []
    for event in events_result.get("items", []):
        start = event["start"].get("dateTime", event["start"].get("date", ""))
        events.append({
            "id": event["id"],  # use this to edit the event via update_calendar_event
            "summary": event.get("summary", "(no title)"),
            "start": start,
            "location": event.get("location", ""),
        })

    return {"events": events, "count": len(events)}


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
