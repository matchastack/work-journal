"""`wj github digest --db`: the token must be the database user's own GitHub account (FR-GH-1).
`wj github link`, `sync`, `list` and `unlink` keep linked repositories and their facts (FR-GH-2,
FR-GH-7), against a stand-in for GitHub, with the fake LLM client."""

import asyncio
import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Executable, delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings
from app.db.crypto import new_key, use_key_ring
from app.db.models import FactRow, ProfileVersionRow, User
from app.db.profiles import save_version
from app.github.extraction import RepoExtractionReply
from app.github.fake import TOKEN, FakeGitHubApi
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.profile import Profile

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
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", new_key("k1"))
    get_settings.cache_clear()
    use_key_ring(None)
    user = User(github_id=random.randint(10**8, 10**9), github_login=f"gh-{uuid.uuid4().hex[:8]}")
    run(database_url, insert(User).values(github_id=user.github_id, github_login=user.github_login))
    yield user
    run(database_url, delete(User).where(User.github_login == user.github_login))
    get_settings.cache_clear()
    use_key_ring(None)


def github(monkeypatch: pytest.MonkeyPatch, account_id: int) -> FakeGitHubApi:
    """GitHub, where the token belongs to account `account_id` with the owner's username."""
    fake = FakeGitHubApi({"id": account_id, "login": "casey-example", "type": "User"})
    fake.add(json.loads((FIXTURES / "tinyparse.json").read_text(encoding="utf-8")))
    # The owner's own repository: the fixture's account is theirs.
    recipe_box = (FIXTURES / "recipe-box.json").read_text(encoding="utf-8")
    fake.add(json.loads(recipe_box.replace('"id": 4242', f'"id": {account_id}')))

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


# --- wj github link, sync, list and unlink ---

PROFILE = Profile.model_validate_json(
    (FIXTURES.parent / "profile.json").read_text(encoding="utf-8")
)
RECIPE_BOX = "casey-example/recipe-box"
SHOPPING = "Added a weekly shopping list that groups ingredients by aisle."


def with_profile(database_url: str, owner: User, *, loaded: datetime) -> None:
    """The owner's profile, loaded as version 1 at `loaded`."""

    async def save() -> None:
        engine = create_async_engine(database_url)
        try:
            async with AsyncSession(engine) as session:
                user_id = await session.scalar(
                    select(User.id).where(User.github_login == owner.github_login)
                )
                assert user_id is not None
                await save_version(session, user_id, PROFILE, author="owner")
                await session.execute(
                    update(ProfileVersionRow)
                    .where(ProfileVersionRow.user_id == user_id)
                    .values(created_at=loaded)
                )
                await session.commit()
        finally:
            await engine.dispose()

    # Versions can't be changed once saved, so the trigger is off while this one is dated.
    run(database_url, text("ALTER TABLE profile_versions DISABLE TRIGGER USER"))
    try:
        asyncio.run(save())
    finally:
        run(database_url, text("ALTER TABLE profile_versions ENABLE TRIGGER USER"))


def model(monkeypatch: pytest.MonkeyPatch, *replies: RepoExtractionReply) -> FakeMessages:
    fake = FakeMessages(*(reply(answer) for answer in replies))
    settings = Settings(llm_model_standard="standard-model")
    monkeypatch.setattr(
        cli_module, "_llm_client", lambda: LLMClient(settings, fake, MemoryCallLog())
    )
    return fake


def found(*labels: str) -> RepoExtractionReply:
    facts = [
        {"items": [label], "statement": SHOPPING, "kind": "accomplishment"} for label in labels
    ]
    return RepoExtractionReply.model_validate({"facts": facts})


def github_facts(database_url: str, owner: User) -> int:
    user = select(User.id).where(User.github_login == owner.github_login).scalar_subquery()
    query = select(func.count()).select_from(FactRow)
    query = query.where(FactRow.user_id == user, FactRow.origin == "github")

    async def count() -> int:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return int((await connection.execute(query)).scalar_one())
        finally:
            await engine.dispose()

    return asyncio.run(count())


