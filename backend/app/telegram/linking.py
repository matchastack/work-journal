"""Linking a Telegram chat to a user, with a one-time link to the bot (FR-CAP-3, FR-SET-1).

The web app, or `wj telegram link`, makes a link such as `https://t.me/<bot>?start=<token>`.
Opening it in Telegram sends `/start <token>` to the bot, which links that chat to the token's
user. A token works once, for 15 minutes, and only its hash is stored. Linking another chat moves
the user's link there.
"""

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import hash_token
from app.db.models import TelegramLink, TelegramLinkToken

TOKEN_LIFETIME = timedelta(minutes=15)
START_WITH_TOKEN = re.compile(r"^/start(?:@\w+)?\s+([A-Za-z0-9_-]{1,64})\s*$")
"""`/start` followed by a link's token: Telegram allows up to 64 of these characters."""

LINKED = (
    "Linked. Send me notes about your work whenever you like, and I'll keep them in your journal."
)
ALREADY_LINKED = "This chat is already linked to your journal."
LINKED_ELSEWHERE = "This chat is linked to another Work Journal account."
EXPIRED = (
    "This link has expired or was already used. Make a new one from your Work Journal account."
)


async def new_link_token(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[str, datetime]:
    """A new one-time token for `user_id`, and when it expires."""
    token = secrets.token_urlsafe(24)
    expires_at = (now or datetime.now(UTC)) + TOKEN_LIFETIME
    session.add(
        TelegramLinkToken(token_hash=hash_token(token), user_id=user_id, expires_at=expires_at)
    )
    await session.flush()
    return token, expires_at


def deep_link(bot_username: str, token: str) -> str:
    """The link that opens the bot in Telegram and sends it `/start <token>`."""
    return f"https://t.me/{bot_username}?start={token}"


def start_token(text: str) -> str | None:
    """The token in a `/start <token>` message, if it is one."""
    match = START_WITH_TOKEN.match(text)
    return match.group(1) if match else None


async def link_chat(
    session: AsyncSession, chat_id: int, token: str, *, now: datetime | None = None
) -> str:
    """Link `chat_id` to the user the token was made for, using the token up. Returns the bot's
    answer."""
    row = await session.get(TelegramLinkToken, hash_token(token))
    if row is None:
        return EXPIRED
    await session.delete(row)
    if row.expires_at <= (now or datetime.now(UTC)):
        return EXPIRED
    owner = await session.scalar(
        select(TelegramLink.user_id).where(TelegramLink.chat_id == chat_id)
    )
    if owner == row.user_id:
        return ALREADY_LINKED
    if owner is not None:
        return LINKED_ELSEWHERE
    await session.execute(delete(TelegramLink).where(TelegramLink.user_id == row.user_id))
    session.add(TelegramLink(user_id=row.user_id, chat_id=chat_id))
    await session.flush()
    return LINKED


async def linked_at(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """When the user's chat was linked, or None when no chat is."""
    return await session.scalar(
        select(TelegramLink.linked_at).where(TelegramLink.user_id == user_id)
    )
