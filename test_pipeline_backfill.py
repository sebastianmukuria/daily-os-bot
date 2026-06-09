"""
Unit tests for the backfill's pure logic (cluster / replay / decide) on synthetic
events that mirror the known target state. No Gmail/Notion/LLM.

  python3 test_pipeline_backfill.py   # standalone
  pytest test_pipeline_backfill.py    # in CI
"""

from datetime import datetime, timezone

from pipeline_backfill import (
    cluster_events, replay_group, decide_action, norm_company,
)

TODAY = "2026-06-09"


def _ms(date_iso):
    return int(datetime.fromisoformat(date_iso + "T12:00:00+00:00").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _ev(eid, thread, company, role, et, date, conf=0.95):
    return {"id": eid, "thread_id": thread, "ts": _ms(date), "from": f"x@{company}.com",
            "to": [], "subject": f"{et} {company}", "snippet": "",
            "company": company, "role": role, "event_type": et, "confidence": conf}


EVENTS = [
    _ev("1", "A1", "Affirm", "Data Analyst I", "applied", "2026-05-20"),
    _ev("2", "A1b", "Affirm", "Analyst I", "interview_scheduled", "2026-06-04"),   # role variant, new thread
    _ev("3", "A2", "Affirm", "Data Analyst II", "applied", "2026-05-21"),
    _ev("4", "A2", "Affirm", "", "rejection", "2026-06-08"),                        # empty role, same thread
    _ev("5", "S1", "SpaceX", "Starlink Growth BA", "applied", "2026-05-15"),
    _ev("6", "S1", "SpaceX", "Starlink Growth BA", "rejection", "2026-05-28"),
    _ev("7", "S2", "SpaceX", "Starlink Growth Sr BA", "applied", "2026-05-16"),
    _ev("8", "S2", "SpaceX", "Starlink Growth Sr BA", "rejection", "2026-05-29"),
    _ev("9", "N1", "Netflix", "Data Analyst Production Finance", "applied", "2026-06-05"),
]


def _recons():
    groups = cluster_events(EVENTS)
    return groups, [replay_group(g, TODAY) for g in groups]


def test_distinct_roles_stay_split():
    groups, _ = _recons()
    affirm = [g for g in groups if g["company_norm"] == "affirm"]
    spacex = [g for g in groups if g["company_norm"] == "spacex"]
    assert len(affirm) == 2, [g["role"] for g in affirm]   # Analyst I and II, never merged
    assert len(spacex) == 2, [g["role"] for g in spacex]   # BA and Sr BA
    assert len(groups) == 5, len(groups)                   # + Netflix


def test_reconstructed_statuses():
    _, recons = _recons()
    by = {(norm_company(r["company"]), r["role"]): r["status"] for r in recons}
    assert by[("affirm", "Data Analyst I")] == "Interviewing", by
    assert by[("affirm", "Data Analyst II")] == "Rejected", by
    assert by[("spacex", "Starlink Growth BA")] == "Rejected", by
    assert by[("spacex", "Starlink Growth Sr BA")] == "Rejected", by
    assert by[("netflix", "Data Analyst Production Finance")] == "Applied", by


def test_thread_merge_keeps_one_record():
    # The empty-role Affirm II rejection (same thread as its 'applied') must merge,
    # not spawn a 3rd Affirm group.
    groups, _ = _recons()
    affirm_ii = next(g for g in groups if g["role"] == "Data Analyst II")
    assert len(affirm_ii["events"]) == 2 and "A2" in affirm_ii["thread_ids"]


def test_canonical_role_is_longest():
    groups, _ = _recons()
    affirm_i = next(g for g in groups if "Analyst I" in g["role"] and "II" not in g["role"])
    assert affirm_i["role"] == "Data Analyst I"  # not the shorter 'Analyst I' variant


def test_decide_create_update_skip():
    _, recons = _recons()
    affirm_ii = next(r for r in recons if r["role"] == "Data Analyst II")
    # no existing -> create
    assert decide_action(affirm_ii, [])["action"] == "create"
    # existing at earlier stage -> update (Applied -> Rejected)
    existing = [{"id": "p1", "company": "Affirm", "role": "Data Analyst II",
                 "status": "Applied", "thread_ids": []}]
    assert decide_action(affirm_ii, existing)["action"] == "update"
    # existing already Rejected -> skip
    existing2 = [{"id": "p1", "company": "Affirm", "role": "Data Analyst II",
                  "status": "Rejected", "thread_ids": []}]
    assert decide_action(affirm_ii, existing2)["action"] == "skip"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} backfill tests passed")


if __name__ == "__main__":
    _run()
