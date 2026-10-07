"""`POST /telegram/webhook`: where Telegram sends each update (FR-CAP-1).

Telegram resends an update until the webhook answers with success, so the update is stored
before the answer, and storing it twice does nothing (NFR-REL-1). The answer can carry the bot's
reply as a Bot API call, which Telegram makes itself, so replying needs no request of its own.
"""

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.deps import AppSettings, Db
from app.telegram.journal import handle_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: Db, settings: AppSettings) -> Response:
    """Store an update from Telegram, if it carries the webhook's secret (NFR-SEC-4)."""
    expected = settings.telegram_webhook_secret
    given = request.headers.get(SECRET_HEADER, "")
    if expected is None or not secrets.compare_digest(
        given.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown sender")
    timeout = timedelta(minutes=settings.entry_timeout_minutes)
    try:
        reply = await handle_update(db, (await request.body()).decode(), entry_timeout=timeout)
    except (UnicodeDecodeError, ValidationError):
        logger.warning("Telegram sent an update the bot can't read; it was skipped")
        return Response(status_code=status.HTTP_200_OK)
    await db.commit()
    if reply is None:
        return Response(status_code=status.HTTP_200_OK)
    return JSONResponse(reply.webhook_body())
