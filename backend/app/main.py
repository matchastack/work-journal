"""FastAPI application. Run locally with `uv run uvicorn app.main:app --reload`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.config import Settings, get_settings
from app.db.engine import DatabaseStatus, check_database, create_engine

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: DatabaseStatus


@router.get("/healthz")
async def healthz(request: Request, response: Response) -> Health:
    """Report that the API is up, and whether it can reach the database."""
    engine: AsyncEngine | None = request.app.state.engine
    database = await check_database(engine)
    if database == "unreachable":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="error", database=database)
    return Health(status="ok", database=database)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. The database engine lives as long as the app does."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncGenerator[None]:
        url = settings.database_url
        engine = create_engine(url.get_secret_value()) if url else None
        api.state.engine = engine
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    api = FastAPI(title="Work Journal", version=__version__, lifespan=lifespan)
    api.include_router(router)
    return api


app = create_app()
