"""
Unit tests for plan_email (pure decision logic over classified emails).

  python3 test_pipeline_ingest.py   # standalone
  pytest test_pipeline_ingest.py    # in CI
"""

from pipeline_ingest import plan_email


def test_create_new_application():
    p = plan_email({"thread_id": "tX"},
                   {"company": "NewCo", "role": "Analyst", "event_type": "applied", "confidence": 0.95},
                   [])
    assert p["action"] == "create", p


def test_update_existing_on_rejection():
    records = [{"id": "1", "company": "Affirm", "role": "Data Analyst I",
                "status": "Recruiter Screen", "thread_ids": []}]
    p = plan_email({"thread_id": "t1"},
                   {"company": "Affirm", "role": "Data Analyst I", "event_type": "rejection", "confidence": 0.95},
                   records)
    assert p["action"] == "update" and p["to_status"] == "Rejected", p


def test_thread_match_no_forward_is_skip():
    records = [{"id": "1", "company": "Affirm", "role": "DA",
                "status": "Final Round", "thread_ids": ["t1"]}]
    p = plan_email({"thread_id": "t1"},
                   {"company": "Affirm", "role": "DA", "event_type": "screen_invite", "confidence": 0.95},
                   records)
    assert p["action"] == "skip", p  # can't move Final Round back to Recruiter Screen


def test_low_confidence_confirms():
    p = plan_email({"thread_id": None},
                   {"company": "X", "role": "Y", "event_type": "offer", "confidence": 0.4},
                   [])
    assert p["action"] == "confirm", p


def test_no_match_non_applied_event_confirms():
    p = plan_email({"thread_id": None},
                   {"company": "X", "role": "Y", "event_type": "interview_scheduled", "confidence": 0.95},
                   [])
    assert p["action"] == "confirm", p  # high conf, no record, not 'applied' -> ask


def test_other_event_skips():
    p = plan_email({"thread_id": None},
                   {"company": "", "role": "", "event_type": "other", "confidence": 0.85},
                   [])
    assert p["action"] == "skip", p


def test_ambiguous_confirms():
    records = [
        {"id": "1", "company": "Affirm", "role": "Data Analyst I", "status": "Applied", "thread_ids": []},
        {"id": "2", "company": "Affirm", "role": "Data Analyst II", "status": "Applied", "thread_ids": []},
    ]
    p = plan_email({"thread_id": None},
                   {"company": "Affirm", "role": "", "event_type": "rejection", "confidence": 0.95},
                   records)
    assert p["action"] == "confirm" and len(p["candidates"]) == 2, p


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} ingest-planner tests passed")


if __name__ == "__main__":
    _run()
