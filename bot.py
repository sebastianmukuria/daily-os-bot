import os
import re
import json
import html
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
from tools import TOOLS, execute_tool

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
- Notion Tasks DB (get, create, complete tasks)
- Notion Ideas DB (add ideas)
- Notion Projects DB (get projects, add projects — useful for check-ins)
- Notion Reading List DB (get list, add books/articles/papers/videos/podcasts)
- Google Calendar (view upcoming events, create new events, edit existing events)

When creating calendar events, infer the date/time from context and the current date provided.
Times are Eastern. If no end time is given, a 1-hour default is fine.

Editing vs. creating events — don't make duplicates:
- If Sebastian asks to change or add a detail to an event that already exists (add a location, move the time, rename it), UPDATE that event with update_calendar_event. Never create a second event for the same thing.
- If you just created the event this conversation, reuse the id you got back. Otherwise call get_calendar_events to find the right event and its id first.

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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    base_history = conversation_history.get(chat_id, [])

    et = pytz.timezone("America/New_York")
    now_str = datetime.now(et).strftime("%A, %B %d, %Y %I:%M %p ET")
    system = f"{SYSTEM_PROMPT}\n\nCurrent date/time: {now_str}"

    messages = base_history + [{"role": "user", "content": user_text}]

    assistant_text = ""

    try:
        # Agentic tool loop, capped so it can't run away on tokens.
        for _ in range(MAX_TOOL_ITERATIONS):
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                system=system,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })

                messages.append({"role": "user", "content": tool_results})
                continue  # loop back for Claude to use the results

            # Any non-tool stop reason (end_turn, max_tokens, etc.) ends the turn.
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
    logger.info("Daily OS bot polling... Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
