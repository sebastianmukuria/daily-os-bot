# Daily OS Bot

[![Pipeline tests](https://github.com/sebastianmukuria/daily-os-bot/actions/workflows/pipeline-tests.yml/badge.svg)](https://github.com/sebastianmukuria/daily-os-bot/actions/workflows/pipeline-tests.yml)

A personal AI assistant that runs your life ops over Telegram — powered by Claude, backed by [Notion](https://notion.so), Google Calendar, and Gmail. Message it in plain English and it manages tasks, projects, habits, reading lists, and calendar events. In the background, it **automatically tracks your job search by reading your inbox**: every application, interview invite, and rejection becomes a structured, queryable pipeline in Notion — no manual status updates.

Built ADHD-first: tasks ordered by energy level, smallest-next-step framing, a few fixed check-in windows instead of random pings, and proactive "want me to time-block this?" prompts.

```
You:  add: call the dentist, low energy
Bot:  ✓ Created "Dentist Call 🦷" (Low energy)

You:  took my vitamins
Bot:  ✓ Take Vitamins 💊 — 4-day streak 🔥

(20 min after a rejection email lands, unprompted:)
Bot:  ❌ ExampleCorp — Data Analyst: now Rejected  [Gmail link]
```

## What it does

- **Tasks / Projects / Ideas / Reading list** — full CRUD in Notion via natural language. New tasks are auto-enriched: inferred energy level, type, and a link to the right project.
- **Habits** — a dedicated tracker with streaks ("took my vitamins" → checked off, streak bumped). Habits are recurring, so they live apart from one-off tasks.
- **Calendar** — create, edit (never duplicate), and query Google Calendar events; times inferred from context.
- **Job pipeline (the headline)** — a background job polls Gmail, classifies job-search emails, and drives a per-application state machine in Notion. Instant Telegram alerts on rejections / interviews / offers; low-confidence cases ask instead of guessing.
- **Web search** — server-side search for "find me details on this event" flows, with results reusable for calendar adds.
- **Reply-context** — reply to any earlier message (like an automated digest) and the bot reads it, so "add the 2nd one to my calendar" just works.

## Architecture

One Python process, three execution contexts:

```
                       ┌─────────────────────────────────────────┐
 You ──▶ Telegram ──▶  │  REACTIVE   message handler + commands  │
                       │             (Claude agentic tool loop)  │
                       │                                         │
 Gmail ◀── poll ────▶  │  PERIODIC   JobQueue: pipeline_poll     │──▶ Notion
                       │             every 20 min                │──▶ Telegram alerts
                       │                                         │
 Calendar ◀─ watch ──▶ │  HOURLY     interview reminders +       │
                       │             post-interview debriefs     │
                       └─────────────────────────────────────────┘
```

The reactive path runs a manual agentic loop: Claude gets ~20 tools (Notion CRUD, Calendar, habits, pipeline, web search); tool calls are executed and fed back until it has a final answer. The periodic paths reuse the exact same domain logic, so manual and automated writes can't diverge.

## The job pipeline tracker

The most engineering-dense part of the repo — an email-driven ETL pipeline with a review-before-write workflow:

1. **Two-pass classification.** A deterministic prefilter (ATS sender domains, interview-scheduling recipients, recruiting language) gates a Claude structured-output classifier, so the LLM only sees genuinely job-shaped email. Known false-positives — apartment-rental and OAuth "applications" — are filtered by rule. Validated against a fixtures file of 17 labeled emails that runs in CI.
2. **Pure state machine.** `(current_status, event_type, confidence) → action`. Forward-only progression (an old email can't move you backward), rejection/offer override, sub-0.85-confidence routes to a human confirm instead of a write. 10 unit tests.
3. **Per-role matching.** Records are per-role, not per-company. Tiered matching (exact → edge → substring) keeps "Analyst I" and "Analyst II" at the same company separate — and ambiguity returns candidates for a human decision rather than guessing.
4. **Idempotent ingestion.** A Gmail label is the processing ledger; Notion writes dedupe by thread-id. Every status change appends to a `Pipeline Events` log for funnel analytics (conversion per stage, time-in-stage).
5. **90-day backfill.** A one-time script reconstructs historical applications: thread-id union-find groups emails per application, chronological replay through the state machine derives final status, real email timestamps stamp the dates. Dry-run by default, prints a reviewable plan, optionally validates against a known-state file, and resumes from an incremental classification cache (rate-limit-safe).

## Engineering practices

- **PR-based development** — every change lands through a reviewed pull request (25+ and counting), squash-merged with CI.
- **Tests where they pay rent** — the pure logic (classifier prefilter, state machine, ingest planner, backfill grouping, interview windows) is unit-tested: 40+ assertions across 5 suites, run by GitHub Actions on relevant PRs.
- **Dry-run before write** — the seeder and backfill both print a full plan and touch nothing without an explicit `--apply`.
- **Honest failure reporting** — every tool result is logged (with tracebacks) and surfaced to the user as explicit per-action ✓/✗, never silently swallowed.

## Setup

Fair warning: this is a personal single-user system, not a packaged product. Expect ~30–60 minutes of API setup.

1. **Install:** `pip3 install -r requirements.txt`
2. **Configure:** `cp .env.example .env` and fill it in (Telegram bot token + chat ID, Anthropic API key, Notion integration token). See the comments in [.env.example](.env.example).
3. **Notion:** create your databases (Tasks, Projects, Ideas, Reading, Habits, Job Pipeline, Pipeline Events) and share each with your integration, then set the data-source IDs at the top of [tools.py](tools.py). Schemas are documented inline.
4. **Google:** create a Cloud project, enable the Calendar + Gmail APIs, download OAuth desktop credentials as `credentials.json`, then run `python3 auth_google.py` (writes `token.json`; prints a `GOOGLE_TOKEN_JSON` value for cloud deploys).
5. **Run:** `python3 bot.py`

### Deploying (Railway / Render)

The `Procfile` runs the bot as a worker. Set every `.env` variable in the host's dashboard — including `GOOGLE_TOKEN_JSON`, because ephemeral filesystems lose `token.json` on redeploy. Merges to `main` auto-deploy if you connect the repo.

> ⚠️ Run exactly **one** instance per Telegram bot token — two pollers conflict.

### Configuration

| Variable | Purpose | Default |
|---|---|---|
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Bot identity; the single chat it serves | required |
| `ANTHROPIC_API_KEY` | Claude API | required |
| `NOTION_TOKEN` | Notion integration | required |
| `GOOGLE_TOKEN_JSON` | OAuth token for cloud deploys | falls back to `token.json` |
| `CLAUDE_MODEL` | Chat model | `claude-haiku-4-5` |
| `CLASSIFIER_MODEL` | Email classifier model | `claude-haiku-4-5` |
| `PIPELINE_POLL_MINUTES` | Gmail poll cadence | `20` |
| `BACKFILL_PACE_SEC` | Backfill classification pacing | `1.4` |

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Telegram handlers, Claude agentic loop, JobQueue scheduling, formatting |
| `tools.py` | Tool schemas + implementations: Notion (tasks/projects/ideas/reading/habits/pipeline), Calendar, Gmail |
| `pipeline_classifier.py` | Prefilter + LLM email classification (structured output) |
| `pipeline_state.py` | Pure state machine + per-role matching |
| `pipeline_ingest.py` | Plan + apply: classified email → Notion writes / Telegram alerts |
| `pipeline_interviews.py` | Interview detection, reminder + debrief windows (pure) |
| `pipeline_backfill.py` | One-time 90-day historical reconstruction (dry-run first) |
| `seed.py` | Idempotent bulk-seeder for projects/tasks/ideas from YAML |
| `test_*.py`, `fixtures/` | Unit tests + labeled email fixtures (run in CI) |
| `auth_google.py` | One-time Google OAuth (Calendar + Gmail scopes) |

## The proactive layer

This bot is the *reactive* half of a larger setup. A separate scheduled-agent layer (Claude scheduled tasks) pushes a morning briefing (energy-ordered tasks + habits due), a midday check, an end-of-day wrap, and Gmail→Calendar sweeps to the same Telegram chat. They share the bot token safely: senders and pollers don't conflict. The bot's reply-context feature closes the loop — reply to any briefing to act on it.

## Limitations & roadmap

- **Single-user by design** — one chat ID, one Notion workspace, no multi-tenancy.
- Notion database IDs are constants in `tools.py`; provisioning them is manual (a setup script is a welcome contribution).
- Conversation memory is in-process (last 20 messages) — restarts forget context.
- Pipeline analytics (conversion rates, time-in-stage from `Pipeline Events`) are logged but not yet reported.

## Development workflow

```bash
git checkout -b descriptive-branch-name
# ...make changes, run the test suites...
git add -A && git commit -m "describe the change"
git push -u origin descriptive-branch-name
gh pr create   # CI runs the pipeline tests; review, then squash-merge
```

## Security

Secrets live in `.env`, `token.json`, and `credentials.json` — all gitignored, never committed. Personal data files (`seed_data.yaml`, `backfill_cache.json`, `backfill_targets.json`) are gitignored too. The bot answers only the configured `TELEGRAM_CHAT_ID`, Gmail access is read-plus-label only (it never archives, deletes, or sends mail), and every fixture in this repo is synthetic.

## License

[MIT](LICENSE)
