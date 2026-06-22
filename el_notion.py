"""
EL (extract-load): snapshot every Notion database into Snowflake as
semi-structured rows. One row per Notion page in RAW.NOTION_PAGES, with the
page's properties landed as a VARIANT (JSON) column — dbt does all the typing
and modeling downstream (RAW -> staging -> marts).

Full-refresh each run (truncate + load): the marts are current-state, and the
funnel's history comes from the append-only `pipeline_events` log, so no
snapshotting is needed here.

Run locally:  python3 el_notion.py
Needs env:    NOTION_TOKEN, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
              SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE  (+ optional SNOWFLAKE_ROLE)
"""

import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import snowflake.connector
from notion_client import Client

# Notion data sources -> the `source` discriminator column in RAW.NOTION_PAGES.
SOURCES = {
    "tasks": "b550275a-0137-4cab-9231-7950e838eb34",
    "projects": "c99375ae-b7d5-4225-bd45-fe7e204c4e9c",
    "ideas": "b70516f3-6782-4256-837e-85bb5ce11b62",
    "reading": "29aba34c-4a58-4096-8c7f-f649976e7639",
    "habits": "818e9cd3-48a1-413e-9d38-aa74c6b4e480",
    "job_pipeline": "551e0098-cf2f-41eb-8dff-437501636fbe",
    "pipeline_events": "bf426aeb-7a4c-45d5-83ac-7155e28cca79",
}

# Full-refresh each run. properties lands as raw JSON text; dbt staging
# parse_json()s it into a VARIANT. (Loading text lets executemany batch — the
# connector can't batch an INSERT ... SELECT parse_json().)
DDL = [
    "create schema if not exists RAW",
    """create or replace table RAW.NOTION_PAGES (
        source            string,
        page_id           string,
        created_time      string,
        last_edited_time  string,
        properties        string,
        loaded_at         timestamp_tz default current_timestamp()
    )""",
]

INSERT = """insert into RAW.NOTION_PAGES (source, page_id, created_time, last_edited_time, properties)
            values (%s, %s, %s, %s, %s)"""


def fetch_pages(notion: Client, ds_id: str) -> list:
    """Paginate a Notion data source and return all page objects."""
    pages, cursor = [], None
    while True:
        kwargs = {"data_source_id": ds_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.data_sources.query(**kwargs)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages


def connect():
    # Prefer key-pair auth (works around MFA-on-password and is CI-friendly);
    # fall back to password if no key path is set.
    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE"),
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
    )
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        kwargs["private_key_file"] = key_path
    else:
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def main() -> None:
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    conn = connect()
    cur = conn.cursor()
    try:
        wh = os.environ["SNOWFLAKE_WAREHOUSE"]
        db = os.environ["SNOWFLAKE_DATABASE"]
        cur.execute(
            f"create warehouse if not exists {wh} warehouse_size = xsmall "
            "auto_suspend = 60 auto_resume = true initially_suspended = true"
        )
        cur.execute(f"use warehouse {wh}")
        cur.execute(f"create database if not exists {db}")
        cur.execute(f"use database {db}")
        for stmt in DDL:
            cur.execute(stmt)

        total = 0
        for source, ds_id in SOURCES.items():
            pages = fetch_pages(notion, ds_id)
            rows = [
                (
                    source,
                    p["id"],
                    p.get("created_time"),
                    p.get("last_edited_time"),
                    json.dumps(p.get("properties", {})),
                )
                for p in pages
            ]
            if rows:
                cur.executemany(INSERT, rows)
            total += len(rows)
            print(f"  loaded {len(rows):4d}  {source}")
        conn.commit()
        print(f"done: {total} pages -> RAW.NOTION_PAGES")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
