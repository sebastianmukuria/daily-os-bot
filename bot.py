import os
import re
import json
import html
import time
import asyncio
import logging
from datetime import datetime
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
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "5384689298"))

MAX_TOOL_ITERATIONS = 8  # cap the agentic loop so it can't run away on tokens
TELEGRAM_MAX_CHARS = 4096
# Switch models without code changes: set CLAUDE_MODEL in the environment.
# Default Haiku (cheap, fast). Bump to claude-sonnet-4-6 for stronger reasoning.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory conversation history per chat
conversation_history: dict[int, list] = {}

SYSTEM_PROMPT = """You are Sebastian's personal AI assistant, embedded in his Daily OS Telegram bot. Sebastian has ADHD.

Rules for how you respond:
- Smallest-commit framing: say "open the draft" not "finish the essay", "send one text" not "resolve the conflict"
- When listing tasks, order by Energy: High → Medium → Low. Flag stale tasks (not updated in 3+ days) with ⚠️
- Be concise — this is Telegram. Use bullet points, avoid walls of text.
- Give 1-3 next actions max. Never overwhelm.
- When someone says "add: X" parse it as a task creation. "done: X" = complete task. "idea: X" = add idea.

Always enrich new tasks (never leave them bare):
- Energy: infer High / Medium / Low from how cognitively demanding the task is — don't just default to Medium.
- Type: set it. Usually "Task"; use "Appointment" for things with a set time/place, "Admin/Inbox" for quick admin.
- Project: if the task clearly belongs to one of Sebastian's active projects (listed near the end of this prompt), pass that project's name to link it.
- (Status starts as Not Started automatically.)

Naming things — always polish the title and add an emoji:
- Whenever you create a task, calendar event, idea, project, or reading-list item, never use Sebastian's raw phrasing verbatim. Rewrite it into a concise, title-cased label and ALWAYS include a relevant emoji (placed at the end).
- Examples: "go to the gym at 8am" → "Gym Session 🏋️"; "call dentist about the crown" → "Dentist Call 🦷"; "write the q3 essay" → "Q3 Essay ✍️"; "buy groceries" → "Grocery Run 🛒"; "find a good book to read" → "Find a Book 📚".
- Keep the real meaning and any important specifics — just make it cleaner and shorter.
- When one request creates BOTH a task and a calendar event, use the SAME polished title (with emoji) for both.

Report outcomes honestly and specifically:
- After using tools, tell Sebastian exactly what happened with EACH action — what worked and what didn't. Use ✓ for success and ✗ for failure.
- If a tool returns an "error" field, that action FAILED. Say so plainly and include the actual reason (paraphrase the error briefly), e.g. "✗ Couldn't add to calendar — the Google token is invalid." Never call a failure "finicky", never gloss over it, and never imply something was saved when the tool returned an error.
- If part of a multi-step request succeeds and part fails, list each result separately so it's clear what still needs doing.

You have access to:
- Notion Tasks DB (get, create, complete, edit tasks)
- Notion Ideas DB (add ideas)
- Notion Projects DB (get projects, add projects — useful for check-ins)
- Notion Reading List DB (get list, add books/articles/papers/videos/podcasts)
- Google Calendar (view upcoming events, create new events, edit existing events)
- Habits tracker (show habits, mark a habit done for today, add a habit)
- Web search (look up current info, facts, and event details)

Habits vs. tasks:
- Habits are recurring things he wants to do regularly (gym, vitamins, meditation, journaling). They live in a separate Habits tracker — NOT the Tasks DB. Never create a task for a recurring habit.
- When he says he did one ("took my vitamins", "hit the gym", "meditated"), call log_habit to check it off and bump the streak. Celebrate the streak briefly.
- "add a habit" / "track X daily" → add_habit. "what are my habits / did I do them?" → get_habits.

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
        assistant_text = f"⚠️ Something broke: {type(e).__name__}. I reset our conversation — try again."

    # Always reply with something, even if Claude returned no text after a tool call.
    await send_reply(update, assistant_text or "✅ Done.")


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
        "• What's on my calendar this week?"
    )


async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    conversation_history.pop(update.effective_chat.id, None)
    await update.message.reply_text("Conversation cleared.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all so an unhandled error logs instead of printing a bare traceback."""
    logger.error("Unhandled error", exc_info=context.error)


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)
    logger.info("Daily OS bot polling on model %s... Ctrl+C to stop.", CLAUDE_MODEL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
