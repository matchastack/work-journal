"""`/api/settings`: the signed-in owner's preferences and data (T-048, FR-SET-1 to FR-SET-6,
NFR-PRIV-3)."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser, clear_session_cookies
from app.db.settings import (
    get_style_notes,
    handle_taken,
    save_confidential_terms,
    save_portfolio_settings,
    save_style_notes,
)
from app.deps import AppSettings, Db
from app.reminders import default_schedule
from app.schema.common import Model
from app.schema.settings import ConfidentialTerms, PortfolioSettings, ReminderSettings, StyleNotes
from app.settings.data import delete_all_data, export_data
from app.settings.views import (
    ReminderView,
    SettingsView,
    StyleNotesChange,
    changed_notes,
    load_settings,
    reminder_view,
)
from app.telegram.reminders import change_reminders

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


class DataDeletion(Model):
    confirm: str
    """The owner's GitHub username, typed to confirm, in any case."""


@router.get("")
async def settings_page(
    user: CurrentUser, db: Db, settings: AppSettings, response: Response
) -> SettingsView:
    """Every preference: the Telegram link, the reminders, the portfolio page, the style notes
    and the confidential terms."""
    response.headers.update(NO_STORE)
    return await load_settings(db, user.id, default_reminders=default_schedule(settings))


@router.put("/reminders")
async def change_reminder_settings(
    body: ReminderSettings, user: CurrentUser, db: Db, settings: AppSettings, response: Response
) -> ReminderView:
    """Replace the reminder schedule and pause (FR-SET-2). A day, time or time zone left null
    follows the app's default."""
    response.headers.update(NO_STORE)
    await change_reminders(db, user.id, body)
    await db.commit()
    return reminder_view(body, default_schedule(settings))


@router.put("/portfolio")
async def change_portfolio(
    body: PortfolioSettings, user: CurrentUser, db: Db, response: Response
) -> PortfolioSettings:
    """Replace the portfolio page's settings (FR-SET-3). A handle another user has is refused
    (409)."""
    response.headers.update(NO_STORE)
    if body.handle is not None and await handle_taken(db, user.id, body.handle):
        raise HTTPException(status.HTTP_409_CONFLICT, "That handle is taken; choose another")
    await save_portfolio_settings(db, user.id, body)
    await db.commit()
    return body


@router.put("/style-notes")
async def change_style_notes(
    body: StyleNotesChange, user: CurrentUser, db: Db, response: Response
) -> StyleNotes:
    """Replace the words to avoid and the style notes (FR-SET-4). A note kept from before keeps
    where it came from."""
    response.headers.update(NO_STORE)
    notes = changed_notes(await get_style_notes(db, user.id), body, now=datetime.now(UTC))
    await save_style_notes(db, user.id, notes)
    await db.commit()
    return notes


@router.put("/confidential-terms")
async def change_confidential_terms(
    body: ConfidentialTerms, user: CurrentUser, db: Db, response: Response
) -> ConfidentialTerms:
    """Replace the confidential terms (FR-SET-5)."""
    response.headers.update(NO_STORE)
    await save_confidential_terms(db, user.id, body)
    await db.commit()
    return body


@router.get(
    "/export.json",
    response_class=JSONResponse,
    responses={200: {"content": {"application/json": {"schema": {"type": "object"}}}}},
)
async def export_all(user: CurrentUser, db: Db) -> JSONResponse:
    """Everything stored for the owner, as a JSON file (FR-SET-6, NFR-PRIV-3)."""
    now = datetime.now(UTC)
    content = await export_data(db, user, now=now)
    name = f"work-journal-data-{now:%Y-%m-%d}.json"
    headers = NO_STORE | {"Content-Disposition": f'attachment; filename="{name}"'}
    return JSONResponse(content, headers=headers)


@router.delete("/data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data(
    body: DataDeletion, user: CurrentUser, db: Db, settings: AppSettings
) -> Response:
    """Delete the owner's account and everything stored for them, and sign them out everywhere
    (FR-SET-6, NFR-PRIV-3). `confirm` must be their GitHub username (422 otherwise)."""
    login = user.github_login or ""
    if not login or body.confirm.lower() != login.lower():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Type your GitHub username to confirm; nothing was deleted",
        )
    user_id = user.id
    await delete_all_data(db, user_id)
    await db.commit()
    logger.info("User %s deleted all their data", user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
    clear_session_cookies(response, settings)
    return response
