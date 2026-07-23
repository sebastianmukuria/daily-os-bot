import os
import re
import json
import html
import time
import asyncio
import logging
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import pytz
from dotenv import load_dotenv

load_dotenv()  # must run before tools.py is imported so NOTION_TOKEN is set

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from tools import TOOLS, WEB_SEARCH_TOOL, execute_tool, get_project_names

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# python-telegram-bot's httpx logs are noisy at INFO; quiet them.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("daily_os_bot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])  # the only chat the bot serves

MAX_TOOL_ITERATIONS = 8  # cap the agentic loop so it can't run away on tokens
TELEGRAM_MAX_CHARS = 4096
PIPELINE_POLL_MINUTES = int(os.environ.get("PIPELINE_POLL_MINUTES", "20"))
PIPELINE_DIGEST_HOUR = int(os.environ.get("PIPELINE_DIGEST_HOUR", "8"))  # ET
HEALTHCHECK_HOUR = int(os.environ.get("HEALTHCHECK_HOUR", "7"))  # ET; before the digest
# Switch models without code changes: set CLAUDE_MODEL in the environment.
# Default Haiku (cheap, fast). Bump to claude-sonnet-4-6 for stronger reasoning.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory conversation history per chat
conversation_history: dict[int, list] = {}
# Interview events we've already reminded / asked to debrief (reset on restart).
_interview_reminded: set = set()
_interview_debriefed: set = set()

