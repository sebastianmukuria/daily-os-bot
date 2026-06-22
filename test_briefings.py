"""
Unit tests for the briefing formatters (pure — no I/O).

  python3 test_briefings.py    # standalone
  pytest test_briefings.py     # in CI
"""

from datetime import date
from briefings import format_morning, format_midday, format_eod, format_week_start, format_week_end

TODAY = date(2026, 6, 20)


def test_morning_order_sections_and_labels():
    tasks = [
        {"id": "1", "name": "Low task", "energy": "Low", "type": "Task", "due_date": None, "rolling_days": 1, "stale": False},
        {"id": "2", "name": "High task", "energy": "High", "type": "Task", "due_date": "2026-06-20", "rolling_days": 0, "stale": False},
        {"id": "3", "name": "Check in on X", "energy": None, "type": "Project Check-in", "due_date": None, "rolling_days": 0, "stale": False},
        {"id": "4", "name": "Old task", "energy": "Medium", "type": "Task", "due_date": None, "rolling_days": 5, "stale": True},
    ]
    events = [{"summary": "Standup", "time_str": "10:00am", "sort_key": "10:00", "all_day": False, "location": "", "calendar": "sebastian@blockworks.co"}]
    out = format_morning(tasks, events, TODAY)
    assert "Morning Briefing" in out
    assert "[Blockworks] Standup" in out                      # work-calendar label
    assert "High task ⚠️ due today" in out
    assert "Project Check-ins" in out and "Check in on X" in out
    assert "Old task (5 days)" in out and "Stale" in out
    assert out.index("High Energy") < out.index("Medium Energy")  # energy order


def test_morning_empty_states():
    out = format_morning([], [], TODAY)
    assert "Nothing scheduled." in out and "clear runway" in out


def test_morning_caps_at_8():
    tasks = [{"id": str(i), "name": f"T{i}", "energy": "Low", "type": "Task", "due_date": None, "rolling_days": 0, "stale": False} for i in range(11)]
    out = format_morning(tasks, [], TODAY)
    assert "+3 more not shown" in out


def test_midday_counts_and_due():
    open_tasks = [{"name": "A", "energy": "High", "due_date": "2026-06-20"}, {"name": "B", "energy": "Low", "due_date": None}]
    out = format_midday(open_tasks, 3, [], TODAY)
    assert "3 ✅ done" in out and "2 remaining" in out and "⚠️ Due today: A" in out


def test_midday_all_clear():
    assert "All clear" in format_midday([], 5, [], TODAY)


def test_eod_done_rolling_stale():
    rolling = [{"name": "Y", "energy": "Low", "due_date": "2026-06-19", "stale": True, "rolling_days": 4}]
    out = format_eod(["Shipped X"], rolling, [], TODAY)
    assert "Shipped X" in out and "Rolling to tomorrow" in out
    assert "Past due: Y" in out and "stale 3+ days" in out


def test_eod_empty_states():
    out = format_eod([], [], [], TODAY)
    assert "Nothing marked done today" in out and "Nothing open" in out and "Nothing scheduled yet" in out


def test_morning_habits_and_stale_cap():
    tasks = [{"id": str(i), "name": f"S{i}", "energy": None, "type": "Task", "due_date": None, "rolling_days": 5, "stale": True} for i in range(7)]
    habits = [
        {"name": "Gym", "due_today": True, "streak": 4, "done_today": False},
        {"name": "Vitamins", "due_today": False, "streak": 2, "done_today": True},
    ]
    out = format_morning(tasks, [], TODAY, habits)
    assert "Habits due today" in out and "Gym" in out and "🔥 4" in out
    assert "Vitamins" not in out                 # not due today
    assert "+2 more stale" in out                # 7 stale, capped at 5


def test_eod_rolling_cap_and_habits():
    rolling = [{"name": f"R{i}", "energy": "Low", "due_date": None, "stale": False} for i in range(15)]
    habits = [{"name": "Walk", "due_today": True}]
    out = format_eod([], rolling, [], TODAY, habits)
    assert "+3 more" in out                       # 15 rolling, capped at 12
    assert "Habits still open" in out and "Walk" in out


def test_week_start():
    cal = {"days": [
        {"date": "2026-06-22", "label": "Mon Jun 22", "events": [
            {"summary": "Standup", "time_str": "10:00am", "sort_key": "10:00", "all_day": False, "location": "", "calendar": "sebastian@blockworks.co"}]},
        {"date": "2026-06-23", "label": "Tue Jun 23", "events": []},
    ], "total": 1}
    due = [{"name": "Submit report", "due_date": "2026-06-25"}]        # Thu
    jobs = [{"company": "Helio", "role": "Data Analyst", "status": "Recruiter Screen", "next_action_due": "2026-06-24"}]  # Wed
    checkins = [{"name": "Side project", "next_check_in": "2026-06-26"}]  # Fri
    out = format_week_start(cal, due, jobs, checkins, "Jun 22 – 28")
    assert "Week Ahead — Jun 22 – 28" in out
    assert "Mon Jun 22" in out and "[Blockworks] Standup" in out
    assert "Tue Jun 23" not in out                       # empty day skipped
    assert "Wed · Helio" in out and "Interviews & job actions" in out
    assert "Thu · Submit report" in out
    assert "Fri · Side project" in out
    assert "Heads up:" in out


def test_week_start_empty():
    cal = {"days": [{"date": "2026-06-22", "label": "Mon Jun 22", "events": []}], "total": 0}
    out = format_week_start(cal, [], [], [], "Jun 22 – 28")
    assert "Nothing scheduled yet." in out


def test_week_end():
    next_cal = {"days": [{"date": "2026-06-27", "label": "Sat Jun 27", "events": [
        {"summary": "Brunch", "time_str": "11:00am", "sort_key": "11:00", "all_day": False, "location": "", "calendar": "Personal"}]}], "total": 1}
    done = [f"Task {i}" for i in range(10)]
    events = [{"to_status": "Rejected"}, {"to_status": "Rejected"}, {"to_status": "Recruiter Screen"}]
    overdue = [{"name": "Late thing", "due_date": "2026-06-20"}]
    upcoming = [{"company": "Cobalt", "status": "Interviewing", "next_action_due": "2026-06-30"}]
    out = format_week_end(done, events, next_cal, overdue, 3, upcoming, "Jun 22 – 26")
    assert "Week in Review — Jun 22 – 26" in out
    assert "10 task(s) done" in out and "+2 more" in out
    assert "3 update(s): 2 Rejected, 1 Recruiter Screen" in out
    assert "Loose ends" in out and "Late thing" in out and "3 stale" in out
    assert "Heading into next week" in out and "Sat Jun 27" in out and "Brunch" in out and "Cobalt" in out


def test_week_end_quiet():
    out = format_week_end([], [], {"days": [], "total": 0}, [], 0, [], "Jun 22 – 26")
    assert "Nothing marked done" in out and "No pipeline movement" in out
    assert "Heading into next week" not in out


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} briefing tests passed")


if __name__ == "__main__":
    _run()
