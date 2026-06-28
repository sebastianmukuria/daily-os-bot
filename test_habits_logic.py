"""Unit tests for cadence-aware habit streaks (pure).

  python3 test_habits_logic.py    # standalone
  pytest test_habits_logic.py     # in CI
"""
from datetime import date
from habits_logic import next_streak

T = date(2026, 6, 25)  # a Thursday


def test_first_ever_log():
    assert next_streak("Daily", None, T, 0) == 1


def test_daily_consecutive():
    assert next_streak("Daily", "2026-06-24", T, 4) == 5


def test_daily_gap_resets():
    assert next_streak("Daily", "2026-06-22", T, 4) == 1   # 3-day gap breaks daily


def test_weekly_maintained():
    assert next_streak("Weekly", "2026-06-18", T, 10) == 11  # 7-day gap is fine


def test_weekly_broken():
    assert next_streak("Weekly", "2026-06-10", T, 10) == 1   # 15-day gap breaks it


def test_weekdays_over_weekend():
    # last done Fri Jun 19, today Mon Jun 22 -> 3-day gap is fine
    assert next_streak("Weekdays", "2026-06-19", date(2026, 6, 22), 3) == 4


def test_mwf_gap():
    # last Mon Jun 22, today Wed Jun 24 -> 2-day gap is fine
    assert next_streak("MWF", "2026-06-22", date(2026, 6, 24), 2) == 3


def test_unknown_cadence_uses_daily_rule():
    assert next_streak(None, "2026-06-22", T, 4) == 1   # 3-day gap, daily fallback


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} habits_logic tests passed")


if __name__ == "__main__":
    _run()
