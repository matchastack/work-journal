"""Sign-in routes: `/auth/login`, `/auth/callback`, `/auth/me` and `/auth/logout`."""

import logging
import secrets
import uuid
from functools import partial
from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.github import GitHubAccount, GitHubError, GitHubOAuth
from app.auth.sessions import (
    SESSION_COOKIE,
    CurrentUser,
    check_csrf,
    clear_session_cookies,
    cookie_name,
    delete_expired_sessions,
    end_session,
    find_session,
    is_allowed,
    set_session_cookies,
    start_session,
)
from app.config import Settings
from app.db.models import User
from app.deps import AppSettings, Db
from app.schema.common import Model

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "__Host-wj_oauth"
"""The sign-in in progress: its `state` and PKCE verifier, for ten minutes (as long as GitHub's
codes last)."""
STATE_MAX_AGE_S = 600
NO_STORE = {"Cache-Control": "no-store"}
PAGE_HEADERS = {
    **NO_STORE,
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


class Me(Model):
    id: uuid.UUID
    github_login: str | None


def github_oauth(request: Request, settings: AppSettings) -> GitHubOAuth:
    if not settings.github_client_id or settings.github_client_secret is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GitHub sign-in isn't set up: set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET",
        )
    return GitHubOAuth(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        redirect_uri=f"{settings.app_url}/auth/callback",
        http=request.app.state.http,
    )


GitHub = Annotated[GitHubOAuth, Depends(github_oauth)]


@router.get("/login", response_class=RedirectResponse, status_code=status.HTTP_303_SEE_OTHER)
async def login(github: GitHub, settings: AppSettings) -> Response:
    """Start signing in: send the browser to GitHub with a new `state` and PKCE challenge."""
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    response = RedirectResponse(
        github.authorize_url(state, verifier), status.HTTP_303_SEE_OTHER, headers=NO_STORE
    )
    response.set_cookie(
        cookie_name(STATE_COOKIE, settings),
        f"{state}.{verifier}",
        max_age=STATE_MAX_AGE_S,
        secure=settings.https,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback", response_class=HTMLResponse)
async def callback(
    request: Request,
    github: GitHub,
    db: Db,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish signing in when GitHub sends the browser back, then go to the web app."""
    started = request.cookies.get(cookie_name(STATE_COOKIE, settings), "")
    expected, _, verifier = started.partition(".")
    if not (state and expected and secrets.compare_digest(state.encode(), expected.encode())):
        # Not the sign-in this browser started: it expired, or another site sent the browser.
        return _page(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in expired",
            "This sign-in wasn't started in this browser in the last ten minutes. Start again.",
            retry=True,
        )
    response = await _finish(request, github, db, settings, code, verifier, error)
    response.delete_cookie(
        cookie_name(STATE_COOKIE, settings), secure=settings.https, httponly=True, samesite="lax"
    )
    return response


@router.get("/me")
async def me(user: CurrentUser, response: Response) -> Me:
    """Who is signed in. Answers 401 when nobody is."""
    response.headers.update(NO_STORE)
    return Me(id=user.id, github_login=user.github_login)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: Db, settings: AppSettings) -> Response:
    """Sign this browser out. While the session is valid, this needs the CSRF header."""
    token = request.cookies.get(cookie_name(SESSION_COOKIE, settings))
    if token and (found := await find_session(db, token)):
        check_csrf(request, found.session)
        await end_session(db, token)
        await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
    clear_session_cookies(response, settings)
    return response


async def _finish(
    request: Request,
    github: GitHubOAuth,
    db: AsyncSession,
    settings: Settings,
    code: str | None,
    verifier: str,
    error: str | None,
) -> Response:
    if error == "access_denied":
        return _page(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in cancelled",
            "You didn't approve signing in on GitHub.",
            retry=True,
        )
    if error or not code:
        logger.warning("GitHub sign-in failed: GitHub sent back %r", (error or "no code")[:64])
        return _page(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in failed",
            f"GitHub sent back an error: {error or 'no code'}.",
            retry=True,
        )
    try:
        account = await github.account(code, verifier)
    except GitHubError as failure:
        logger.warning("GitHub sign-in failed: %s", failure)
        return _page(
            status.HTTP_502_BAD_GATEWAY,
            "Sign-in failed",
            "GitHub didn't confirm who you are. Try again in a minute.",
            retry=True,
        )
    if not is_allowed(account.login, settings):
        logger.info("Refused a sign-in from a GitHub account that isn't on the allowlist")
        return _page(
            status.HTTP_403_FORBIDDEN,
            "This journal is private",
            f"Only the GitHub accounts on this app's allowlist can sign in, and @{account.login}"
            " isn't one of them. If this is your app, add the username to ALLOWED_GITHUB_LOGINS.",
        )
    user_id = await _upsert_user(db, account)
    if previous := request.cookies.get(cookie_name(SESSION_COOKIE, settings)):
        await end_session(db, previous)
    await delete_expired_sessions(db)
    token, session = start_session(db, user_id)
    await db.commit()
    logger.info("User %s signed in with GitHub", user_id)
    response = RedirectResponse("/", status.HTTP_303_SEE_OTHER, headers=NO_STORE)
    set_session_cookies(response, token, session.csrf_token, settings)
    return response


async def _upsert_user(db: AsyncSession, account: GitHubAccount) -> uuid.UUID:
    """The user for this GitHub account, created at the first sign-in. The username is updated,
    as GitHub lets people change it; the account number stays the same."""
    query = (
        insert(User)
        .values(github_id=account.id, github_login=account.login)
        .on_conflict_do_update(
            index_elements=[User.github_id], set_={"github_login": account.login}
        )
        .returning(User.id)
    )
    return (await db.execute(query)).scalar_one()


def _page(status_code: int, title: str, message: str, *, retry: bool = False) -> HTMLResponse:
    """A short page for the browser, which arrives here from GitHub rather than the web app."""
    again = '\n<p><a href="/auth/login">Try again</a></p>' if retry else ""
    text = partial(escape, quote=False)  # the values go between tags, never into attributes
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(title)} | Work Journal</title>
<style>
  body {{ max-width: 36rem; margin: 4rem auto; padding: 0 1rem; }}
  body {{ font: 1rem/1.5 system-ui, sans-serif; }}
</style>
</head>
<body>
<main>
<h1>{text(title)}</h1>
<p>{text(message)}</p>{again}
</main>
</body>
</html>
"""
    return HTMLResponse(html, status_code, headers=PAGE_HEADERS)
