# Job Pipeline Tracker — Architecture

Design map for building the pipeline tracker spec (kept at
`~/Projects/Career/Pipeline_Tracker_Claude_Code_Plan.md`) into the Daily OS bot.
Status: **approved — building in phases.**

## 1. The core architectural insight

The spec assumes a background process ("poll Gmail every 15–30 min"), which the
reactive bot doesn't have. But we don't need a separate service: **`python-telegram-bot`
ships a `JobQueue`** that runs periodic jobs *inside the existing bot process*. The bot
is already an always-on Railway worker, so it can poll Telegram for your messages **and**
run a Gmail-scan job every ~20 min **and** fire a daily digest — all in one process, one
deploy, no new infra.

So every part of this lives in **this repo**, in one of three execution contexts:

| Context | Mechanism | Used for |
|---|---|---|
| **Reactive** | Telegram message handler (exists) | `/pipeline`, `/add`, `/note`, confirm-button taps, chat |
| **Periodic** | `JobQueue.run_repeating` (new) | Gmail poll → classify → match → write/confirm |
| **Daily** | `JobQueue.run_daily` (new) | Pipeline digest, Ghosted sweep, interview reminders |

Cowork (your general life briefings) stays untouched. Pipeline logic is version-controlled
and testable here — which the spec's "fixtures eval in CI" requirement demands anyway.

## 2. Where each spec section lands

| Spec section | Home | Notes |
|---|---|---|
| §2 Notion schema | Notion + repo constants | Two new DBs (Job Pipeline, Pipeline Events) |
| §5 `/pipeline` `/add` `/note` | Reactive (commands + tools) | Foundation, build first |
| §5 Confirm buttons | Reactive (inline keyboard + CallbackQueryHandler) | New handler type |
| §3 Gmail poll + classify | Periodic JobQueue | Needs Gmail auth (new) |
| §4 Matching + state machine | Pure module (unit-tested) | No I/O — easy to test |
| §6 Calendar watch + debrief | Periodic/Daily JobQueue | Reuses existing Calendar auth |
| §7 Backfill | One-time script (`pipeline_backfill.py`) | Like `seed.py` |
| §5 Daily digest, §4 Ghosted sweep | Daily JobQueue | Could echo into morning briefing |

## 3. Key design decisions (with recommendations)

### D1 — Scheduler home → **JobQueue inside the bot process** ✅ recommend
No second Railway service. `application.job_queue.run_repeating(...)`. Adds the
`python-telegram-bot[job-queue]` extra. Alternative (separate Railway cron) only worth it
if the poll work ever gets heavy; it won't.

### D2 — Gmail access → **add `gmail.modify` to the bot's Google OAuth** ✅ recommend
The bot's Google auth currently has Calendar scope only. Add `gmail.modify` (covers
reading messages **and** applying the `JobTracker/Processed` label; does not allow delete).
Requires re-running `auth_google.py` to re-consent, then updating `GOOGLE_TOKEN_JSON` on
Railway. Guardrail from spec §8: we only ever *add a label* — never archive/delete.

### D3 — Polling cadence → **20 min, configurable via env** ✅ recommend
`PIPELINE_POLL_MINUTES` (default 20). Alerts land within ~20 min — plenty for a job
search, and gentle on Gmail quota.

### D4 — Idempotency → **the Gmail label IS the ledger** ✅ recommend
Each poll queries job-candidate messages **without** `JobTracker/Processed`, handles them,
then applies the label. No separate ledger DB, and it survives redeploys (state lives in
Gmail, not on the ephemeral filesystem). Simpler and more robust than a message-ID file.

### D5 — Classifier eval in CI → **GitHub Action on classifier PRs** ⚪ optional
A `pytest` fixtures file (~20 real emails from spec §3) run by a GitHub Action when the
classifier/prompts change. Strongly recommended given the spec calls for it, but it's a
nice-to-have we can add at PR D rather than up front.

### D6 — Classification model → **Sonnet for the classifier**, Haiku for chat ⚪ consider
Email classification (extract company/role/event/confidence as structured output) is
exactly the reasoning-heavy work where Haiku is weakest, and it runs unattended. Worth
running the *classifier* on `claude-sonnet-4-6` even if chat stays on Haiku. Cheap because
the heuristic prefilter means we only LLM-classify a handful of emails per poll.

## 4. Data model notes

- **Job Pipeline** + **Pipeline Events** DBs created once via API (like the Habits DB),
  IDs stored as constants in `tools.py`.
- **Records are per-role**, not per-company (spec §4) — match key is thread-ID first, then
  fuzzy company+role.
- **State machine is a pure function** `(status, event_type, confidence) -> new_status | CONFIRM`
  — forward-only, rejection overrides, below-threshold ⇒ confirm. Trivial to unit-test.

## 5. PR-by-PR roadmap

| PR | Title | Context | Depends on |
|----|-------|---------|------------|
| **A** | Notion schema: Job Pipeline + Pipeline Events DBs | one-time + constants | — |
| **B** | Manual CRUD: `/pipeline`, `add/update/note` tools | reactive | A |
| **C** | Add Gmail scope + gmail client helper (no logic yet) | plumbing | — |
| **D** | Classifier: prefilter + LLM structured output + fixtures test | pure + CI | C |
| **E** | Matching + state machine (pure, unit-tested) | pure | A |
| **F** | Ingestion JobQueue + writes + confirm buttons | periodic + reactive | B,D,E |
| **G** | Backfill script (90-day scan, validate vs spec §7) | one-time | E |
| **H** | Calendar watch + interview reminders + debrief capture | periodic/daily | F |
| **I** | Daily digest + Ghosted sweep | daily | F |

**Phase 1 = PRs A + B** — bot-native, zero infra decisions, immediately usable (track
applications by hand from Telegram today). Everything Gmail/scheduled comes after.

## 6. Risks / watch-items

- **Gmail re-consent** changes the token → must update `GOOGLE_TOKEN_JSON` on Railway.
- **`[job-queue]` extra** adds APScheduler — small, well-maintained.
- **Confidence threshold** starts at 0.85 (spec §8); log everything, tune after a week.
- **Single process** runs bot + jobs — fine at this scale; if the poll ever blocks, it
  runs in JobQueue's own loop, not the message handler.
- **Cost** is bounded by the prefilter: only prefilter-passing emails hit the LLM.
