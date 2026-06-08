# Daily OS Bot

A two-way Telegram assistant for a [Notion](https://notion.so)-based personal operating system. Message the bot in plain English and it manages your tasks, ideas, projects, reading list, and Google Calendar — powered by Claude.

Built with ADHD-friendly defaults: tasks ordered by energy level, smallest-next-step framing, and proactive "want me to time-block this?" prompts.

## Features

Talk to it naturally over Telegram:

- **Tasks** — "what's on my list?", "add: call dentist, low energy", "done: call dentist"
- **Ideas** — "idea: build a habit tracker"
- **Projects** — "what are my active projects?", "start a project: launch my site, check in weekly"
- **Reading list** — "add Atomic Habits as a book", "what's on my reading list?"
- **Calendar** — "what's on my calendar this week?", "put dentist on my calendar Tuesday at 2pm"
- **Anything else** — general questions get a normal Claude answer

## How it works

```
You ─▶ Telegram ─▶ bot.py (polling)
                      │
                      ▼
                 Claude API  ◀──▶  tools.py
                                      ├─ Notion API (tasks, ideas, projects, reading)
                                      └─ Google Calendar API
```

`bot.py` polls Telegram for your messages and runs an **agentic loop**: it sends your
message to Claude along with a set of tools; if Claude decides to call a tool (e.g.
`create_task`), the bot runs it, feeds the result back, and repeats until Claude has a
final answer. `tools.py` defines those tools and talks to Notion and Google Calendar.

## The bigger picture: two halves of a Daily OS

This repo is the **reactive** half of a larger personal operating system. The full
setup pairs two complementary pieces that share the same Telegram bot:

| Layer | Direction | What it does | Where it lives |
|-------|-----------|--------------|----------------|
| **This bot** | Inbound (you → bot) | Polls Telegram for your messages and responds, calling Notion / Calendar tools | This repo |
| **Proactive briefings** | Outbound (bot → you) | Sends scheduled check-ins on a timer — a morning briefing, midday check, end-of-day wrap, and inbox scans | [Claude](https://claude.ai) scheduled tasks, configured separately |

The proactive layer *pushes* messages to your Telegram chat on a schedule, while this
bot *listens* for your replies. They use the same bot token but never collide:
sending (`sendMessage`) and polling (`getUpdates`) are independent operations.

> ⚠️ Only one process may **poll** a given bot token at a time. The scheduled tasks
> only *send*, so they coexist fine with this bot — but don't run two copies of this
> bot at once.

### The proactive briefings

The outbound layer runs as scheduled tasks in [Claude](https://claude.ai). Each fires
on a cron schedule, does its work through MCP connectors (Notion, Google Calendar,
Gmail), and sends the result to Telegram via the Bot API.

| Task | Schedule (ET) | What it does |
|------|---------------|--------------|
| **Morning Briefing** | 7:30am | Reads all incomplete tasks, sorts by energy (High → Medium → Low), flags tasks untouched for 3+ days as stale (and marks them in Notion), pulls the day's calendar events, and sends a structured daily plan. |
| **Midday Check** | 12:30pm | Pulls remaining tasks and afternoon calendar events; sends a short progress nudge — how many done, what's still open. |
| **EOD Wrap** | 6:00pm | Summarizes what got done vs. what's rolling to tomorrow, pulls tomorrow's calendar, and calls out anything that's been stale for multiple days. |
| **Gmail → Calendar Sync** | 8:00am & 6:00pm | Searches Gmail for real-world events (flights, hotels, reservations, tickets, invites) and creates calendar events for any with a clear date/time that aren't already there. Only messages you if it actually added something. |

These follow the same ADHD-friendly design rules as the bot: energy-ordered tasks,
stale-task flagging, and a few fixed check-in windows instead of random notifications.

## Tech stack

- **Python** with [`python-telegram-bot`](https://python-telegram-bot.org/) (async polling)
- [`anthropic`](https://github.com/anthropics/anthropic-sdk-python) SDK — model: `claude-haiku-4-5`
- [`notion-client`](https://github.com/ramnes/notion-sdk-py) for Notion (2025-09-03 data-source API)
- `google-api-python-client` for Google Calendar

## Setup

### 1. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Then fill in `.env`:

| Variable | Where to get it |
|----------|-----------------|
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (the bot only responds to this ID) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `NOTION_TOKEN` | [notion.so/my-integrations](https://www.notion.so/my-integrations) |

After creating the Notion integration, **share each database with it**: open the
database → `•••` → Connections → add your integration.

### 3. Authenticate Google Calendar

```bash
python3 auth_google.py
```

Follow the instructions printed by the script (you'll need a `credentials.json` from
Google Cloud Console). It writes `token.json` for local use and prints a
`GOOGLE_TOKEN_JSON` value for cloud deployment.

### 4. Run it

```bash
python3 bot.py
```

Message your bot on Telegram. Send `/start` for a quick command list.

## Deployment (Railway / Render)

The included `Procfile` runs the bot as a worker process. Set every variable from
`.env` in your host's dashboard — **including `GOOGLE_TOKEN_JSON`**, because these
hosts have an ephemeral filesystem and `token.json` won't survive a redeploy.

> ⚠️ Only run **one** instance of the bot per Telegram token. Running it locally
> and in the cloud at the same time causes a polling conflict.

## Project structure

| File | Purpose |
|------|---------|
| `bot.py` | Telegram polling, the Claude agentic loop, message formatting |
| `tools.py` | Tool definitions + Notion and Google Calendar implementations |
| `auth_google.py` | One-time Google OAuth setup |
| `requirements.txt` | Python dependencies |
| `Procfile` | Process definition for Railway/Render |
| `.env.example` | Template for required environment variables |

## Development workflow

Changes are made on a branch and merged via pull request — never committed straight
to `main`:

```bash
git checkout -b descriptive-branch-name   # start a branch
# ...make changes...
git add -A
git commit -m "Describe what changed"
git push -u origin descriptive-branch-name
gh pr create                              # open a pull request
# review, then merge on GitHub (or: gh pr merge)
```

## Security

Secrets live in `.env` and `token.json` / `credentials.json`, all of which are
gitignored and must **never** be committed. The bot only responds to the single
`TELEGRAM_CHAT_ID` you configure.
