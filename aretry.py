"""Tiny dependency-free async retry helper for flaky network calls (Notion /
Telegram / Google APIs). Pure stdlib so it stays unit-testable without the live
clients.

Usage:
    from aretry import aretry
    pages = await aretry(lambda: notion.data_sources.query(**kw), label="get_tasks")

`fn` is a zero-arg factory that returns a fresh awaitable each attempt (pass a
lambda, not an already-created coroutine, so it can be re-awaited on retry).
"""
import asyncio
import logging

logger = logging.getLogger("daily_os_bot")

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5  # seconds; doubles each retry


async def aretry(
    fn,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    retry_on: tuple = (Exception,),
    label: str = "call",
    sleep=asyncio.sleep,
):
    """Await ``fn()``, retrying transient failures with exponential backoff.

    Returns ``fn()``'s result on success. Re-raises the last exception once all
    attempts are exhausted. ``sleep`` is injectable so tests run instantly.
    """
    attempts = max(1, attempts)
    last = None
    for i in range(attempts):
        try:
            return await fn()
        except retry_on as e:
            last = e
            if i == attempts - 1:
                break
            delay = base_delay * (2 ** i)
            logger.warning(
                "aretry: %s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, i + 1, attempts, type(e).__name__, delay,
            )
            await sleep(delay)
    raise last
