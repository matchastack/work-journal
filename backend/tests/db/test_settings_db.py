"""The owner's settings in the database: style notes (FR-WRT-4, FR-REV-4) and the LinkedIn
pack's state (FR-LIN-2)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.settings import (
    add_style_notes,
    get_linkedin_done,
    get_linkedin_pack,
    get_style_notes,
    save_linkedin_done,
    save_linkedin_pack,
    save_style_notes,
)
from app.linkedin import DoneSection, LinkedInDone, LinkedInPack, PackSection
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


async def test_the_linkedin_pack_and_its_done_snapshot_are_kept(session: AsyncSession) -> None:
    user = await new_user(session)
    assert await get_linkedin_pack(session, user) is None
    assert await get_linkedin_done(session, user) is None
    section = PackSection(
        key="headline",
        kind="headline",
        label="Headline",
        text="Software Engineer",
        source="abc",
        changed=True,
    )
    pack = LinkedInPack(sections=(section,), profile_version=2)
    await save_linkedin_pack(session, user, pack)
    done = LinkedInDone(
        sections=(DoneSection(key="headline", label="Headline", text="Engineer", source="a"),),
        marked_at=NOW,
    )
    await save_linkedin_done(session, user, done)
    await save_linkedin_pack(session, user, pack.model_copy(update={"profile_version": 3}))
    assert await get_linkedin_pack(session, user) == pack.model_copy(update={"profile_version": 3})
    assert await get_linkedin_done(session, user) == done
