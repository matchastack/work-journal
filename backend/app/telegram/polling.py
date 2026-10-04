"""Fetching updates from Telegram instead of receiving them, for journaling on this computer.

Telegram offers a bot's updates one way at a time: to the webhook, or to `getUpdates`. Polling
turns the webhook off first; `wj telegram set-webhook` turns it back on.
"""

import json
import logging
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
) -> int:
    """Journal the bot's updates as the webhook would, and send its replies. Asks Telegram
    `rounds` times, or until cancelled. Returns how many updates it handled."""
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
                    reply = await handle_update(
                        session, json.dumps(update), entry_timeout=entry_timeout
                    )
                except ValidationError:
                    logger.warning("Telegram sent an update the bot can't read; it was skipped")
                    continue
            if reply is not None:
                await api.send_message(reply.chat_id, reply.text)
            handled += 1
    return handled
