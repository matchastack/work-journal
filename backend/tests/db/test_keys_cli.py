import pytest
from typer.testing import CliRunner

from app.cli import cli
from app.config import get_settings
from app.db.crypto import new_key, use_key_ring


def test_keys_rotate_on_the_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", new_key("k1"))
    get_settings.cache_clear()
    use_key_ring(None)
    try:
        result = CliRunner().invoke(cli, ["keys", "rotate"])
    finally:
        get_settings.cache_clear()
        use_key_ring(None)
    assert result.exit_code == 0, result.output
    assert "There are no encrypted columns yet." in result.stdout
