"""The commands' --db and --user options, where no database is needed."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app import cli as cli_module
from app.cli import cli
from app.config import get_settings

PROFILE = Path(__file__).parent / "fixtures" / "profile.json"
runner = CliRunner()


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("no_database")
def test_db_needs_a_database_url() -> None:
    result = runner.invoke(cli, ["lint", "--db"])
    assert result.exit_code == 1
    assert "Set DATABASE_URL to use the database." in result.stderr


def test_user_goes_with_db() -> None:
    result = runner.invoke(cli, ["lint", "--profile", str(PROFILE), "--user", "casey"])
    assert result.exit_code == 2
    assert "goes with --db" in result.stderr


def test_a_profile_file_and_the_database_dont_mix() -> None:
    result = runner.invoke(cli, ["render", "--profile", str(PROFILE), "--db"])
    assert result.exit_code == 2
    assert "use either --profile or --db" in result.stderr


def test_a_missing_default_profile_suggests_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "LOCAL_DIR", tmp_path)
    result = runner.invoke(cli, ["lint"])
    assert result.exit_code == 2
    assert "does not exist; import your resume with `wj import tex`" in result.stderr
