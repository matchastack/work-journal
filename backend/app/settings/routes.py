"""`/api/settings`: the signed-in owner's preferences (T-048, FR-SET-1 to FR-SET-5)."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.db.settings import (
    get_style_notes,
    handle_taken,
    save_confidential_terms,
    save_portfolio_settings,
    save_style_notes,
)
from app.deps import AppSettings, Db
from app.reminders import default_schedule
from app.schema.settings import ConfidentialTerms, PortfolioSettings, ReminderSettings, StyleNotes
from app.settings.views import (
    ReminderView,
    SettingsView,
    StyleNotesChange,
    changed_notes,
    load_settings,
    reminder_view,
)
from app.telegram.reminders import change_reminders

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
