"""
Weekly job-search funnel report. Queries the dbt marts in Snowflake and posts a
summary to Telegram. Run by .github/workflows/funnel_report.yml (Sundays), after
the daily warehouse refresh. Reuses el_notion.connect() for Snowflake auth.

Needs env: SNOWFLAKE_* (as for el_notion) + TELEGRAM_TOKEN + TELEGRAM_CHAT_ID.
"""

import os
import urllib.parse
import urllib.request
from datetime import date

from el_notion import connect


def _rows(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def build_message(funnel, status, new_apps, moves) -> str:
    today = date.today()
    reached = [str(r) for _, r, _ in funnel]
    lines = [
        f"📊 <b>Job Search Funnel</b> — week of {today:%b %-d}",
        "",
        "  →  ".join(reached) + "   <i>(Applied→Screen→Interview→Final→Offer)</i>",
        "",
        "<b>Reached each stage</b>",
    ]
    for stage, n, pct in funnel:
        p = f"  ({pct}% of applied)" if pct is not None else ""
        lines.append(f"• {stage}: {n}{p}")

    lines += ["", "<b>Current status</b>"]
    lines += [f"• {s}: {n}" for s, n in status]

    lines += ["", f"<b>This week:</b> {new_apps} new application(s), {moves} status change(s)"]
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_TOKEN"]
    data = urllib.parse.urlencode(
        {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML"}
    ).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req) as r:
        print("telegram:", r.status)


def main() -> None:
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("use warehouse " + os.environ["SNOWFLAKE_WAREHOUSE"])
        cur.execute("use database " + os.environ["SNOWFLAKE_DATABASE"])
        funnel = _rows(cur, """
            select stage, applications_reached, pct_of_applied
            from MARTS.MART_FUNNEL order by stage_rank
        """)
        status = _rows(cur, """
            select status, count(*) from MARTS.FCT_APPLICATIONS
            group by status order by count(*) desc
        """)
        new_apps = _rows(cur, """
            select count(*) from MARTS.FCT_APPLICATIONS
            where applied_date >= dateadd(day, -7, current_date())
        """)[0][0]
        moves = _rows(cur, """
            select count(*) from MARTS.FCT_PIPELINE_EVENTS
            where event_at >= dateadd(day, -7, current_date())
        """)[0][0]
    finally:
        cur.close()
        conn.close()

    send_telegram(build_message(funnel, status, new_apps, moves))


if __name__ == "__main__":
    main()
