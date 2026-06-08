"""
One-time seeder for the Daily OS Notion databases.

Reads seed_data.yaml (your personal data — gitignored) and creates the projects,
tasks, and ideas it describes. Projects are created first so tasks can link to
them via the Project relation.

Safe to re-run: any item whose title already exists is skipped, so a partial run
can be resumed without creating duplicates.

Usage:
    python3 seed.py
"""

import os
import asyncio

import yaml
from dotenv import load_dotenv

load_dotenv()  # must run before importing tools (it builds the Notion client)

from tools import (  # noqa: E402  -- imported after load_dotenv on purpose
    notion,
    TASKS_DS_ID,
    PROJECTS_DS_ID,
    IDEAS_DS_ID,
    ENERGY_MAP,
    STATUS_NOT_STARTED,
    PROJECT_STATUS_MAP,
)

DATA_PATH = os.path.join(os.path.dirname(__file__), "seed_data.yaml")


async def _existing_by_title(data_source_id: str, title_prop: str) -> dict:
    """Return {title: page_id} for every existing row in a data source."""
    found: dict[str, str] = {}
    cursor = None
    while True:
        params: dict = {"data_source_id": data_source_id, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        res = await notion.data_sources.query(**params)
        for page in res.get("results", []):
            items = page["properties"].get(title_prop, {}).get("title", [])
            if items:
                found[items[0].get("plain_text", "")] = page["id"]
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return found


async def seed_projects(projects: list) -> dict:
    """Create projects, returning {name: page_id} for all (existing + created)."""
    ids = await _existing_by_title(PROJECTS_DS_ID, "Name")
    for proj in projects:
        name = proj["name"]
        if name in ids:
            print(f"  = project exists, skipping: {name}")
            continue
        props = {
            "Name": {"title": [{"text": {"content": name}}]},
            "Status": {"select": {"name": PROJECT_STATUS_MAP[proj["status"]]}},
        }
        if proj.get("check_in"):
            props["Check-in Frequency"] = {"select": {"name": proj["check_in"]}}
        if proj.get("description"):
            props["Description"] = {"rich_text": [{"text": {"content": proj["description"]}}]}
        page = await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": PROJECTS_DS_ID},
            properties=props,
        )
        ids[name] = page["id"]
        print(f"  + project: {name}")
    return ids


async def seed_tasks(tasks: list, project_ids: dict) -> None:
    existing = await _existing_by_title(TASKS_DS_ID, "Task")
    for task in tasks:
        name = task["name"]
        if name in existing:
            print(f"  = task exists, skipping: {name}")
            continue
        props = {
            "Task": {"title": [{"text": {"content": name}}]},
            "Status": {"select": {"name": STATUS_NOT_STARTED}},
            "Type": {"select": {"name": "Task"}},
        }
        if task.get("energy"):
            props["Energy"] = {"select": {"name": ENERGY_MAP[task["energy"]]}}
        if task.get("due"):
            props["Due Date"] = {"date": {"start": str(task["due"])}}
        project = task.get("project")
        if project:
            pid = project_ids.get(project)
            if pid:
                props["Project"] = {"relation": [{"id": pid}]}
            else:
                print(f"    ! no project match for task '{name}': {project}")
        await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": TASKS_DS_ID},
            properties=props,
        )
        print(f"  + task: {name}")


async def seed_ideas(ideas: list) -> None:
    existing = await _existing_by_title(IDEAS_DS_ID, "Idea")
    for idea in ideas:
        name = idea["name"]
        if name in existing:
            print(f"  = idea exists, skipping: {name}")
            continue
        props = {"Idea": {"title": [{"text": {"content": name}}]}}
        if idea.get("category"):
            props["Category"] = {"select": {"name": idea["category"]}}
        await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": IDEAS_DS_ID},
            properties=props,
        )
        print(f"  + idea: {name}")


async def main() -> None:
    with open(DATA_PATH) as f:
        data = yaml.safe_load(f)

    print("Seeding projects...")
    project_ids = await seed_projects(data.get("projects", []))
    print("Seeding tasks...")
    await seed_tasks(data.get("tasks", []), project_ids)
    print("Seeding ideas...")
    await seed_ideas(data.get("ideas", []))
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
