"""
Unit tests for the inbox→calendar pure helpers (prefilter + time assembly).
The LLM extractor (extract_event) is exercised live, not here.

  python3 test_inbox_events.py   # standalone
  pytest test_inbox_events.py    # in CI
"""

from inbox_events import looks_like_event, build_event_times


def test_prefilter_positive():
    assert looks_like_event({"subject": "Your flight confirmation", "snippet": "Booking ref ABC123"})
    assert looks_like_event({"subject": "Reservation confirmed", "snippet": "table for 2 at 7pm"})
    assert looks_like_event({"subject": "Hotel booking", "snippet": "check-in Jul 4"})


def test_prefilter_negative():
    assert not looks_like_event({"subject": "50% off summer sale", "snippet": "shop now"})
    assert not looks_like_event({"subject": "Your weekly newsletter", "snippet": "top stories"})


def test_build_times_timed_default_end():
    s, e, ad = build_event_times("2026-07-01", "19:30", "", False)
    assert s == "2026-07-01T19:30:00" and e == "2026-07-01T20:30:00" and ad is False


def test_build_times_explicit_end():
    s, e, ad = build_event_times("2026-07-01", "19:30", "22:00", False)
    assert s == "2026-07-01T19:30:00" and e == "2026-07-01T22:00:00" and ad is False


def test_build_times_all_day_next_day_end():
    s, e, ad = build_event_times("2026-07-01", "", "", True)
    assert s == "2026-07-01" and e == "2026-07-02" and ad is True  # Google end is exclusive


def test_build_times_no_start_is_all_day():
    s, e, ad = build_event_times("2026-07-01", "", "", False)
    assert ad is True and e == "2026-07-02"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} inbox-event tests passed")


if __name__ == "__main__":
    _run()
