"""`wj github digest --db`: the token must be the database user's own GitHub account (FR-GH-1)."""

import asyncio
import json
import random
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Executable, delete, insert
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings
from app.db.models import User
from app.github.fake import TOKEN, FakeGitHubApi

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
runner = CliRunner()


def run(database_url: str, statement: Executable) -> None:
    async def execute() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(statement)
        finally:
            await engine.dispose()

    asyncio.run(execute())


@pytest.fixture
def owner(database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[User]:
    """A signed-in user, known by their GitHub account's number, and the settings to reach
    them."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("GITHUB_REPOS_TOKEN", TOKEN)
    get_settings.cache_clear()
    user = User(github_id=random.randint(10**8, 10**9), github_login=f"gh-{uuid.uuid4().hex[:8]}")
    run(database_url, insert(User).values(github_id=user.github_id, github_login=user.github_login))
    yield user
    run(database_url, delete(User).where(User.github_login == user.github_login))
    get_settings.cache_clear()


def github(monkeypatch: pytest.MonkeyPatch, account_id: int) -> FakeGitHubApi:
    """GitHub, where the token belongs to account `account_id` with the owner's username."""
    fake = FakeGitHubApi({"id": account_id, "login": "casey-example", "type": "User"})
    fake.add(json.loads((FIXTURES / "tinyparse.json").read_text(encoding="utf-8")))

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=fake.transport)

    monkeypatch.setattr(cli_module, "_github_http", client)
    return fake


def test_digest_reads_with_the_users_own_token(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert owner.github_id is not None
    github(monkeypatch, owner.github_id)
    args = [
        "github",
        "digest",
        "tinyparse-org/tinyparse",
        "--db",
        "--user",
        str(owner.github_login),
    ]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("tinyparse-org/tinyparse: someone else's repository, public.")


def test_digest_refuses_a_token_for_another_account(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The username matches, but the account's number doesn't: it isn't the owner's account."""
    assert owner.github_id is not None
    fake = github(monkeypatch, owner.github_id + 1)
    args = [
        "github",
        "digest",
        "tinyparse-org/tinyparse",
        "--db",
        "--user",
        str(owner.github_login),
    ]
    result = runner.invoke(cli, args)
    assert result.exit_code == 1
    assert "belongs to another GitHub account" in result.output
    assert fake.paths() == ["/user"]