def wj(owner: User, *args: str, input: str | None = None) -> Result:
    return runner.invoke(cli, ["github", *args, "--user", str(owner.github_login)], input=input)


def test_link_reads_from_the_month_the_profile_was_loaded_and_saves_the_facts(
    owner: User, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-GH-3: for a project in the profile, what the imported resume already says isn't read
    again. Here that leaves a commit and a pull request (`c1` and `pr1` in this read), with the
    about item."""
    assert owner.github_id is not None
    with_profile(database_url, owner, loaded=datetime(2023, 8, 10, tzinfo=UTC))
    github(monkeypatch, owner.github_id)
    fake = model(monkeypatch, found("pr1"))
    result = wj(owner, "link", RECIPE_BOX, "--project", "recipe_box", "--yes")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Found 3 merged pull requests and 7 commits in casey-example/recipe-box since August "
        "2023: 3 new items to read, so 1 model call.",
        "Part 1 of 1: saved 1 fact.",
        "Linked casey-example/recipe-box to Recipe Box, and saved 1 fact.",
    ]
    assert len(fake.requests) == 1
    assert github_facts(database_url, owner) == 1


def test_link_asks_before_it_reads(
    owner: User, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert owner.github_id is not None
    with_profile(database_url, owner, loaded=datetime(2023, 8, 10, tzinfo=UTC))
    github(monkeypatch, owner.github_id)
    fake = model(monkeypatch)
    result = wj(owner, "link", RECIPE_BOX, "--project", "recipe_box", input="n\n")
    assert result.exit_code == 0, result.output
    assert "Go on? [y/N]" in result.stdout
    assert result.stdout.rstrip().endswith("Nothing was linked.")
    assert fake.requests == []
    assert wj(owner, "list").stdout == "No repository is linked. Link one with `wj github link`.\n"


def test_sync_reads_only_whats_new_and_unlink_stops_it(
    owner: User, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-GH-7: a second read with nothing new makes no model call. Unlinking keeps the
    facts."""
    assert owner.github_id is not None
    with_profile(database_url, owner, loaded=datetime(2023, 8, 10, tzinfo=UTC))
    github(monkeypatch, owner.github_id)
    model(monkeypatch, found("pr2"))
    since = ["--since", "2023-06", "--ownership", "contributed", "--yes"]
    assert wj(owner, "link", RECIPE_BOX, "--project", "recipe_box", *since).exit_code == 0
    idle = model(monkeypatch)
    synced = wj(owner, "sync")
    assert synced.exit_code == 0, synced.output
    assert synced.stdout == "casey-example/recipe-box: nothing new.\n"
    assert idle.requests == []
    listed = wj(owner, "list")
    assert listed.exit_code == 0, listed.output
    today = datetime.now(UTC)
    last = f"{today.day} {today:%b} {today.year}"
    assert listed.stdout == (
        f"casey-example/recipe-box: Recipe Box (recipe_box); read from June 2023, last read "
        f"{last}, 6 items read, ownership capped at contributed.\n"
    )
    unlinked = wj(owner, "unlink", "Casey-Example/Recipe-Box")
    assert unlinked.exit_code == 0, unlinked.output
    assert unlinked.stdout.startswith("Unlinked casey-example/recipe-box: it won't be read again")
    assert wj(owner, "sync").stdout == "No repository is linked. Link one with `wj github link`.\n"
    assert github_facts(database_url, owner) == 1
    again = wj(owner, "unlink", RECIPE_BOX)
    assert again.exit_code == 1
    assert "isn't linked" in again.stdout


def test_link_refuses_a_project_the_profile_doesnt_have(
    owner: User, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert owner.github_id is not None
    with_profile(database_url, owner, loaded=datetime(2023, 8, 10, tzinfo=UTC))
    fake = github(monkeypatch, owner.github_id)
    result = wj(owner, "link", RECIPE_BOX, "--project", "no_such_project", "--yes")
    assert result.exit_code == 2
    assert "the profile has no project 'no_such_project'" in result.output
    assert fake.requests == []
