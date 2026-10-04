"""`/api/telegram/link`: link the signed-in user's Telegram chat (FR-CAP-3, FR-SET-1)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import AppSettings, Db
from app.schema.common import Model
from app.telegram.api import BotApi, TelegramError
from app.telegram.linking import deep_link, linked_at, new_link_token

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramLinkStatus(Model):
    linked: bool
    linked_at: datetime | None


class TelegramDeepLink(Model):
    url: str
    """Opens the bot in Telegram, which then links that chat. It works once."""
    expires_at: datetime


@router.get("/link")
async def link_status(user: CurrentUser, db: Db, response: Response) -> TelegramLinkStatus:
    """Whether the user's Telegram chat is linked, and since when."""
    response.headers.update(NO_STORE)
    since = await linked_at(db, user.id)
    return TelegramLinkStatus(linked=since is not None, linked_at=since)


@router.post("/link")
async def new_link(
    user: CurrentUser, db: Db, settings: AppSettings, request: Request, response: Response
) -> TelegramDeepLink:
    """A one-time link to the bot that links the chat it's opened in, for 15 minutes."""
    response.headers.update(NO_STORE)
    token = settings.telegram_bot_token
    if token is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The bot isn't set up")
    try:
        bot = await BotApi(token, request.app.state.http).get_me()
    except TelegramError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram can't be reached; try again"
        ) from None
    link_token, expires_at = await new_link_token(db, user.id)
    await db.commit()
    return TelegramDeepLink(url=deep_link(bot.username, link_token), expires_at=expires_at)
