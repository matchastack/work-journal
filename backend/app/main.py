"""FastAPI application. Run locally with `uv run uvicorn app.main:app --reload`."""

from fastapi import APIRouter, FastAPI

from app import __version__

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Report that the API process is up."""
    return {"status": "ok"}


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    api = FastAPI(title="Work Journal", version=__version__)
    api.include_router(router)
    return api


app = create_app()
