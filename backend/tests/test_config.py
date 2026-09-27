import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

ENV_EXAMPLE = Path(__file__).parent.parent / ".env.example"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run each test in an empty directory (no .env) with no settings in the environment."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return tmp_path


def test_defaults_apply_without_environment() -> None:
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings()
    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"


def test_dotenv_file_is_read_and_environment_wins(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    (isolated_settings / ".env").write_text("APP_ENV=test\nLOG_LEVEL=WARNING\n")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    settings = Settings()
    assert settings.app_env == "test"
    assert settings.log_level == "ERROR"


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "")
    assert Settings().app_env == "development"


def test_unknown_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_lists_every_setting_without_values() -> None:
    lines = ENV_EXAMPLE.read_text().splitlines()
    entries = dict(line.split("=", 1) for line in lines if re.match(r"^[A-Z][A-Z0-9_]*=", line))
    assert set(entries) == {name.upper() for name in Settings.model_fields}
    assert all(value == "" for value in entries.values())
