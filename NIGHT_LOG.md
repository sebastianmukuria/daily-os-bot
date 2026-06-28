# Night Log — autonomous improvements (branch `night/auto-20260627`)

Goal: make the Daily OS bot more reliable, useful, and ADHD-friendly without
breaking the live bot. Flow: branch → PR (CI) → batched merges to main (deploy).
Constraints: token-frugal; new logic in dependency-free modules so it's testable
locally (notion_client/pytest unavailable here); additive/reversible Notion ops.

## Changelog
- **Reliability/foundation:** `aretry.py` — pure async retry-with-backoff helper
  (+ `test_aretry.py`, 4 tests). Will wrap flaky Notion/Telegram/Google calls.

## Found but deferred (with reasons)
- _(to fill as I go)_

## For Sebastian to review/decide (top 3)
- _(to fill by morning)_
