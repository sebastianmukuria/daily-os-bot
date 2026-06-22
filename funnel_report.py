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


def build_caption(status, new_apps, moves, today) -> str:
    """Short caption to accompany the funnel chart (the chart shows the funnel)."""
    lines = [
        f"📊 <b>Job Search Funnel</b> — week of {today:%b %-d}",
        "",
        f"This week: {new_apps} new application(s), {moves} status change(s)",
        "",
        "<b>Current status</b>",
    ]
    lines += [f"• {s}: {n}" for s, n in status]
    return "\n".join(lines)


def make_funnel_chart(funnel, today) -> bytes:
    """Render the funnel as a dark-mode horizontal bar chart; return PNG bytes."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stages = [r[0] for r in funnel]
    reached = [r[1] for r in funnel]
    bg = "#0b0b0f"
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    bars = ax.barh(stages, reached, color="#3b82f6")
    ax.invert_yaxis()  # Applied on top
    span = max(reached) if reached else 1
    for b, v in zip(bars, reached):
        ax.text(b.get_width() + span * 0.012, b.get_y() + b.get_height() / 2,
                str(v), va="center", color="#e5e7eb", fontsize=11)
    ax.set_title(f"Job Search Funnel — week of {today:%b %-d, %Y}",
                 color="#f3f4f6", fontsize=13, pad=12)
    ax.set_xlabel("Applications reached", color="#9ca3af")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors="#9ca3af")
    ax.margins(x=0.14)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=bg)
    plt.close(fig)
    return buf.getvalue()


def send_telegram_photo(png: bytes, caption: str) -> None:
    import requests
    token = os.environ["TELEGRAM_TOKEN"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("funnel.png", png, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    print("telegram photo:", resp.status_code)


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

    today = date.today()
    try:
        chart = make_funnel_chart(funnel, today)
        send_telegram_photo(chart, build_caption(status, new_apps, moves, today))
    except Exception as e:  # never let a chart hiccup drop the report
        print("chart send failed, falling back to text:", e)
        send_telegram(build_message(funnel, status, new_apps, moves))


if __name__ == "__main__":
    main()
