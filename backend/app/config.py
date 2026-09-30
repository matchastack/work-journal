"""Application settings, read from environment variables.

Values come from the process environment first, then from `backend/.env` if it exists.
Every variable is listed in `backend/.env.example`; empty values fall back to the defaults.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal and runtime data."""


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

    anthropic_api_key: SecretStr | None = None
    """The Claude API key. Without it, the Anthropic SDK looks for its other credentials."""
    llm_model_heavy: str | None = None
    """The model ID for heavy tasks (requirements §10). Model IDs are never hardcoded."""
    llm_model_standard: str | None = None
    llm_model_light: str | None = None
    llm_price_heavy: tuple[float, float] | None = None
    """USD per million input and output tokens, as JSON such as `[5, 25]`, for cost logging."""
    llm_price_standard: tuple[float, float] | None = None
    llm_price_light: tuple[float, float] | None = None
    llm_heavy_fallback: bool = True
    """Let the API rerun a refused heavy-tier request on its recommended fallback model."""
    llm_call_log: Path = LOCAL_DIR / "llm-calls.jsonl"
    """Where each LLM call's task, model, tokens and cost are logged, until the database exists."""


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
