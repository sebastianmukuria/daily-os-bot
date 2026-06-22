"""
Unit tests for the briefing formatters (pure — no I/O).

  python3 test_briefings.py    # standalone
  pytest test_briefings.py     # in CI
"""

from datetime import date
from briefings import format_morning, format_midday, format_eod

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


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} briefing tests passed")


if __name__ == "__main__":
    _run()
