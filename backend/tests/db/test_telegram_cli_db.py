"""`wj telegram link` against the test database, with a stand-in for Telegram."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Executable, insert, select
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from app import cli as cli_module
from app.auth.sessions import hash_token
from app.cli import cli
from app.config import Settings, get_settings
from app.db.models import TelegramLinkToken, User

runner = CliRunner()


@pytest.fixture
def owner(database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    """A user with a GitHub username of the test's own, and the settings to reach them."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token-not-real")
    get_settings.cache_clear()
    login = f"cli-{uuid.uuid4().hex[:8]}"
    run(database_url, insert(User).values(github_login=login))
    yield login
    get_settings.cache_clear()


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


def test_link_prints_a_one_time_link_for_the_user(
    database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def telegram(request: httpx2.Request) -> httpx2.Response:
        bot = {"id": 99, "is_bot": True, "first_name": "Journal", "username": "wj_example_bot"}
        return httpx2.Response(200, json={"ok": True, "result": bot})

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(telegram))

    monkeypatch.setattr(cli_module, "_telegram_http", client)
    result = runner.invoke(cli, ["telegram", "link", "--user", owner])
    assert result.exit_code == 0, result.output
    url = result.stdout.strip().splitlines()[-1]
    assert url.startswith("https://t.me/wj_example_bot?start=")
    token = url.split("start=", 1)[1]
    query = (
        select(User.github_login)
        .join(TelegramLinkToken, TelegramLinkToken.user_id == User.id)
        .where(TelegramLinkToken.token_hash == hash_token(token))
    )
    assert run(database_url, query) == [(owner,)]
