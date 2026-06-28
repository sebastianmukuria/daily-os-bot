# Night Log — autonomous improvements (branch `night/auto-20260627`)

Goal: make the Daily OS bot more reliable, useful, and ADHD-friendly without
breaking the live bot. Flow: branch → PR (CI) → batched merges to main (deploy).
Constraints: token-frugal; new logic in dependency-free modules so it's testable
locally (notion_client/pytest unavailable here); additive/reversible Notion ops.

## Changelog
- **Reliability/foundation:** `aretry.py` — pure async retry-with-backoff helper
  (+ `test_aretry.py`, 4 tests). Will wrap flaky Notion/Telegram/Google calls.
- **Reliability: no more silent briefing failures.** `briefings.format_job_failure`
  (+ test) + `bot._alert`; the 6 user-facing scheduled jobs (morning/midday/evening,
  week-ahead/in-review, pipeline digest) now Telegram-alert on exception instead of
  only logging. Background/high-frequency jobs left on silent-log to avoid spam.

- **Usefulness:** `/habits` quick-status command (no LLM) + `format_habits_status`
  (tested); the 9pm wrap now shows 🔥 streaks on still-open habits for momentum.

## Found but deferred (with reasons)
- _(to fill as I go)_

## For Sebastian to review/decide (top 3)
- _(to fill by morning)_
