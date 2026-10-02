"""FastAPI dependencies shared by the routes: the settings and a database session."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings


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


AppSettings = Annotated[Settings, Depends(get_app_settings)]
Db = Annotated[AsyncSession, Depends(get_db)]
