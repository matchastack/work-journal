"""Application settings, read from environment variables.

Values come from the process environment first, then from `backend/.env` if it exists.
Every variable is listed in `backend/.env.example`; empty values fall back to the defaults.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPOSITORY = Path(__file__).resolve().parents[2]
GITHUB_LOGIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
"""A GitHub username in lowercase: up to 39 letters, digits and hyphens, not starting with one."""
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Addresses on this computer, the only ones the app may be served from without HTTPS."""


class Settings(BaseSettings):
    """Settings shared by the API, the worker and the `wj` command-line tool."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: SecretStr | None = None
    """PostgreSQL, e.g. `postgresql+asyncpg://work_journal@localhost:5432/work_journal` with
    `compose.yml`. A plain `postgresql://` URL also works."""
    data_encryption_key: SecretStr | None = None
    """Keys for journal and fact text: `id:key` pairs, the current key first (app/db/crypto.py)."""
    app_url: str = "http://localhost:8000"
    """The app's public address. GitHub sends people back to `<app_url>/auth/callback`. It uses
    https://, except on this computer."""
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    allowed_github_logins: Annotated[frozenset[str], NoDecode] = frozenset()
    """GitHub usernames that may sign in, in lowercase. Empty means nobody can."""
    web_dist_dir: Path = REPOSITORY / "frontend" / "dist"
    """The built web app (`npm run build` in `frontend/`), served at `/` when it exists."""

    @field_validator("app_url")
    @classmethod
    def http_url_without_trailing_slash(cls, value: str) -> str:
        if not re.match(r"^https?://[^/\s]+(/\S*)?$", value):
            raise ValueError("must be an http:// or https:// address")
        if value.startswith("http://") and urlsplit(value).hostname not in LOCAL_HOSTS:
            raise ValueError("must use https://, except on this computer (localhost)")
        return value.rstrip("/")

    @property
    def https(self) -> bool:
        """Whether the app is served over HTTPS, as it is everywhere but on this computer."""
        return self.app_url.startswith("https://")

    @field_validator("allowed_github_logins", mode="before")
    @classmethod
    def split_logins(cls, value: object) -> object:
        """Accept a list separated by commas or spaces, as in `ALLOWED_GITHUB_LOGINS=ada,lin`."""
        if isinstance(value, str):
            return [login for login in re.split(r"[\s,]+", value) if login]
        return value

    @field_validator("allowed_github_logins")
    @classmethod
    def valid_logins(cls, value: frozenset[str]) -> frozenset[str]:
        logins = frozenset(login.lower() for login in value)
        if invalid := sorted(login for login in logins if not GITHUB_LOGIN.match(login)):
            raise ValueError(f"not GitHub usernames: {', '.join(invalid)}")
        return logins


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
