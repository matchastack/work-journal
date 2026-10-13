"""The owner's style notes in the database (FR-WRT-4, FR-REV-4)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.settings import add_style_notes, get_style_notes, save_style_notes
from app.schema.settings import StyleNote, StyleNotes

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


async def new_user(session: AsyncSession) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    return user.id


async def test_style_notes_are_empty_until_there_are_some(session: AsyncSession) -> None:
    assert await get_style_notes(session, await new_user(session)) == StyleNotes()


async def test_notes_are_added_after_the_others_once_each(session: AsyncSession) -> None:
    user = await new_user(session)
    await save_style_notes(session, user, StyleNotes(avoid=("spearheaded",)))
    await add_style_notes(
        session,
        user,
        [
            "Keep the team's name out",
            "Keep the team\N{RIGHT SINGLE QUOTATION MARK}s name out",
            "Say what it fixed",
        ],
        source="rejection",
        now=NOW,
    )
    await add_style_notes(session, user, ["say what it fixed"], source="owner", now=NOW)
    assert await get_style_notes(session, user) == StyleNotes(
        avoid=("spearheaded",),
        notes=(
            StyleNote(text="Keep the team's name out", source="rejection", added_at=NOW),
            StyleNote(text="Say what it fixed", source="rejection", added_at=NOW),
        ),
    )