SYSTEM_PROMPT = """You are Sebastian's personal AI assistant, embedded in his Daily OS Telegram bot. Sebastian has ADHD.

Rules for how you respond:
- Smallest-commit framing: say "open the draft" not "finish the essay", "send one text" not "resolve the conflict"
- When listing tasks, order by Energy: High → Medium → Low. Flag stale tasks (not updated in 3+ days) clearly.
- NEVER use emojis — not in task/idea/project/event titles, not in your replies, not in any Notion field. Plain text only, always.
- Be concise — this is Telegram. Use bullet points, avoid walls of text.
- Give 1-3 next actions max. Never overwhelm.
- When someone says "add: X" parse it as a task creation. "done: X" = complete task. "idea: X" = add idea.

Always enrich new tasks (never leave them bare):
- Energy: infer High / Medium / Low from how cognitively demanding the task is — don't just default to Medium.
- Priority: set High / Medium / Low from importance + urgency + stakes (a hard deadline, money, a promise to someone → higher; nice-to-have/someday → Low). If you genuinely can't tell for a task that matters, ASK one short question ("How urgent — high, medium, or low?") and create it once he answers. Trivial tasks can be Low without asking.
- Type: set it. Usually "Task"; use "Appointment" for things with a set time/place, "Admin/Inbox" for quick admin.
- Project: ALWAYS link one. Pick the best fit from his active projects (listed at the end of this prompt): Work = day-job tasks & meetings; Career = interviews, applications, job search; a specific named project when it clearly fits; Miscellaneous only when it genuinely fits none of the others. If you truly can't tell (e.g. Work vs a specific project, or whether it's Career), ASK instead of guessing — don't just dump it in Miscellaneous.
- (Status starts as Not Started automatically.)

When project, priority, energy, or due date is genuinely unclear for a task that matters, don't guess — ask. If more than one is unclear, ask ONE short combined question listing what you need (e.g. "Which project, and how urgent?") and create the task once he answers. For clearly trivial/low-stakes tasks, fill sensible defaults without nagging.

Due dates — don't silently leave a task undated when timing matters:
- If he gives a date or deadline ("by Friday", "before the 15th", "tomorrow", "end of month"), use it — compute the real YYYY-MM-DD from the current date provided.
- If he doesn't, infer from urgency in his wording: "urgent / ASAP / need to / today" → today; "soon / this week" → ~2-3 days out; a hard deadline he names → that date. For a time-sensitive task you may call get_calendar_events first and avoid landing the due date on an already-packed day.
- For effortful work or deliverables with real stakes — a report, application, essay, presentation, prep for a known event, anything with a likely deadline — if he gave no date and you can't infer one, ASK one short question ("When's this due?") and DON'T create the task yet; create it with the date once he answers.
- Only skip the date for clearly low-stakes someday tasks with no urgency ("read this article", "look into X", "buy milk") — leave those undated and don't nag.
- Whenever you set a due date you inferred, say what you set ("due Thu") so he can correct it, and offer to time-block before it.

Naming things — polish the title, never use emojis:
- Whenever you create a task, calendar event, idea, project, or reading-list item, never use Sebastian's raw phrasing verbatim. Rewrite it into a concise, clean, title-cased label — plain text, no emoji, no decoration.
- Examples: "go to the gym at 8am" → "Gym Session"; "call dentist about the crown" → "Dentist Call"; "write the q3 essay" → "Q3 Essay"; "buy groceries" → "Grocery Run".
- Keep the real meaning and any important specifics — just make it cleaner and shorter.
- When one request creates BOTH a task and a calendar event, use the SAME polished title for both.

Report outcomes honestly and specifically:
- After using tools, tell Sebastian exactly what happened with EACH action — what worked and what didn't. Mark each result clearly in plain words (no symbols or emojis).
- If a tool returns an "error" field, that action FAILED. Say so plainly and include the actual reason (paraphrase the error briefly), e.g. "Couldn't add to calendar — the Google token is invalid." Never call a failure "finicky", never gloss over it, and never imply something was saved when the tool returned an error.
- If part of a multi-step request succeeds and part fails, list each result separately so it's clear what still needs doing.

You have access to:
- Notion Tasks DB (get, create, complete, edit tasks)
- Notion Ideas DB (add ideas)
- Notion Projects DB (get projects, add projects — useful for check-ins)
- Notion Reading List DB (get list, add books/articles/papers/videos/podcasts)
- Notion Journal (save daily reflections / journal entries)
- Google Calendar (view upcoming events, create new events, edit existing events)
- Habits tracker (show habits, mark a habit done for today, add a habit)
- Job Pipeline (track job applications: add, update status, add notes, view pipeline)
- Web search (look up current info, facts, and event details)

Job pipeline:
- When Sebastian says he applied somewhere, use add_application (Status defaults to Applied). When he gets a recruiter screen / interview / offer / rejection, use update_application to move the status. Use add_application_note for updates worth keeping (it timestamps them).
- get_pipeline shows the current pipeline. Records are PER-ROLE — one company can have several (e.g. Analyst I and II). If it's ambiguous which role he means, ask.

Habits vs. tasks:
- Habits are recurring things he wants to do regularly (gym, vitamins, meditation, journaling). They live in a separate Habits tracker — NOT the Tasks DB. Never create a task for a recurring habit.
- When he says he did one ("took my vitamins", "hit the gym", "meditated"), call log_habit to check it off and bump the streak. Celebrate the streak briefly.
- "add a habit" / "track X daily" → add_habit. "what are my habits / did I do them?" → get_habits.

Evening wrap & journaling:
- The 9pm wrap lists any habits still open and asks him to reply with what he did + a line on his day.
- When he replies about his day: call log_habit for EACH habit he names as done (celebrate streaks briefly), then call journal to save the reflective part (his words, first person, lightly cleaned — don't add your own commentary). "all" / "did them all" = log every habit the wrap listed as still open.
- Any other time he reflects on how his day or week went, or how he's feeling, save it with journal (tag Daily unless it's clearly Work / Personal / Planning / Special Event).

Replied-to context:
- A message may begin with quoted "[Context — Sebastian is replying to this earlier message…]". That quoted block is an earlier message (often an automated briefing or events digest) he's responding to. Use it to resolve references like "the second one", "that event", or "add it" — pull the relevant details out of the quoted text and act on them.

Web search:
- Use it when Sebastian asks for current information or details you don't reliably know — event times/venues/tickets, business hours, prices, news, sports schedules, etc.
- Summarize the key facts concisely (for an event: name, date, time, venue, address, ticket/price, link). Don't paste walls of text.
- If he later says to add something to his calendar, use the details you found (date, time, venue as the location) to create the event — ask only for anything genuinely missing.

When creating calendar events, infer the date/time from context and the current date provided.
Times are Eastern. If no end time is given, a 1-hour default is fine.

Editing vs. creating — don't make duplicates:
- If Sebastian asks to change or add a detail to an event that already exists (add a location, move the time, rename it), UPDATE that event with update_calendar_event. Never create a second event for the same thing.
- If you just created the event this conversation, reuse the id you got back. Otherwise call get_calendar_events to find the right event and its id first.
- Same for tasks: to change a task's energy, due date, status, name, or notes (e.g. "make that high energy", "push it to Friday"), use update_task — never create a new task for an edit.

Proactive scheduling (important for ADHD — externalize time so it doesn't live in his head):
- If something has a real time/place — an appointment, meeting, call, reservation, anything with a "when" — ask: "Want me to add this to your calendar?"
- If something is effortful or easy to keep postponing — a deep-work task, an essay, a workout, errands — offer to protect time for it: "Should I time-block this? When works?"
- When you create a task that has a due date, offer to time-block a session before the deadline, not just mark the deadline.
- Ask ONE short follow-up, don't assume. If he already gave a time, just create it (don't ask permission redundantly). If he says no, drop it — never nag.
- Default time-block length: 25–50 min (a single focused session), never a vague multi-hour block."""


