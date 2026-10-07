"""Fetching updates from Telegram instead of receiving them, for journaling on this computer.

Telegram offers a bot's updates one way at a time: to the webhook, or to `getUpdates`. Polling
turns the webhook off first; `wj telegram set-webhook` turns it back on. As with the webhook, an
update that closes an entry has its extraction run right away (`after_close`).
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.telegram.api import POLL_TIMEOUT_S, BotApi
from app.telegram.entries import DEFAULT_TIMEOUT
from app.telegram.journal import handle_update

logger = logging.getLogger(__name__)


async def poll(
    api: BotApi,
    sessions: async_sessionmaker[AsyncSession],
    *,
    rounds: int | None = None,
    timeout: int = POLL_TIMEOUT_S,
    entry_timeout: timedelta = DEFAULT_TIMEOUT,
    after_close: Callable[[uuid.UUID], Awaitable[object]] | None = None,
) -> int:
    """Journal the bot's updates as the webhook would, and send its replies. When an update
    closes an entry, `after_close` gets it. Asks Telegram `rounds` times, or until cancelled.
    Returns how many updates it handled."""
    await api.delete_webhook()
    offset: int | None = None
    handled = 0
    asked = 0
    while rounds is None or asked < rounds:
        asked += 1
        for update in await api.get_updates(offset, timeout):
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            offset = update_id + 1
            async with sessions() as session, session.begin():
                try:
                    result = await handle_update(
                        session, json.dumps(update), entry_timeout=entry_timeout
                    )
                except ValidationError:
                    logger.warning("Telegram sent an update the bot can't read; it was skipped")
                    continue
            if result.reply is not None:
                await api.send_message(result.reply.chat_id, result.reply.text)
            if result.closed is not None and after_close is not None:
                await after_close(result.closed)
            handled += 1
    return handled
