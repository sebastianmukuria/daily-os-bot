"""
Unit tests for the interview-watch pure helpers.

  python3 test_pipeline_interviews.py   # standalone
  pytest test_pipeline_interviews.py    # in CI
"""

from datetime import datetime, timedelta, timezone

from pipeline_interviews import is_interview, extract_company, due_reminder, due_debrief

NOW = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)


def test_is_interview_by_title():
    assert is_interview("Interview: Affirm x Sebastian", [])
    assert not is_interview("Lunch with Tom", [])


def test_is_interview_by_attendee_domain():
    assert is_interview("Affirm sync", ["seb@gmail.com", "bot@interviewplanner.com"])
    assert is_interview("Chat", ["x@goodtime.io"])
    assert not is_interview("Chat", ["x@gmail.com"])


def test_extract_company():
    assert extract_company("Interview: Affirm x Sebastian") == "Affirm"
    assert extract_company("Affirm <> Sebastian — Interview") == "Affirm"
    assert extract_company("Google interview with Sebastian") == "Google"


def test_due_reminder():
    assert due_reminder(NOW + timedelta(minutes=45), NOW) is True
    assert due_reminder(NOW + timedelta(minutes=90), NOW) is False   # too far out
    assert due_reminder(NOW - timedelta(minutes=5), NOW) is False    # already started


def test_due_debrief():
    assert due_debrief(NOW - timedelta(minutes=90), NOW) is True     # 90 min after start
    assert due_debrief(NOW - timedelta(minutes=20), NOW) is False    # probably still in it
    assert due_debrief(NOW - timedelta(minutes=300), NOW) is False   # too long ago


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} interview-watch tests passed")


if __name__ == "__main__":
    _run()