def _markdown_to_telegram_html(text: str) -> str:
    """Convert the common markdown Claude emits into Telegram-safe HTML.

    Telegram's reply_text sends plain text by default, so '**bold**' shows literal
    asterisks. We escape HTML first, then translate **bold**, *italic*/_italic_,
    `code`, and [label](url) into tags, and turn '- '/'* ' bullets into '• '.
    Anything we don't recognize is left as escaped text.
    """
    text = html.escape(text)
    # Code spans first so their contents aren't re-processed.
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Links: [label](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
    # Bold (** or __) before italic so the inner * isn't grabbed first.
    text = re.sub(r"\*\*([^\n]+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^\n]+?)__", r"<b>\1</b>", text)
    # Italic: single * or _ around a span.
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"<i>\1</i>", text)
    # Bullet markers at line start.
    text = re.sub(r"(?m)^\s*[-*]\s+", "• ", text)
    return text


def _chunk(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        # A single line longer than the limit gets hard-split.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def send_reply(update: Update, text: str) -> None:
    """Send a possibly-long, markdown-formatted reply, falling back to plain text
    if Telegram rejects the HTML (so a formatting glitch never eats a message)."""
    for chunk in _chunk(text):
        try:
            await update.message.reply_text(
                _markdown_to_telegram_html(chunk), parse_mode="HTML"
            )
        except BadRequest:
            logger.warning("HTML parse failed; sending as plain text")
            await update.message.reply_text(chunk)


def _trim_history(messages: list, max_messages: int = 20) -> list:
    """Trim history without splitting a tool_use/tool_result pair.

    A naive messages[-N:] can cut between an assistant message holding a
    `tool_use` block and the following user message holding the matching
    `tool_result`, leaving an orphaned tool_result that the API rejects.
    So after slicing we drop any leading assistant message or any leading
    user message that contains tool_result blocks — history must always
    start on a clean user text turn.
    """
    if len(messages) <= max_messages:
        return messages

    trimmed = messages[-max_messages:]
    while trimmed:
        first = trimmed[0]
        content = first.get("content")
        is_orphan_tool_result = (
            first["role"] == "user"
            and isinstance(content, list)
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
        )
        if first["role"] == "assistant" or is_orphan_tool_result:
            trimmed.pop(0)
        else:
            break
    return trimmed


def _build_user_content(text: str, replied_text: str | None) -> str:
    """If Sebastian is replying to an earlier message (e.g. a cowork digest the
    bot can't otherwise see), quote it so Claude has the context to act on it."""
    if replied_text:
        return (
            "[Context — Sebastian is replying to this earlier message "
            "(it may be an automated briefing/digest):\n"
            f'"""\n{replied_text}\n"""\n]\n\n{text}'
        )
    return text


# Cache active project names so we can inject them into the system prompt (for
# task→project linking) without querying Notion on every single message.
_projects_cache: dict = {"names": [], "ts": 0.0}
_PROJECTS_TTL = 600  # seconds


async def _active_project_names() -> list:
    if time.time() - _projects_cache["ts"] > _PROJECTS_TTL:
        try:
            _projects_cache["names"] = await get_project_names()
            _projects_cache["ts"] = time.time()
        except Exception:
            logger.warning("could not refresh project list", exc_info=True)
    return _projects_cache["names"]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    chat_id = update.effective_chat.id

    # If this is a reply to another message, pull that message's text in as context
    # (this is the only way the bot can "see" a message it/cowork sent earlier).
    replied = update.message.reply_to_message
    replied_text = (getattr(replied, "text", None) or getattr(replied, "caption", None)) if replied else None
    user_content = _build_user_content(update.message.text, replied_text)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    base_history = conversation_history.get(chat_id, [])

    et = pytz.timezone("America/New_York")
    now_str = datetime.now(et).strftime("%A, %B %d, %Y %I:%M %p ET")
    project_names = await _active_project_names()
    projects_line = (
        "Sebastian's active projects (use these names to link tasks): "
        + "; ".join(project_names)
        if project_names
        else "No active projects found."
    )
    system = f"{SYSTEM_PROMPT}\n\n{projects_line}\n\nCurrent date/time: {now_str}"

    messages = base_history + [{"role": "user", "content": user_content}]

    assistant_text = ""

    try:
        # Agentic tool loop, capped so it can't run away on tokens.
        for _ in range(MAX_TOOL_ITERATIONS):
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=system,
                tools=TOOLS + [WEB_SEARCH_TOOL],
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    # Only our own tools need executing; server tools (web search)
                    # are run by the API and won't appear as "tool_use" blocks.
                    if block.type == "tool_use":
                        result = await execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })

                messages.append({"role": "user", "content": tool_results})
                continue  # loop back for Claude to use the results

            if response.stop_reason == "pause_turn":
                # A server tool (e.g. web search) needs another round-trip to finish.
                messages.append({"role": "assistant", "content": response.content})
                continue

            # Any other stop reason (end_turn, max_tokens, etc.) ends the turn.
            assistant_text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            messages.append({"role": "assistant", "content": response.content})
            break
        else:
            # Loop hit the cap without a final answer — discard this messy turn
            # (the last message is a tool_result with no follow-up) to keep history clean.
            logger.warning("Tool loop hit MAX_TOOL_ITERATIONS for chat %s", chat_id)
            conversation_history[chat_id] = base_history
            assistant_text = (
                "That turned into a lot of steps so I stopped. "
                "Try breaking it into a smaller ask?"
            )
            await send_reply(update, assistant_text)
            return

        # Persist, trimming only at safe turn boundaries
        conversation_history[chat_id] = _trim_history(messages)

    except Exception as e:
        logger.exception("handle_message failed")
        # Reset this chat's history so a corrupted state can't get stuck looping
        conversation_history[chat_id] = []
        assistant_text = f"Something broke: {type(e).__name__}. I reset our conversation — try again."

    # Always reply with something, even if Claude returned no text after a tool call.
    await send_reply(update, assistant_text or "Done.")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await update.message.reply_text(
        "Daily OS online.\n\n"
        "Try:\n"
        "• What's on my task list?\n"
        "• add: call dentist, low energy\n"
        "• done: call dentist\n"
        "• idea: build a habit tracker\n"
        "• What's on my calendar this week?\n\n"
        "/help — full list of commands and capabilities"
    )


