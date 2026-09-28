from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import cli
from app.config import get_settings


def test_the_worker_needs_a_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(cli, ["worker"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "Set DATABASE_URL" in result.stderr
