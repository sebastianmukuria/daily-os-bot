"""
Pure formatters for the daily briefings (morning / midday / EOD). They take
already-fetched data and return Telegram HTML strings — no I/O, so they're
unit-tested. The bot's JobQueue jobs fetch the data and send the result.

Note: these show your real (already-emoji-polished) task titles; there's no LLM
rephrasing step like the old cowork prompt had.
"""

import html
from datetime import date

ENERGY_EMOJI = {"High": "⚡", "Medium": "🔋", "Low": "🪫"}
_ENERGY_RANK = {"High": 0, "Medium": 1, "Low": 2}
MAX_BRIEFING_TASKS = 8  # cap the morning list; note the remainder


def _esc(s: str) -> str:
    return html.escape(s or "")


def _cal_label(calname: str) -> str:
    """Short work-calendar tag from a shared calendar's name. Shared work
    calendars surface as their email (sebastian@blockworks.co -> 'Blockworks');
    personal calendars (Personal/Family) get no tag."""
    c = calname or ""
    if "@" in c:
        return c.split("@", 1)[1].split(".", 1)[0].capitalize()
    return ""


def _fmt_event(e: dict) -> str:
    label = _cal_label(e.get("calendar", ""))
    tag = f"[{label}] " if label else ""
    loc = f" ({_esc(e['location'])})" if e.get("location") else ""
    return f"• {e.get('time_str','')} — {tag}{_esc(e.get('summary',''))}{loc}"


def _due_today_or_past(task: dict, today_iso: str) -> bool:
    d = task.get("due_date")
    return bool(d) and d <= today_iso


def _energy_key(task: dict) -> int:
    return _ENERGY_RANK.get(task.get("energy"), 3)


def _task_line(task: dict, today_iso: str) -> str:
    flag = " ⚠️ due today" if _due_today_or_past(task, today_iso) else ""
    return f"• {_esc(task.get('name',''))}{flag}"


def format_morning(tasks: list, events: list, today: date) -> str:
    today_iso = today.isoformat()
    lines = [f"☀️ <b>Morning Briefing — {today.strftime('%A, %B %-d')}</b>", ""]

    lines.append("📅 <b>Today's Calendar</b>")
    lines += [_fmt_event(e) for e in events] if events else ["Nothing scheduled."]
    lines.append("")

    checkins = [t for t in tasks if t.get("type") == "Project Check-in"]
    work = [t for t in tasks if t.get("type") != "Project Check-in"]
    ordered = sorted(work, key=lambda t: (_energy_key(t), 0 if _due_today_or_past(t, today_iso) else 1))
    visible = ordered[:MAX_BRIEFING_TASKS]
    hidden = len(ordered) - len(visible)

    # High Energy — always shown (clear-runway empty state)
    high = [t for t in visible if t.get("energy") == "High"]
    lines.append("⚡ <b>High Energy — do these first</b>")
    lines += [_task_line(t, today_iso) for t in high] if high else ["Nothing high-energy — clear runway."]

    for key, header in (("Medium", "🔋 <b>Medium Energy</b>"), ("Low", "🪫 <b>Low Energy / Admin</b>")):
        grp = [t for t in visible if t.get("energy") == key]
        if grp:
            lines += ["", header] + [_task_line(t, today_iso) for t in grp]

    other = [t for t in visible if t.get("energy") not in ("High", "Medium", "Low")]
    if other:
        lines += ["", "📌 <b>Unsorted</b>"] + [_task_line(t, today_iso) for t in other]

    if checkins:
        lines += ["", "🔁 <b>Project Check-ins (15 min each)</b>"] + [f"• {_esc(t['name'])}" for t in checkins]

    stale = [t for t in tasks if t.get("stale")]
    if stale:
        lines += ["", "🔴 <b>Stale — been waiting too long</b>"]
        lines += [f"• {_esc(t['name'])} ({t.get('rolling_days', 0)} days)" for t in stale]

    if hidden > 0:
        lines += ["", f"<i>+{hidden} more not shown — check Notion</i>"]
    lines += ["", f"<i>{len(tasks)} tasks total — {today.strftime('%b %-d')}</i>"]
    return "\n".join(lines)


def format_midday(open_tasks: list, done_count: int, afternoon_events: list, today: date) -> str:
    today_iso = today.isoformat()
    if not open_tasks:
        return "🕐 <b>Midday Check</b>\n\nAll clear — nothing left today 🎉"

    lines = ["🕐 <b>Midday Check</b>", "", f"{done_count} ✅ done  |  {len(open_tasks)} remaining", ""]
    lines.append("📅 <b>This afternoon</b>")
    lines += [_fmt_event(e) for e in afternoon_events] if afternoon_events else ["Nothing scheduled."]
    lines += ["", "📋 <b>Still open</b>"]
    top = sorted(open_tasks, key=_energy_key)[:5]
    for t in top:
        em = ENERGY_EMOJI.get(t.get("energy"), "•")
        lines.append(f"{em} {_esc(t.get('name',''))}")

    due = [t for t in open_tasks if _due_today_or_past(t, today_iso)]
    if due:
        lines += [""] + [f"⚠️ Due today: {_esc(t['name'])}" for t in due]
    return "\n".join(lines)


def format_eod(done_today: list, rolling: list, tomorrow_events: list, today: date) -> str:
    today_iso = today.isoformat()
    lines = ["🌙 <b>End of Day</b>", "", "✅ <b>Done today</b>"]
    lines += [f"• {_esc(n)}" for n in done_today] if done_today else \
        ["Nothing marked done today — update Notion if you got things done."]

    lines += ["", "➡️ <b>Rolling to tomorrow</b>"]
    lines += [f"• {_esc(t['name'])}" for t in rolling] if rolling else ["Nothing open 🎉"]

    lines += ["", "📅 <b>Tomorrow's calendar</b>"]
    lines += [_fmt_event(e) for e in tomorrow_events] if tomorrow_events else ["Nothing scheduled yet."]

    past_due = [t for t in rolling if _due_today_or_past(t, today_iso)]
    if past_due:
        lines += [""] + [f"⚠️ Past due: {_esc(t['name'])}" for t in past_due]

    stale = [t for t in rolling if t.get("stale")]
    if stale:
        lines += ["", f"⚠️ {len(stale)} task(s) stale 3+ days — timebox one tomorrow or drop it."]
    return "\n".join(lines)
