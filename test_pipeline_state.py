"""
Unit tests for the pipeline state machine + matching (pure logic, no I/O).

  python3 test_pipeline_state.py    # standalone
  pytest test_pipeline_state.py     # in CI
"""

from pipeline_state import decide_transition, should_ghost, match_application


def test_rejection_overrides_any_stage():
    for stage in ["Applied", "Interviewing", "Final Round"]:
        d = decide_transition(stage, "rejection", 0.95)
        assert d["action"] == "set" and d["to_status"] == "Rejected", (stage, d)
    # already rejected -> no-op
    assert decide_transition("Rejected", "rejection", 0.95)["action"] == "none"


def test_forward_only():
    # screen invite advances Applied -> Recruiter Screen
    d = decide_transition("Applied", "screen_invite", 0.95)
    assert d["action"] == "set" and d["to_status"] == "Recruiter Screen"
    # but never moves backward: interview_scheduled (Interviewing) on a Final Round stays
    d = decide_transition("Final Round", "interview_scheduled", 0.95)
    assert d["action"] == "none", d


def test_offer_wins():
    assert decide_transition("Interviewing", "offer", 0.95)["to_status"] == "Offer"


def test_low_confidence_confirms():
    d = decide_transition("Applied", "rejection", 0.5)
    assert d["action"] == "confirm", d


def test_terminal_records_confirm_on_progression():
    d = decide_transition("Rejected", "interview_scheduled", 0.95)
    assert d["action"] == "confirm", d


def test_ghosted_record_is_reversible():
    # a new interview on a Ghosted record advances it again
    d = decide_transition("Ghosted", "interview_scheduled", 0.95)
    assert d["action"] == "set" and d["to_status"] == "Interviewing", d


def test_should_ghost():
    assert should_ghost("Applied", "2026-05-01", "2026-06-01") is True   # 31 days
    assert should_ghost("Applied", "2026-05-25", "2026-06-01") is False  # 7 days
    assert should_ghost("Interviewing", "2026-01-01", "2026-06-01") is False  # not Applied
    assert should_ghost("Applied", None, "2026-06-01") is False


def test_match_by_thread():
    records = [{"id": "1", "company": "Affirm", "role": "Data Analyst I", "thread_ids": ["t1"]}]
    m = match_application("Affirm", "Data Analyst I", "t1", records)
    assert m["by"] == "thread" and m["match"]["id"] == "1"


def test_match_per_role_disambiguation():
    records = [
        {"id": "1", "company": "Affirm", "role": "Data Analyst I", "thread_ids": []},
        {"id": "2", "company": "Affirm", "role": "Data Analyst II", "thread_ids": []},
    ]
    # "Analyst I" must NOT also match "Analyst II"
    m = match_application("Affirm", "Analyst I", None, records)
    assert m["by"] == "company_role" and m["match"]["id"] == "1", m
    # no role given -> ambiguous, return both
    m2 = match_application("Affirm", None, None, records)
    assert m2["by"] == "ambiguous" and len(m2["candidates"]) == 2, m2


def test_match_none():
    assert match_application("Unknown Co", "Role", None, [])["match"] is None


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} state-machine tests passed")


if __name__ == "__main__":
    _run()
