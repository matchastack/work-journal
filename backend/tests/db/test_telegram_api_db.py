"""`/api/telegram/link` for the signed-in user, with a stand-in for Telegram."""

import asyncio
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Executable, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, hash_token, start_session
from app.config import Settings
from app.db.models import TelegramLink, TelegramLinkToken, User
from app.main import create_app

BROWSER = "https://testserver"
_random = random.SystemRandom()


class Owner:
    """A signed-in user of the test's own."""

    def __init__(self, database_url: str) -> None:
        self.login = f"owner-{uuid.uuid4().hex[:8]}"

        async def sign_in() -> tuple[uuid.UUID, str, str]:
            engine = create_async_engine(database_url)
            try:
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                async with sessions() as session, session.begin():
                    github_id = _random.randrange(10**9, 10**12)
                    user = User(github_id=github_id, github_login=self.login)
                    session.add(user)
                    await session.flush()
                    token, row = start_session(session, user.id)
                    return user.id, token, row.csrf_token
            finally:
                await engine.dispose()

        self.id, self.session_token, self.csrf_token = asyncio.run(sign_in())


def telegram_bot(request: httpx2.Request) -> httpx2.Response:
    assert request.url.path.endswith("/getMe")
    bot = {"id": 99, "is_bot": True, "first_name": "Journal", "username": "wj_example_bot"}
    return httpx2.Response(200, json={"ok": True, "result": bot})


def client_for(
    database_url: str,
    owner: Owner,
    telegram: Callable[[httpx2.Request], httpx2.Response] = telegram_bot,
    *,
    bot_token: str | None = "123456:test-token-not-real",
) -> TestClient:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset({owner.login}),
        telegram_bot_token=SecretStr(bot_token) if bot_token else None,
    )
    api = create_app(settings, transport=httpx2.MockTransport(telegram))
    return TestClient(api, base_url=BROWSER, cookies={SESSION_COOKIE: owner.session_token})


def run(database_url: str, statement: Executable) -> list[tuple[object, ...]]:
    async def execute() -> list[tuple[object, ...]]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(statement)
                return [tuple(row) for row in result.all()] if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def test_a_signed_in_owner_gets_a_one_time_link(database_url: str) -> None:
    """FR-CAP-3: the link opens the bot with a token that works for 15 minutes."""
    owner = Owner(database_url)
    with client_for(database_url, owner) as client:
        response = client.post("/api/telegram/link", headers={CSRF_HEADER: owner.csrf_token})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    link = response.json()
    prefix = "https://t.me/wj_example_bot?start="
    assert link["url"].startswith(prefix)
    token = link["url"].removeprefix(prefix)
    expires_at = datetime.fromisoformat(link["expiresAt"])
    assert timedelta(minutes=14) < expires_at - datetime.now(UTC) <= timedelta(minutes=15)
    stored = select(TelegramLinkToken.user_id).where(
        TelegramLinkToken.token_hash == hash_token(token)
    )
    assert run(database_url, stored) == [(owner.id,)], "only the token's hash is stored"


def test_making_a_link_needs_the_csrf_header(database_url: str) -> None:
    owner = Owner(database_url)
    with client_for(database_url, owner) as client:
        assert client.post("/api/telegram/link").status_code == 403


def test_signed_out_requests_are_refused(database_url: str) -> None:
    owner = Owner(database_url)
    with client_for(database_url, owner) as client:
        client.cookies.clear()
        assert client.get("/api/telegram/link").status_code == 401
        assert client.post("/api/telegram/link").status_code == 401


def test_the_link_status_says_whether_and_since_when(database_url: str) -> None:
    """FR-SET-1."""
    owner = Owner(database_url)
    with client_for(database_url, owner) as client:
        assert client.get("/api/telegram/link").json() == {"linked": False, "linkedAt": None}
        chat_id = _random.randrange(10**9, 10**12)
        run(database_url, insert(TelegramLink).values(user_id=owner.id, chat_id=chat_id))
        status = client.get("/api/telegram/link").json()
    assert status["linked"] is True
    assert datetime.fromisoformat(status["linkedAt"]) <= datetime.now(UTC)


def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("Telegram is down")


@pytest.mark.parametrize(
    ("telegram", "bot_token"), [(telegram_bot, None), (unreachable, "123456:test-token-not-real")]
)
def test_without_the_bot_no_link_is_made(
    database_url: str,
    telegram: Callable[[httpx2.Request], httpx2.Response],
    bot_token: str | None,
) -> None:
    owner = Owner(database_url)
    with client_for(database_url, owner, telegram, bot_token=bot_token) as client:
        response = client.post("/api/telegram/link", headers={CSRF_HEADER: owner.csrf_token})
    assert response.status_code == 503
    tokens = select(TelegramLinkToken.token_hash).where(TelegramLinkToken.user_id == owner.id)
    assert run(database_url, tokens) == []
