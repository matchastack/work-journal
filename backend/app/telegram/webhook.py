"""`POST /telegram/webhook`: where Telegram sends each update (FR-CAP-1).

Telegram resends an update until the webhook answers with success, so the update is stored
before the answer, and storing it twice does nothing (NFR-REL-1). The answer can carry the bot's
reply as a Bot API call, which Telegram makes itself, so replying needs no request of its own.
Any further calls, such as editing a message after a button press, are made right after the
answer. When the update closes an entry or chooses its role, the entry's jobs run right after
that (FR-CAP-5), and after `/refresh`, the refresh's job (FR-REV-6).
"""

import logging
import secrets
from collections.abc import Sequence
from datetime import timedelta

import httpx2
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr, ValidationError

from app.deps import AppSettings, Db
from app.telegram.api import BotApi, BotCall, TelegramError
from app.telegram.journal import handle_update
from app.tick import extract_now, refresh_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request, db: Db, settings: AppSettings, background: BackgroundTasks
) -> Response:
    """Store an update from Telegram, if it carries the webhook's secret (NFR-SEC-4)."""
    expected = settings.telegram_webhook_secret
    given = request.headers.get(SECRET_HEADER, "")
    if expected is None or not secrets.compare_digest(
        given.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown sender")
    timeout = timedelta(minutes=settings.entry_timeout_minutes)
    try:
        handled = await handle_update(
            db, (await request.body()).decode(), entry_timeout=timeout, app_url=settings.app_url
        )
    except (UnicodeDecodeError, ValidationError):
        logger.warning("Telegram sent an update the bot can't read; it was skipped")
        return Response(status_code=status.HTTP_200_OK)
    await db.commit()
    if handled.calls[1:]:
        background.add_task(
            make_calls, settings.telegram_bot_token, request.app.state.http, handled.calls[1:]
        )
    if handled.ready is not None:
        background.add_task(extract_now, handled.ready)
    if handled.refresh is not None:
        background.add_task(refresh_now, handled.refresh)
    if not handled.calls:
        return Response(status_code=status.HTTP_200_OK)
    return JSONResponse(handled.calls[0].webhook_body())


async def make_calls(
    token: SecretStr | None, http: httpx2.AsyncClient, calls: Sequence[BotCall]
) -> None:
    """Make the calls the webhook's answer can't carry. One that fails is logged and skipped:
    Telegram won't send the update again, and the calls change nothing that's stored."""
    if token is None:
        logger.warning("TELEGRAM_BOT_TOKEN isn't set, so the bot can't finish its answer")
        return
    api = BotApi(token, http)
    for call in calls:
        try:
            await api.make(call)
        except TelegramError as error:
            logger.warning("couldn't make the bot's %s call: %s", call.method, error)