async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    conversation_history.pop(update.effective_chat.id, None)
    await update.message.reply_text("Conversation cleared.")


async def handle_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick job-pipeline view, grouped by status — no LLM call."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    result = await execute_tool("get_pipeline", {})
    apps = result.get("applications", [])
    if not apps:
        await send_reply(update, "No applications in the pipeline yet. Tell me about one and I'll add it.")
        return

    lines = ["**Job Pipeline**"]
    current_status = None
    for a in apps:
        if a["status"] != current_status:
            current_status = a["status"]
            lines.append(f"\n**{current_status}**")
        role = f" — {a['role']}" if a.get("role") else ""
        nxt = f"  · next: {a['next_action']}" if a.get("next_action") else ""
        lines.append(f"• {a['company']}{role}{nxt}")
    await send_reply(update, "\n".join(lines))


async def handle_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand morning briefing."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await morning_briefing(context)


async def handle_midday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand midday check."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await midday_check(context)


async def handle_eod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand end-of-day wrap."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await eod_wrap(context)


async def handle_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tight 'what should I do right now' view — no LLM call."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    import tools, briefings
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        tasks = (await tools._get_tasks())["tasks"]
        now = datetime.now(ZoneInfo("America/New_York"))
        all_today = (await asyncio.to_thread(tools.get_events_for_day, 0))["events"]
        nowhm = now.strftime("%H:%M")
        upcoming = [e for e in all_today if not e["all_day"] and e["sort_key"] >= nowhm]
        await update.message.reply_text(
            briefings.format_today(tasks, upcoming, now.date()), parse_mode="HTML"
        )
    except Exception as e:
        logger.exception("handle_today failed")
        await update.message.reply_text(f"Couldn't build your 'right now' view: {type(e).__name__}")


