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
- **Usefulness:** `/today` — tight "right now" view (top 1-3 by priority→due→energy +
  next event), `format_today` (tested). Honors the "1-3 next actions" ADHD rule.

## Found but deferred (with reasons)
- **One-tap habit logging (inline keyboard) — deferred, NOT shipped.** High value but
  it touches the live evening flow, needs a CallbackQueryHandler + habit IDs threaded
  through `_get_habits` (which currently omits page id) + in-memory state that doesn't
  survive a restart, and it's not unit-testable locally. Too risky to deploy unattended
  overnight. Designed and ready to build with you. (Text-reply logging works today.)

## For Sebastian to review/decide (top 3)
1. **Inline-keyboard one-tap habit logging** — want me to build it next? (See above.)
2. **Notion plan gate**: structured queries are gated (Business plan) so the bot reads
   via the integration token, but I can't run SQL/view queries from the MCP. Worth
   upgrading if you want richer analytics, or I keep using the token path.
3. **New `/today` & `/habits` ordering**: `/today` leads with Priority; tell me if you'd
   rather it lead with Energy (to match the morning briefing's sequencing).
