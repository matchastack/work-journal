"""Signing in, sessions and the CSRF check, against the database and a fake GitHub."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from http.cookies import Morsel, SimpleCookie
from typing import Any

import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Executable, insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.fake import FakeGitHub
from app.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, CurrentUser
from app.config import Settings
from app.db.models import User, UserSession
from app.main import create_app

BROWSER = "https://testserver"
OWNER = "casey-example"


def app_for(database_url: str, github: FakeGitHub, *allowed: str) -> FastAPI:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        github_client_id=github.client_id,
        github_client_secret=SecretStr(github.client_secret),
        allowed_github_logins=frozenset(allowed or {OWNER}),
    )
    api = create_app(settings, transport=github.transport)

    @api.post("/test/change")
    async def change(user: CurrentUser) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Stands in for the routes that will change things for the signed-in user."""
        return {"user": str(user.id)}

    return api


def run(database_url: str, statement: Executable) -> list[Any]:
    """Run one statement in its own transaction and return its rows."""

    async def execute() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(statement)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def sessions_of(database_url: str, github_id: int) -> list[Any]:
    user = select(User.id).where(User.github_id == github_id).scalar_subquery()
    query = select(UserSession.token_hash, UserSession.csrf_token).where(
        UserSession.user_id == user
    )
    return run(database_url, query)


def me_with(
    database_url: str, github: FakeGitHub, token: str, *, allowed: str = OWNER
) -> httpx2.Response:
    """`/auth/me` from another browser that has only this session token."""
    api = app_for(database_url, github, allowed)
    with TestClient(api, base_url=BROWSER, cookies={SESSION_COOKIE: token}) as client:
        return client.get("/auth/me")


def set_cookie(response: httpx2.Response, name: str) -> Morsel[str]:
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        if name in cookie:
            return cookie[name]
    raise AssertionError(f"the response doesn't set {name}")


def test_signing_in_creates_the_user_and_a_session(database_url: str) -> None:
    github = FakeGitHub()
    with TestClient(app_for(database_url, github), base_url=BROWSER) as client:
        response = github.sign_in(client, 3001, "Casey-Example")
        me = client.get("/auth/me")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session, csrf = set_cookie(response, SESSION_COOKIE), set_cookie(response, CSRF_COOKIE)
    assert (session["httponly"], session["secure"], session["samesite"]) == (True, True, "lax")
    assert (csrf["httponly"], csrf["secure"], csrf["samesite"]) == ("", True, "lax")
    assert session["path"] == csrf["path"] == "/"
    assert session["max-age"] == csrf["max-age"] == str(30 * 24 * 3600)
    assert me.status_code == 200
    assert me.headers["cache-control"] == "no-store"
    [user] = run(database_url, select(User).where(User.github_id == 3001))
    assert me.json() == {"id": str(user.id), "githubLogin": "Casey-Example"}
    [stored] = sessions_of(database_url, 3001)
    assert stored.token_hash == hashlib.sha256(session.value.encode()).digest()
    assert stored.csrf_token == csrf.value


def test_signing_in_again_finds_the_same_user_and_replaces_the_session(
    database_url: str,
) -> None:
    github = FakeGitHub()
    api = app_for(database_url, github, OWNER, "casey-renamed")
    with TestClient(api, base_url=BROWSER) as client:
        github.sign_in(client, 3002, OWNER)
        first = client.get("/auth/me").json()
        old_token = client.cookies[SESSION_COOKIE]
        github.sign_in(client, 3002, "casey-renamed")
        second = client.get("/auth/me").json()
    assert second == {"id": first["id"], "githubLogin": "casey-renamed"}
    assert len(sessions_of(database_url, 3002)) == 1
    assert me_with(database_url, github, old_token).status_code == 401


def test_a_state_works_once(database_url: str) -> None:
    github = FakeGitHub()
    with TestClient(app_for(database_url, github), base_url=BROWSER) as client:
        start = client.get("/auth/login", follow_redirects=False)
        callback = github.approve(start.headers["location"], 3003, OWNER)
        assert client.get(callback, follow_redirects=False).status_code == 303
        assert client.get(callback, follow_redirects=False).status_code == 400


def test_changes_need_the_csrf_token_but_reads_do_not(database_url: str) -> None:
    github = FakeGitHub()
    with (
        TestClient(app_for(database_url, github), base_url=BROWSER) as client,
        TestClient(app_for(database_url, github), base_url=BROWSER) as other,
    ):
        github.sign_in(client, 3004, OWNER)
        github.sign_in(other, 3004, OWNER)
        token, others = client.cookies[CSRF_COOKIE], other.cookies[CSRF_COOKIE]
        missing = client.post("/test/change")
        from_another_session = client.post("/test/change", headers={CSRF_HEADER: others})
        right = client.post("/test/change", headers={CSRF_HEADER: token})
        read = client.get("/auth/me")
    assert missing.status_code == 403
    assert missing.json() == {"detail": f"Missing or wrong {CSRF_HEADER} header"}
    assert from_another_session.status_code == 403
    assert right.status_code == 200
    assert read.status_code == 200


def test_logout_needs_the_csrf_token_and_ends_the_session(database_url: str) -> None:
    github = FakeGitHub()
    with TestClient(app_for(database_url, github), base_url=BROWSER) as client:
        github.sign_in(client, 3005, OWNER)
        token = client.cookies[CSRF_COOKIE]
        refused = client.post("/auth/logout", headers={CSRF_HEADER: "wrong"})
        still_in = client.get("/auth/me")
        logout = client.post("/auth/logout", headers={CSRF_HEADER: token})
        after = client.get("/auth/me")
    assert refused.status_code == 403
    assert still_in.status_code == 200
    assert logout.status_code == 204
    assert set_cookie(logout, SESSION_COOKIE)["max-age"] == "0"
    assert after.status_code == 401
    assert sessions_of(database_url, 3005) == []


def test_an_expired_session_is_signed_out(database_url: str) -> None:
    github = FakeGitHub()
    with TestClient(app_for(database_url, github), base_url=BROWSER) as client:
        github.sign_in(client, 3006, OWNER)
        user = select(User.id).where(User.github_id == 3006).scalar_subquery()
        expire = (
            update(UserSession)
            .where(UserSession.user_id == user)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        run(database_url, expire)
        response = client.get("/auth/me")
    assert response.status_code == 401


def test_taking_a_login_off_the_allowlist_signs_it_out(database_url: str) -> None:
    github = FakeGitHub()
    with TestClient(app_for(database_url, github), base_url=BROWSER) as client:
        github.sign_in(client, 3007, OWNER)
        token = client.cookies[SESSION_COOKIE]
    assert me_with(database_url, github, token).status_code == 200
    assert me_with(database_url, github, token, allowed="someone-else").status_code == 401


def test_the_first_sign_in_claims_a_user_made_ahead_of_it(database_url: str) -> None:
    """`wj db load-profile --user` can make the user before anyone signs in, with just the
    username; the profile loaded for it must end up with the GitHub account."""
    [made] = run(database_url, insert(User).values(github_login="Casey-Early").returning(User.id))
    github = FakeGitHub()
    with TestClient(app_for(database_url, github, "casey-early"), base_url=BROWSER) as client:
        github.sign_in(client, 3101, "casey-early")
        me = client.get("/auth/me").json()
        github.sign_in(client, 3101, "casey-early")
        again = client.get("/auth/me").json()
    assert me == again == {"id": str(made.id), "githubLogin": "casey-early"}
    [user] = run(database_url, select(User).where(User.id == made.id))
    assert user.github_id == 3101
