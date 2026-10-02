"""`wj db load-profile` and the commands' --db option, against the test database."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Executable, delete, func, insert, select
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner, Result

from app.cli import cli
from app.config import get_settings
from app.db.crypto import new_key, use_key_ring
from app.db.models import FactRow, ProfileVersionRow, User

FIXTURES = Path(__file__).parents[1] / "fixtures"
runner = CliRunner()


@pytest.fixture
def owner(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A GitHub username of the test's own, with the database settings; its user is deleted
    after the test."""
    login = f"cli-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", new_key("k1"))
    get_settings.cache_clear()
    use_key_ring(None)
    yield login
    query = delete(User).where(func.lower(User.github_login) == login)
    scalars(database_url, query)
    get_settings.cache_clear()
    use_key_ring(None)


def scalars(database_url: str, query: Executable) -> list[Any]:
    """Run one statement in its own transaction, and return its first column."""

    async def go() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(query)
                return list(result.scalars()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def imported(tmp_path: Path) -> Path:
    """The fictional master resume, imported into files: the profile's path."""
    result = invoke("import", "tex", str(FIXTURES / "master-resume.tex"), "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    return tmp_path / "profile.json"


def invoke(*args: str) -> Result:
    return runner.invoke(cli, list(args))


def stored(database_url: str, login: str) -> tuple[list[int], int]:
    """The user's version numbers, and how many facts they have."""
    user = select(User.id).where(User.github_login == login).scalar_subquery()
    versions = select(ProfileVersionRow.number).where(ProfileVersionRow.user_id == user)
    [facts] = scalars(
        database_url, select(func.count()).select_from(FactRow).where(FactRow.user_id == user)
    )
    return scalars(database_url, versions), facts


def test_load_profile_saves_version_1_and_the_facts(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    profile = imported(tmp_path)
    result = invoke("db", "load-profile", str(profile), "--user", owner)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"Loaded {profile} as version 1 for {owner}, with ")
    versions, facts = stored(database_url, owner)
    assert versions == [1]
    assert facts > 0
    assert f"with {facts} facts from {tmp_path / 'facts.json'}." in result.stdout


def test_the_profile_is_loaded_once(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    again = invoke("db", "load-profile", str(profile), "--user", owner)
    assert again.exit_code == 1
    assert "Nothing was loaded: the profile is already loaded" in again.stderr


def test_commands_read_the_newest_version_with_db(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    from_file = invoke("lint", "--profile", str(profile))
    from_db = invoke("lint", "--db", "--user", owner)
    assert from_db.exit_code == from_file.exit_code
    assert from_db.stdout == from_file.stdout
    site = tmp_path / "site"
    built = invoke("portfolio", "build", "--out", str(site), "--db", "--user", owner)
    assert built.exit_code == 0, built.output
    assert (site / "index.html").is_file()


@pytest.mark.latex
def test_render_reads_the_database(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    out = tmp_path / "master.pdf"
    result = invoke("render", "--db", "--user", owner, "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")


def test_import_tex_can_save_straight_to_the_database(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    """FR-IMP-5: with --db, the imported data never touches a local file."""
    tex = str(FIXTURES / "master-resume.tex")
    result = invoke("import", "tex", tex, "--out", str(tmp_path), "--db", "--user", owner)
    assert result.exit_code == 0, result.output
    versions, facts = stored(database_url, owner)
    assert versions == [1]
    assert f"Saved the profile as version 1 for {owner}, with {facts} facts." in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_an_unknown_user_is_reported(owner: str) -> None:
    result = invoke("lint", "--db", "--user", owner)
    assert result.exit_code == 1
    assert f"Which user? no user '{owner}' in the database." in result.stderr


def test_a_user_without_a_profile_is_told_how_to_load_one(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    scalars(database_url, insert(User).values(github_login=owner))
    result = invoke("lint", "--db", "--user", owner)
    assert result.exit_code == 1
    assert "load one with `wj db load-profile`" in result.stderr
