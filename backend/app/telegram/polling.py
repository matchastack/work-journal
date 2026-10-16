"""Fetching updates from Telegram instead of receiving them, for journaling on this computer.

Telegram offers a bot's updates one way at a time: to the webhook, or to `getUpdates`. Polling
sets the bot's command menu and turns the webhook off first; `wj telegram set-webhook` turns it
back on. As with the webhook, an update that closes an entry or chooses its role has the entry's
jobs run right away (`run_jobs`), `/refresh` has its refresh's job run (`run_refresh`), and an
update that moves the catch-up interview on has its next question asked (`run_catch_up`).
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
from app.telegram.journal import COMMANDS, handle_update

logger = logging.getLogger(__name__)


async def poll(
    api: BotApi,
    sessions: async_sessionmaker[AsyncSession],
    *,
    rounds: int | None = None,
    timeout: int = POLL_TIMEOUT_S,
    entry_timeout: timedelta = DEFAULT_TIMEOUT,
    app_url: str | None = None,
    run_jobs: Callable[[uuid.UUID], Awaitable[object]] | None = None,
    run_refresh: Callable[[uuid.UUID], Awaitable[object]] | None = None,
    run_catch_up: Callable[[uuid.UUID], Awaitable[object]] | None = None,
) -> int:
    """Journal the bot's updates as the webhook would, and send its replies. When an update
    closes an entry or chooses its role, `run_jobs` gets the entry, `run_refresh` gets the
    refresh `/refresh` starts, and `run_catch_up` gets the catch-up interview it moves on. Asks
    Telegram `rounds` times, or until cancelled. Returns how many updates it handled."""
    await api.set_my_commands(COMMANDS)
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
                        session, json.dumps(update), entry_timeout=entry_timeout, app_url=app_url
                    )
                except ValidationError:
                    logger.warning("Telegram sent an update the bot can't read; it was skipped")
                    continue
            for call in result.calls:
                await api.make(call)
            if result.ready is not None and run_jobs is not None:
                await run_jobs(result.ready)
            if result.refresh is not None and run_refresh is not None:
                await run_refresh(result.refresh)
            if result.catch_up is not None and run_catch_up is not None:
                await run_catch_up(result.catch_up)
            handled += 1
    return handled
