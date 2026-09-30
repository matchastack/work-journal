from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli
from app.config import get_settings
from app.db.crypto import KeyRing, use_key_ring

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in ("DATABASE_URL", "DATA_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    use_key_ring(None)
    yield
    get_settings.cache_clear()
    use_key_ring(None)


def test_new_prints_a_key_that_parses() -> None:
    result = runner.invoke(cli, ["keys", "new", "--id", "k2"])
    assert result.exit_code == 0
    assert KeyRing.parse(result.stdout.strip()).current_id == "k2"


def test_rotate_needs_a_database() -> None:
    result = runner.invoke(cli, ["keys", "rotate"])
    assert result.exit_code == 1
    assert "Set DATABASE_URL" in result.stderr


def test_rotate_needs_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:9/nowhere")
    get_settings.cache_clear()
    result = runner.invoke(cli, ["keys", "rotate"])
    assert result.exit_code == 1
    assert "Nothing was changed: set DATA_ENCRYPTION_KEY" in result.stderr
