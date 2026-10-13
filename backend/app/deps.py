"""FastAPI dependencies shared by the routes: the settings, a database session and Claude."""

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.usage import CallLog

LLMFactory = Callable[[CallLog], LLMClient]
"""Makes a Claude client that logs its calls to the given log (`create_app` sets it)."""


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """A session for one request. Routes that write call `commit()` themselves."""
    sessions: async_sessionmaker[AsyncSession] | None = request.app.state.db_sessions
    if sessions is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database isn't set up: set DATABASE_URL"
        )
    async with sessions() as session:
        yield session


def get_llm(request: Request) -> LLMFactory:
    llm: LLMFactory = request.app.state.llm
    return llm


AppSettings = Annotated[Settings, Depends(get_app_settings)]
Db = Annotated[AsyncSession, Depends(get_db)]
Llm = Annotated[LLMFactory, Depends(get_llm)]
