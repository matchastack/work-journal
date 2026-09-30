"""Application settings, read from environment variables.

Values come from the process environment first, then from `backend/.env` if it exists.
Every variable is listed in `backend/.env.example`; empty values fall back to the defaults.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
