"""Browser sessions, and the CSRF check for requests that change something.

Signing in gives the browser two cookies:
- `__Host-wj_session` holds a random token. It's HTTP-only, so page scripts can't read it, and
  the `sessions` table stores only the token's SHA-256 hash.
- `__Host-wj_csrf` holds the session's CSRF token. The web app reads it and sends it back in the
  `X-CSRF-Token` header on every request that changes something. Another site can make a browser
  send the cookies, but it can't read them, so it can't set the header.

Both are Secure and SameSite=Lax. With the `__Host-` prefix, browsers accept them only from this
host over HTTPS, so a neighbouring subdomain can't plant its own.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User, UserSession
from app.deps import AppSettings, Db

SESSION_COOKIE = "__Host-wj_session"
CSRF_COOKIE = "__Host-wj_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_LIFETIME = timedelta(days=30)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SignedIn(NamedTuple):
    session: UserSession
    user: User


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def is_allowed(login: str | None, settings: Settings) -> bool:
    """Whether a GitHub username is on the allowlist. GitHub ignores case in usernames."""
    return login is not None and login.lower() in settings.allowed_github_logins


def start_session(db: AsyncSession, user_id: uuid.UUID) -> tuple[str, UserSession]:
    """Add a session and return the token for its cookie. The caller commits."""
    token = secrets.token_urlsafe(32)
    session = UserSession(
        token_hash=hash_token(token),
        user_id=user_id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + SESSION_LIFETIME,
    )
    db.add(session)
    return token, session


async def find_session(db: AsyncSession, token: str) -> SignedIn | None:
    """The unexpired session with this token, and its user."""
    query = (
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > func.now())
    )
    row = (await db.execute(query)).first()
    return None if row is None else SignedIn(row[0], row[1])


async def end_session(db: AsyncSession, token: str) -> None:
    """Delete the session with this token, if there is one. The caller commits."""
    await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


async def delete_expired_sessions(db: AsyncSession) -> None:
    await db.execute(delete(UserSession).where(UserSession.expires_at <= func.now()))


def set_session_cookies(response: Response, token: str, csrf_token: str) -> None:
    max_age = int(SESSION_LIFETIME.total_seconds())
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max_age, secure=True, httponly=True, samesite="lax"
    )
    response.set_cookie(
        CSRF_COOKIE, csrf_token, max_age=max_age, secure=True, httponly=False, samesite="lax"
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="lax")


def check_csrf(request: Request, session: UserSession) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    if not secrets.compare_digest(sent.encode(), session.csrf_token.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing or wrong {CSRF_HEADER} header")


async def current_user(request: Request, db: Db, settings: AppSettings) -> User:
    """The signed-in user, still on the allowlist. Requests that change something must also
    carry the session's CSRF token."""
    token = request.cookies.get(SESSION_COOKIE)
    found = await find_session(db, token) if token else None
    if found is None or not is_allowed(found.user.github_login, settings):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if request.method not in SAFE_METHODS:
        check_csrf(request, found.session)
    return found.user


CurrentUser = Annotated[User, Depends(current_user)]
"""Add `user: CurrentUser` to a route that acts for the signed-in user."""
