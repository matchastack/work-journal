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


def test_the_database_url_stays_out_of_reprs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:not-a-real-password@db:5432/app")
    settings = Settings()
    assert "not-a-real-password" not in repr(settings)
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().endswith("@db:5432/app")


def test_allowed_github_logins_are_a_list_in_lowercase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "Ada-L, lin99 ,,grace")
    assert Settings().allowed_github_logins == {"ada-l", "lin99", "grace"}


def test_nobody_is_allowed_by_default() -> None:
    assert Settings().allowed_github_logins == frozenset()


@pytest.mark.parametrize("login", ["-ada", "@ada", "ada@example.com", "x" * 40])
def test_malformed_github_logins_are_rejected(monkeypatch: pytest.MonkeyPatch, login: str) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", f"ada,{login}")
    with pytest.raises(ValidationError, match="not GitHub usernames"):
        Settings()


def test_the_app_url_loses_its_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().app_url == "http://localhost:8000"
    monkeypatch.setenv("APP_URL", "https://journal.example.com/")
    assert Settings().app_url == "https://journal.example.com"


@pytest.mark.parametrize("url", ["journal.example.com", "ftp://example.com", "https://"])
def test_the_app_url_must_be_a_web_address(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("APP_URL", url)
    with pytest.raises(ValidationError, match="http"):
        Settings()


@pytest.mark.parametrize(
    ("url", "https"),
    [
        ("https://journal.example.com", True),
        ("http://localhost:5173", False),
        ("http://127.0.0.1:8000", False),
        ("http://[::1]:8000", False),
    ],
)
def test_only_this_computer_may_use_plain_http(
    monkeypatch: pytest.MonkeyPatch, url: str, https: bool
) -> None:
    monkeypatch.setenv("APP_URL", url)
    assert Settings().https is https


def test_another_host_must_use_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without HTTPS, session cookies couldn't be Secure."""
    monkeypatch.setenv("APP_URL", "http://journal.example.com")
    with pytest.raises(ValidationError, match="must use https://, except on this computer"):
        Settings()


def test_the_github_client_secret_stays_out_of_reprs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "not-a-real-secret")
    settings = Settings()
    assert "not-a-real-secret" not in repr(settings)
    assert settings.github_client_secret is not None
    assert settings.github_client_secret.get_secret_value() == "not-a-real-secret"
