"""`POST /internal/tick`: background work, called every 15 minutes by a free scheduler (§13)."""

import secrets

from fastapi import APIRouter, HTTPException, Request, status

from app.deps import AppSettings
from app.tick import tick

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/tick", include_in_schema=False)
async def run_tick(request: Request, settings: AppSettings) -> dict[str, int | bool]:
    """Run one tick of background work (`app/tick.py`) for the scheduler, which sends
    TICK_SECRET as a bearer token."""
    secret = settings.tick_secret
    sent = request.headers.get("Authorization", "")
    if secret is None or not secrets.compare_digest(
        sent.encode(), f"Bearer {secret.get_secret_value()}".encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown caller")
    retried = await tick()
    if retried is None:
        return {"skipped": True}
    return {"retried": retried}
