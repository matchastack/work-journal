"""Application settings, read from environment variables.

Values come from the process environment first, then from `backend/.env` if it exists.
Every variable is listed in `backend/.env.example`; empty values fall back to the defaults.
"""

from functools import lru_cache
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
