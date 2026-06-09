"""
Fixtures eval for the pipeline classifier.

- `python3 test_classifier.py`        → run the (free, deterministic) prefilter eval
- `RUN_LLM=1 python3 test_classifier.py` → also run the LLM classifier on job emails
- `pytest test_classifier.py`         → run test_prefilter in CI

Per architecture decision D5, the prefilter eval should run in CI when the
classifier or its prompts change.
"""

import os
import json

from pipeline_classifier import prefilter, classify_email

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "job_emails.json")
with open(FIXTURES_PATH) as f:
    FIXTURES = json.load(f)


def test_prefilter():
    """Every fixture's job-related verdict must match the expected label."""
    mismatches = []
    for fx in FIXTURES:
        got = prefilter(fx["email"])["job_related"]
        want = fx["expect"]["job_related"]
        if got != want:
            mismatches.append((fx["email"]["subject"], "got", got, "want", want))
    assert not mismatches, f"prefilter mismatches: {mismatches}"


def _run_prefilter_eval():
    ok = 0
    for fx in FIXTURES:
        got = prefilter(fx["email"])
        want = fx["expect"]["job_related"]
        mark = "✓" if got["job_related"] == want else "✗"
        if got["job_related"] == want:
            ok += 1
        print(f"  {mark} [{str(got['job_related']):5}] {fx['email']['subject'][:48]:48}  ({got['reason']})")
    print(f"\nprefilter: {ok}/{len(FIXTURES)} correct")
    return ok == len(FIXTURES)


def _run_llm_eval():
    from dotenv import load_dotenv
    load_dotenv()
    import anthropic
    client = anthropic.Anthropic()
    print("\nLLM classification (job-related fixtures only):")
    for fx in FIXTURES:
        if not fx["expect"]["job_related"]:
            continue
        out = classify_email(fx["email"], client=client)
        want_ev = fx["expect"].get("event_type")
        mark = "✓" if out["event_type"] == want_ev else "≈"
        print(f"  {mark} {fx['email']['subject'][:40]:40} -> {out['company']!r}/{out['role']!r} "
              f"{out['event_type']} (conf {out['confidence']})  [want {want_ev}]")


if __name__ == "__main__":
    print("=== Prefilter eval ===")
    passed = _run_prefilter_eval()
    if os.environ.get("RUN_LLM"):
        _run_llm_eval()
    raise SystemExit(0 if passed else 1)