async def handle_habits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick habit check — no LLM call."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    import tools, briefings
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        habits = (await tools._get_habits())["habits"]
        await update.message.reply_text(briefings.format_habits_status(habits), parse_mode="HTML")
    except Exception as e:
        logger.exception("handle_habits failed")
        await update.message.reply_text(f"Couldn't fetch habits: {type(e).__name__}")


async def handle_funnel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live job-search funnel snapshot (current status), straight from Notion."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    apps = (await execute_tool("get_pipeline", {})).get("applications", [])
    if not apps:
        await send_reply(update, "No applications in the pipeline yet.")
        return
    counts: dict = {}
    for a in apps:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    order = ["Applied", "Recruiter Screen", "Interviewing", "Final Round", "Offer",
             "Rejected", "Withdrawn", "Ghosted"]
    total = len(apps)
    advanced = sum(counts.get(s, 0) for s in ("Recruiter Screen", "Interviewing", "Final Round", "Offer"))
    lines = [f"<b>Job Pipeline</b> — {total} applications", ""]
    lines += [f"• {s}: {counts[s]}" for s in order if counts.get(s)]
    lines += ["", f"Currently past screen: {advanced} · Offers: {counts.get('Offer', 0)}",
              "<i>Full conversion funnel lives in the Evidence dashboard.</i>"]
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="\n".join(lines), parse_mode="HTML"
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List everything the bot can do."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await update.message.reply_text(
        "Daily OS — here's what I can do.\n\n"
        "Commands:\n"
        "/briefing — morning briefing on demand\n"
        "/midday — midday check\n"
        "/eod — end-of-day wrap\n"
        "/today — your top 1-3 to do right now\n"
        "/habits — today's habit check-in\n"
        "/funnel — job-search funnel snapshot\n"
        "/pipeline — applications by status\n"
        "/clear — reset our conversation\n"
        "/help — this message\n\n"
        "Or just talk to me:\n"
        "• add: call dentist, low energy\n"
        "• done: call dentist\n"
        "• took my vitamins  (logs a habit + streak)\n"
        "• idea: build a habit tracker\n"
        "• reading: <url>\n"
        "• what's on my calendar this week?\n"
        "• reply to any message → \"add the 2nd one to my calendar\"\n\n"
        "I also reach out on my own: morning/midday/EOD briefings, Sunday & Friday "
        "week roundups, job-application updates from your inbox, and inbox→calendar sync."
    )


async def pipeline_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job: scan recent unprocessed Gmail, classify, and apply pipeline
    updates. Job-related messages are labeled JobTracker/Processed so they're
    handled once; non-job mail is left unlabeled (cheap to re-check)."""
    import tools
    from pipeline_classifier import prefilter, classify_email
    from pipeline_ingest import plan_email, apply_plan

    try:
        candidates = await asyncio.to_thread(tools.gmail_fetch_candidates)
    except Exception:
        logger.exception("pipeline_poll: could not fetch Gmail")
        return
    if not candidates:
        return

    records = await tools.gather_pipeline_records()
    for email in candidates:
        try:
            if not prefilter(email)["job_related"]:
                continue
            classification = await asyncio.to_thread(classify_email, email)
            plan = plan_email(email, classification, records)
            out = await apply_plan(plan, email)
            # Mark processed BEFORE notifying: a Telegram failure must not cause the
            # same email to be re-ingested next poll (which would duplicate the record).
            await asyncio.to_thread(tools.gmail_apply_processed_label, email["id"])
            for text in (out.get("alert"), out.get("confirm")):
                if text:
                    await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text)
        except Exception:
            logger.exception("pipeline_poll: failed on message %s", email.get("id"))


async def pipeline_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily job: ghost stale Applieds, then send the pipeline digest."""
    import tools
    from pipeline_ingest import build_daily_digest
    try:
        ghosted = await tools.sweep_ghosted()
        text = await build_daily_digest(ghosted)
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text)
    except Exception as e:
        logger.exception("pipeline_daily failed")
        await _alert(context, "Job pipeline digest", e)


