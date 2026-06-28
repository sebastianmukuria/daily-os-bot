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
MAX_BRIEFING_TASKS = 8   # cap the morning energy list; note the remainder
MAX_STALE_SHOWN = 5      # cap the stale callout so it doesn't become a wall
MAX_ROLLING_SHOWN = 12   # cap the EOD rolling-to-tomorrow list


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


def format_morning(tasks: list, events: list, today: date, habits: list = None) -> str:
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

    # High-priority callout — surfaced up top regardless of energy, so important
    # things don't get buried in the Low-energy section.
    high_priority = [t for t in ordered if t.get("priority") == "High"]
    if high_priority:
        lines.append("‼️ <b>High Priority — do regardless of energy</b>")
        lines += [_task_line(t, today_iso) for t in high_priority[:MAX_BRIEFING_TASKS]]
        lines.append("")

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

    due_habits = [h for h in (habits or []) if h.get("due_today")]
    if due_habits:
        lines += ["", "💪 <b>Habits due today</b>"]
        lines += [
            f"• {_esc(h['name'])}" + (f" (🔥 {h['streak']})" if h.get("streak") else "")
            for h in due_habits
        ]

    stale = [t for t in tasks if t.get("stale")]
    if stale:
        lines += ["", "🔴 <b>Stale — been waiting too long</b>"]
        lines += [f"• {_esc(t['name'])} ({t.get('rolling_days', 0)} days)" for t in stale[:MAX_STALE_SHOWN]]
        if len(stale) > MAX_STALE_SHOWN:
            lines.append(f"<i>+{len(stale) - MAX_STALE_SHOWN} more stale</i>")

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


def format_eod(done_today: list, rolling: list, tomorrow_events: list, today: date, habits: list = None) -> str:
    today_iso = today.isoformat()
    lines = ["🌙 <b>End of Day</b>", "", "✅ <b>Done today</b>"]
    lines += [f"• {_esc(n)}" for n in done_today] if done_today else \
        ["Nothing marked done today — update Notion if you got things done."]

    lines += ["", "➡️ <b>Rolling to tomorrow</b>"]
    if rolling:
        lines += [f"• {_esc(t['name'])}" for t in rolling[:MAX_ROLLING_SHOWN]]
        if len(rolling) > MAX_ROLLING_SHOWN:
            lines.append(f"<i>+{len(rolling) - MAX_ROLLING_SHOWN} more</i>")
    else:
        lines.append("Nothing open 🎉")

    missed_habits = [h for h in (habits or []) if h.get("due_today")]
    if missed_habits:
        lines += ["", "💪 <b>Habits still open</b>"]
        lines += [f"• {_esc(h['name'])}" for h in missed_habits]

    lines += ["", "📅 <b>Tomorrow's calendar</b>"]
    lines += [_fmt_event(e) for e in tomorrow_events] if tomorrow_events else ["Nothing scheduled yet."]

    past_due = [t for t in rolling if _due_today_or_past(t, today_iso)]
    if past_due:
        lines += [""] + [f"⚠️ Past due: {_esc(t['name'])}" for t in past_due]

    stale = [t for t in rolling if t.get("stale")]
    if stale:
        lines += ["", f"⚠️ {len(stale)} task(s) stale 3+ days — timebox one tomorrow or drop it."]

    # Interactive close — the 9pm wrap is the one habit-logging + journaling moment.
    lines += ["", "📝 <b>Before bed</b>"]
    if missed_habits:
        lines.append("Reply with the habits you did (or “all”), plus a line on how today "
                     "went — I’ll update your streaks and save it to your journal.")
    else:
        lines.append("Reply with a line on how today went and I’ll save it to your journal.")
    return "\n".join(lines)


def _weekday_prefix(date_iso: str) -> str:
    """'Mon · ' from an ISO date, or '' if missing/unparseable."""
    if not date_iso:
        return ""
    try:
        return date.fromisoformat(date_iso[:10]).strftime("%a") + " · "
    except ValueError:
        return ""


