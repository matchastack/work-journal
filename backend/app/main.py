"""FastAPI application. Run locally with `uv run uvicorn app.main:app --reload`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

import httpx2
from fastapi import APIRouter, FastAPI, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.auth import github
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.db.engine import DatabaseStatus, check_database, create_engine, session_factory
from app.telegram.routes import router as telegram_api_router
from app.telegram.webhook import router as telegram_router
from app.web import add_web_app

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


def create_app(
    settings: Settings | None = None, *, transport: httpx2.AsyncBaseTransport | None = None
) -> FastAPI:
    """Build the FastAPI application. The database engine and the HTTP client for outgoing
    requests (GitHub) live as long as the app does. Tests pass a `transport` that stands in
    for the network."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncGenerator[None]:
        url = settings.database_url
        engine = create_engine(url.get_secret_value()) if url else None
        api.state.engine = engine
        api.state.db_sessions = session_factory(engine) if engine else None
        async with httpx2.AsyncClient(transport=transport, timeout=github.TIMEOUT_S) as http:
            api.state.http = http
            try:
                yield
            finally:
                if engine is not None:
                    await engine.dispose()

    api = FastAPI(title="Work Journal", version=__version__, lifespan=lifespan)
    api.state.settings = settings
    api.include_router(router)
    api.include_router(auth_router)
    api.include_router(telegram_router)
    api.include_router(telegram_api_router)
    add_web_app(api, settings.web_dist_dir)
    return api


app = create_app()
