"""`/internal/tick`: background work, called once a day by Vercel's cron (§13).

Vercel calls it with GET, and sends `CRON_SECRET` as `Authorization: Bearer <secret>`. POST works
too, to run a tick by hand.
"""

import secrets

from fastapi import APIRouter, HTTPException, Request, status

from app.deps import AppSettings
from app.tick import tick

router = APIRouter(prefix="/internal", tags=["internal"])


@router.api_route("/tick", methods=["GET", "POST"], include_in_schema=False)
async def run_tick(request: Request, settings: AppSettings) -> dict[str, int | bool]:
    """Run one tick of background work (`app/tick.py`) for a caller with CRON_SECRET."""
    secret = settings.cron_secret
    sent = request.headers.get("Authorization", "")
    if secret is None or not secrets.compare_digest(
        sent.encode(), f"Bearer {secret.get_secret_value()}".encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown caller")
    retried = await tick()
    if retried is None:
        return {"skipped": True}
    return {"retried": retried}