def _calendar_block(days: list) -> list:
    """Day-grouped event lines (only days that have events)."""
    out = []
    for d in days:
        if not d["events"]:
            continue
        out.append(f"<b>{d['label']}</b>")
        out += [_fmt_event(e) for e in d["events"]]
    return out


def format_week_start(cal: dict, due_tasks: list, job_actions: list, checkins: list, range_label: str) -> str:
    lines = [f"🗓 <b>Week Ahead — {range_label}</b>", "", "📅 <b>Your week</b>"]
    cal_lines = _calendar_block(cal["days"])
    lines += cal_lines if cal_lines else ["Nothing scheduled yet."]

    if job_actions:
        lines += ["", "🎯 <b>Interviews & job actions</b>"]
        for j in job_actions:
            role = f" — {_esc(j['role'])}" if j.get("role") else ""
            lines.append(f"• {_weekday_prefix(j.get('next_action_due'))}{_esc(j.get('company', ''))}{role} ({_esc(j.get('status', ''))})")

    if due_tasks:
        lines += ["", "⏰ <b>Due this week</b>"]
        lines += [f"• {_weekday_prefix(t.get('due_date'))}{_esc(t['name'])}" for t in due_tasks]

    if checkins:
        lines += ["", "🔁 <b>Project check-ins</b>"]
        lines += [f"• {_weekday_prefix(c.get('next_check_in'))}{_esc(c['name'])}" for c in checkins]

    busiest = max(cal["days"], key=lambda d: len(d["events"]), default=None)
    extras = []
    if cal["total"]:
        extras.append(f"{cal['total']} events")
    if busiest and len(busiest["events"]) >= 3:
        extras.append(f"busiest {busiest['label']} ({len(busiest['events'])})")
    if due_tasks:
        extras.append(f"{len(due_tasks)} due")
    if extras:
        lines += ["", f"<i>Heads up: {' · '.join(extras)}</i>"]
    return "\n".join(lines)


def format_week_end(done: list, pipeline_events: list, next_cal: dict, overdue: list,
                    stale_count: int, upcoming_actions: list, range_label: str) -> str:
    lines = [f"🌅 <b>Week in Review — {range_label}</b>", "", "✅ <b>Wins this week</b>"]
    if done:
        lines.append(f"{len(done)} task(s) done:")
        lines += [f"• {_esc(n)}" for n in done[:8]]
        if len(done) > 8:
            lines.append(f"<i>+{len(done) - 8} more</i>")
    else:
        lines.append("Nothing marked done — log what you finished in Notion.")

    lines += ["", "📊 <b>Pipeline this week</b>"]
    if pipeline_events:
        counts: dict = {}
        for e in pipeline_events:
            s = e.get("to_status")
            if s:
                counts[s] = counts.get(s, 0) + 1
        summ = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda x: -x[1]))
        lines.append(f"{len(pipeline_events)} update(s)" + (f": {summ}" if summ else ""))
    else:
        lines.append("No pipeline movement this week.")

    ends = [f"• Overdue: {_esc(t['name'])} (due {t.get('due_date', '')})" for t in overdue[:5]]
    if stale_count:
        ends.append(f"• {stale_count} stale task(s) waiting")
    if ends:
        lines += ["", "⚠️ <b>Loose ends</b>"] + ends

    nxt = _calendar_block(next_cal["days"])
    for j in upcoming_actions:
        nxt.append(f"🎯 {_weekday_prefix(j.get('next_action_due'))}{_esc(j.get('company', ''))} ({_esc(j.get('status', ''))})")
    if nxt:
        lines += ["", "🔜 <b>Heading into next week</b>"] + nxt

    lines += ["", "<i>Have a good weekend.</i>"]
    return "\n".join(lines)


def format_job_failure(label: str, err: str) -> str:
    """Short alert when a scheduled job fails, so failures aren't silent."""
    return (
        f"⚠️ <b>{_esc(label)} didn't send</b>\n"
        f"{_esc(err)}\n"
        f"<i>Your data is safe — this was a fetch/send hiccup. I'll retry next cycle.</i>"
    )
