"""Pure habit-streak logic (dependency-free, unit-testable)."""
from datetime import date


def next_streak(cadence, last, today, streak):
    """New streak after logging `today`, given the habit's cadence, its previous
    'Last Done' date (ISO string or None), and current streak.

    The allowed gap depends on cadence so non-daily habits don't reset every time:
    Daily=1, Weekdays<=3 (Mon after Fri), MWF<=2, Weekly<=8 (a day of slack).
    Unknown cadence falls back to the Daily rule.
    """
    if not last:
        return 1
    try:
        last_d = date.fromisoformat(last[:10])
    except ValueError:
        return 1
    gap = (today - last_d).days
    if gap <= 0:
        return streak or 1
    max_gap = {"Daily": 1, "Weekdays": 3, "MWF": 2, "Weekly": 8}.get(cadence, 1)
    return streak + 1 if gap <= max_gap else 1