async def _send_html(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send HTML to the user, chunked under Telegram's 4096-char limit so a long
    briefing is never dropped as 'message too long'."""
    for chunk in _chunk(text):
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=chunk, parse_mode="HTML")


async def _alert(context: ContextTypes.DEFAULT_TYPE, label: str, exc: Exception) -> None:
    """Best-effort failure alert so a broken scheduled job isn't silent."""
    import briefings
    try:
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=briefings.format_job_failure(label, f"{type(exc).__name__}: {exc}"),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("could not send failure alert for %s", label)


async def morning_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7:30am ET — calendar + energy-sorted tasks + project check-ins + stale flags."""
    import tools, briefings
    try:
        tasks = (await tools._get_tasks())["tasks"]
        for t in tasks:  # persist the stale flag back to Notion
            if t["stale"]:
                try:
                    await tools.set_task_stale(t["id"])
                except Exception:
                    pass
        events = (await asyncio.to_thread(tools.get_events_for_day, 0))["events"]
        habits = (await tools._get_habits())["habits"]
        today = datetime.now(ZoneInfo("America/New_York")).date()
        text = briefings.format_morning(tasks, events, today, habits)
        await _send_html(context, text)
    except Exception as e:
        logger.exception("morning_briefing failed")
        await _alert(context, "Morning briefing", e)


async def midday_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """12:30pm ET — progress (done vs open), afternoon events, top remaining tasks."""
    import tools, briefings
    try:
        tasks = (await tools._get_tasks())["tasks"]
        done_count = len(await tools.get_tasks_done_today())
        now = datetime.now(ZoneInfo("America/New_York"))
        all_today = (await asyncio.to_thread(tools.get_events_for_day, 0))["events"]
        nowhm = now.strftime("%H:%M")
        afternoon = [e for e in all_today if not e["all_day"] and e["sort_key"] >= nowhm]
        text = briefings.format_midday(tasks, done_count, afternoon, now.date())
        await _send_html(context, text)
    except Exception as e:
        logger.exception("midday_check failed")
        await _alert(context, "Midday check", e)


async def eod_wrap(context: ContextTypes.DEFAULT_TYPE) -> None:
    """9pm ET — what got done, what's rolling, tomorrow's calendar, habit check-in + journal prompt."""
    import tools, briefings
    try:
        done = await tools.get_tasks_done_today()
        rolling = (await tools._get_tasks())["tasks"]
        tomorrow = (await asyncio.to_thread(tools.get_events_for_day, 1))["events"]
        habits = (await tools._get_habits())["habits"]
        today = datetime.now(ZoneInfo("America/New_York")).date()
        text = briefings.format_eod(done, rolling, tomorrow, today, habits)
        await _send_html(context, text)
    except Exception as e:
        logger.exception("eod_wrap failed")
        await _alert(context, "Evening wrap", e)


async def week_start_roundup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sunday 5pm ET — the week ahead: calendar, interviews/job actions, deadlines, check-ins."""
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() != 6:  # Sunday
        return
    import tools, briefings
    try:
        cal = await asyncio.to_thread(tools.get_events_for_range, 1, 7)  # Mon–Sun
        ws = (now.date() + timedelta(days=1)).isoformat()
        we = (now.date() + timedelta(days=7)).isoformat()
        tasks = (await tools._get_tasks())["tasks"]
        due_tasks = [t for t in tasks if t.get("due_date") and ws <= t["due_date"] <= we]
        apps = (await tools._get_pipeline()).get("applications", [])
        job_actions = [a for a in apps if a.get("next_action_due") and ws <= a["next_action_due"] <= we]
        projects = (await tools._get_projects()).get("projects", [])
        checkins = [p for p in projects if p.get("next_check_in") and ws <= p["next_check_in"] <= we]
        label = f"{(now.date() + timedelta(days=1)):%b %-d} – {(now.date() + timedelta(days=7)):%b %-d}"
        text = briefings.format_week_start(cal, due_tasks, job_actions, checkins, label)
        await _send_html(context, text)
    except Exception as e:
        logger.exception("week_start_roundup failed")
        await _alert(context, "Week-ahead roundup", e)


async def week_end_roundup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Friday 5pm ET — week in review + a look into next week."""
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() != 4:  # Friday
        return
    import tools, briefings
    try:
        done = await tools.get_tasks_done_this_week()
        events = await tools.get_pipeline_events_since(7)
        next_cal = await asyncio.to_thread(tools.get_events_for_range, 1, 5)  # Sat–Wed
        tasks = (await tools._get_tasks())["tasks"]
        today = now.date().isoformat()
        overdue = [t for t in tasks if t.get("due_date") and t["due_date"] < today]
        stale_count = sum(1 for t in tasks if t.get("stale"))
        apps = (await tools._get_pipeline()).get("applications", [])
        ns = (now.date() + timedelta(days=1)).isoformat()
        ne = (now.date() + timedelta(days=10)).isoformat()
        upcoming = [a for a in apps if a.get("next_action_due") and ns <= a["next_action_due"] <= ne]
        label = f"{(now.date() - timedelta(days=4)):%b %-d} – {now.date():%b %-d}"
        text = briefings.format_week_end(done, events, next_cal, overdue, stale_count, upcoming, label)
        await _send_html(context, text)
    except Exception as e:
        logger.exception("week_end_roundup failed")
        await _alert(context, "Week-in-review", e)


async def inbox_calendar_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    """8am + 6pm ET — scan recent mail for events/bookings, add to calendar
    (dedup'd), file follow-ups, and notify only if something was created."""
    import tools, inbox_events
    query = (
        f"newer_than:2d -label:{tools.CALENDAR_SYNC_LABEL} "
        "(confirmation OR reservation OR booking OR itinerary OR flight OR hotel OR "
        "ticket OR appointment OR invite OR reschedule)"
    )
    try:
        candidates = await asyncio.to_thread(tools.gmail_fetch_all, query, 25)
    except Exception:
        logger.exception("inbox_calendar_sync: gmail fetch failed")
        return

    created = []
    for email in candidates:
        try:
            if not inbox_events.looks_like_event(email):
                continue
            ev = await asyncio.to_thread(inbox_events.extract_event, email)
            # label first so we never re-extract this message, event or not
            await asyncio.to_thread(
                tools.gmail_apply_processed_label, email["id"], tools.CALENDAR_SYNC_LABEL
            )
            if not ev.get("is_event") or not ev.get("date"):
                continue
            if await asyncio.to_thread(tools.calendar_event_exists, ev["title"], ev["date"]):
                continue
            start, end, all_day = inbox_events.build_event_times(
                ev["date"], ev.get("start_time", ""), ev.get("end_time", ""), ev.get("all_day", False)
            )
            desc = f"Confirmation: {ev['confirmation']}" if ev.get("confirmation") else None
            await asyncio.to_thread(
                tools._create_calendar_event, ev["title"], start, end, all_day,
                ev.get("location") or None, desc,
            )
            when = ev["date"] + (f" {ev['start_time']}" if ev.get("start_time") else " (all day)")
            created.append(f"• {ev['title']} — {when}")
            if ev.get("followup"):
                try:
                    await tools._create_task(name=ev["followup"], energy="Low", type="Task")
                except Exception:
                    logger.exception("inbox_calendar_sync: follow-up task failed")
        except Exception:
            logger.exception("inbox_calendar_sync: failed on %s", email.get("id"))

    if created:
        text = "<b>Inbox Sync</b>\n\nAdded to your calendar:\n" + "\n".join(created)
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text, parse_mode="HTML")


async def gmail_healthcheck(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm Gmail auth still works; alert on Telegram if it's broken (e.g. an
    expired/revoked token) so an outage surfaces in minutes, not days. Runs once
    at startup (catches a bad deploy) and daily (catches mid-life token expiry)."""
    import tools
    try:
        await asyncio.to_thread(tools.gmail_check)
    except Exception as e:
        logger.exception("gmail_healthcheck: Gmail auth check failed")
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=(
                "Gmail access is down — the job tracker can't read your inbox.\n"
                f"({type(e).__name__})\n"
                "Fix: re-run auth_google.py, then update GOOGLE_TOKEN_JSON on Railway."
            ),
        )


async def interview_watch(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly: remind ~1h before interviews and prompt a debrief after they end.
    Debriefs are captured by replying to the prompt (the reply lands a note on the
    matching pipeline record via the normal handler)."""
    import tools
    from pipeline_interviews import is_interview, extract_company, due_reminder, due_debrief

    try:
        events = await asyncio.to_thread(tools.get_calendar_events_window, 3, 2)
    except Exception:
        logger.exception("interview_watch: calendar fetch failed")
        return

    now = datetime.now(ZoneInfo("America/New_York"))
    for e in events:
        if not is_interview(e.get("summary", ""), e.get("attendees", [])):
            continue
        try:
            start = datetime.fromisoformat(e["start"])
        except (ValueError, KeyError):
            continue
        if start.tzinfo is None:
            continue
        company = extract_company(e["summary"]) or "the role"
        eid = e["id"]

        if eid not in _interview_reminded and due_reminder(start, now):
            _interview_reminded.add(eid)
            prep = f"~/Projects/Career/Applications/{company}/Prep/"
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"Interview soon: {e['summary']} at {start.strftime('%-I:%M %p')}\nPrep: {prep}",
            )
            try:  # best-effort link onto the pipeline record
                await execute_tool("add_application_note",
                                   {"company": company, "note": f"Interview {start.date().isoformat()}"})
            except Exception:
                logger.exception("interview_watch: could not note pipeline record")

        if eid not in _interview_debriefed and due_debrief(start, now):
            _interview_debriefed.add(eid)
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=(f"How did the {company} interview go? Reply with what they asked and "
                      "what felt weak — I'll save it to the pipeline."),
            )

    if len(_interview_reminded) > 500:
        _interview_reminded.clear()
    if len(_interview_debriefed) > 500:
        _interview_debriefed.clear()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all so an unhandled error logs instead of printing a bare traceback."""
    logger.error("Unhandled error", exc_info=context.error)


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("pipeline", handle_pipeline))
    app.add_handler(CommandHandler("briefing", handle_briefing))
    app.add_handler(CommandHandler("midday", handle_midday))
    app.add_handler(CommandHandler("eod", handle_eod))
    app.add_handler(CommandHandler("today", handle_today))
    app.add_handler(CommandHandler("habits", handle_habits))
    app.add_handler(CommandHandler("funnel", handle_funnel))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    # Periodic job-pipeline ingestion (needs the python-telegram-bot[job-queue] extra).
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            pipeline_poll, interval=PIPELINE_POLL_MINUTES * 60, first=30,
        )
        app.job_queue.run_daily(
            pipeline_daily,
            time=dtime(PIPELINE_DIGEST_HOUR, 0, tzinfo=ZoneInfo("America/New_York")),
        )
        app.job_queue.run_repeating(interview_watch, interval=3600, first=90)
        # Daily briefings (moved in-house from cowork).
        ET = ZoneInfo("America/New_York")
        app.job_queue.run_daily(morning_briefing, time=dtime(7, 30, tzinfo=ET))
        app.job_queue.run_daily(midday_check, time=dtime(12, 30, tzinfo=ET))
        app.job_queue.run_daily(eod_wrap, time=dtime(21, 0, tzinfo=ET))  # 9pm: wrap + habit check-in + journal
        app.job_queue.run_daily(week_start_roundup, time=dtime(17, 0, tzinfo=ET))  # gated to Sunday
        app.job_queue.run_daily(week_end_roundup, time=dtime(17, 0, tzinfo=ET))    # gated to Friday
        app.job_queue.run_daily(inbox_calendar_sync, time=dtime(8, 0, tzinfo=ET))
        app.job_queue.run_daily(inbox_calendar_sync, time=dtime(18, 5, tzinfo=ET))
        # Gmail auth health check: verify on startup, then daily.
        app.job_queue.run_once(gmail_healthcheck, when=60)
        app.job_queue.run_daily(
            gmail_healthcheck,
            time=dtime(HEALTHCHECK_HOUR, 0, tzinfo=ZoneInfo("America/New_York")),
        )
        logger.info("Pipeline poll every %s min; daily digest at %02d:00 ET; "
                    "interview watch hourly; Gmail health check at %02d:00 ET + startup",
                    PIPELINE_POLL_MINUTES, PIPELINE_DIGEST_HOUR, HEALTHCHECK_HOUR)
    else:
        logger.warning("JobQueue unavailable — install python-telegram-bot[job-queue] "
                       "to enable Gmail pipeline ingestion")

    logger.info("Daily OS bot polling on model %s... Ctrl+C to stop.", CLAUDE_MODEL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
