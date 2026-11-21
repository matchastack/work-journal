"""Job postings sent to the bot with `/tailor` (FR-CAP-9, T-078): collecting the posting, and
sending it for tailoring. The draft's jobs, and what the bot says after them, are tested in
`test_tailor_jobs_db.py`."""

import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalEntry, JournalMessage, TailorDraftRow, TelegramLink, User
from app.tailor.drafts import POSTING_LIMIT
from app.telegram import postings
from app.telegram.api import send
from app.telegram.journal import DONE, Handled, handle_update, help_text

pytestmark = pytest.mark.anyio

NINE = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
TIMEOUT = timedelta(minutes=30)
POSTING = "Backend Engineer at Fabrikam\nYou'll build our order APIs in Python and PostgreSQL."
MORE = "Nice to have: Kotlin."
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


async def linked(session: AsyncSession, chat_id: int) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    await session.flush()
    return user.id


def message(chat_id: int, text: str, at: datetime) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "message": {
                "message_id": _random.randrange(1, 10**6),
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat": {"id": chat_id, "type": "private"},
                "date": int(at.timestamp()),
                "text": text,
            },
        }
    )


async def sending(
    session: AsyncSession, chat_id: int, text: str, at: datetime = NINE, **options: Any
) -> Handled:
    return await handle_update(
        session, message(chat_id, text, at), entry_timeout=TIMEOUT, **options
    )


async def drafts(session: AsyncSession, user_id: uuid.UUID) -> list[TailorDraftRow]:
    rows = await session.scalars(select(TailorDraftRow).where(TailorDraftRow.user_id == user_id))
    return list(rows)


async def journaled(session: AsyncSession, user_id: uuid.UUID) -> int:
    query = select(func.count()).where(JournalMessage.user_id == user_id)
    return await session.scalar(query) or 0


async def test_a_posting_joins_its_messages_until_done(session: AsyncSession, chat_id: int) -> None:
    """None of it is journaled, and the draft is marked as sent to the bot."""
    user_id = await linked(session, chat_id)
    started = await sending(session, chat_id, "/tailor")
    assert started.calls == (send(chat_id, postings.STARTED),)
    for minute, text in enumerate((POSTING, MORE), start=1):
        handled = await sending(session, chat_id, text, NINE + timedelta(minutes=minute))
        assert handled == Handled(), "no reply to each part"
    done = await sending(session, chat_id, "/done", NINE + timedelta(minutes=3))
    assert done.calls == (send(chat_id, postings.SENT),)
    [draft] = await drafts(session, user_id)
    assert done.draft == draft.id
    assert (draft.posting_text, draft.from_bot, draft.status) == (
        f"{POSTING}\n{MORE}",
        True,
        "parsing",
    )
    assert await journaled(session, user_id) == 0
    link = await session.get(TelegramLink, user_id)
    assert link is not None and (link.posting, link.posting_at) == (None, None)
    after = await sending(session, chat_id, "/done", NINE + timedelta(minutes=4))
    assert after.calls != done.calls, "the next /done closes an entry, as before"


async def test_text_sent_with_tailor_starts_the_posting(
    session: AsyncSession, chat_id: int
) -> None:
    user_id = await linked(session, chat_id)
    started = await sending(session, chat_id, f"/tailor {POSTING}")
    assert started.calls == (send(chat_id, postings.STARTED_WITH_TEXT),)
    await sending(session, chat_id, "/done", NINE + timedelta(minutes=1))
    [draft] = await drafts(session, user_id)
    assert draft.posting_text == POSTING, "its lines kept"


async def test_tailor_closes_the_open_entry_first(session: AsyncSession, chat_id: int) -> None:
    user_id = await linked(session, chat_id)
    await sending(session, chat_id, "Wrote the runbook for the export.")
    handled = await sending(session, chat_id, "/tailor", NINE + timedelta(minutes=1))
    [entry] = await session.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id))
    assert (entry.closed_by, handled.ready) == ("done", (entry.id,))
    await sending(session, chat_id, POSTING, NINE + timedelta(minutes=2))
    assert await journaled(session, user_id) == 1, "only the note before /tailor"


async def test_a_posting_doesnt_answer_the_bots_questions(
    session: AsyncSession, chat_id: int
) -> None:
    """The bot asked for a new role's name; the posting's text isn't it."""
    user_id = await linked(session, chat_id)
    entry = JournalEntry(
        user_id=user_id,
        opened_at=NINE - timedelta(minutes=10),
        last_message_at=NINE - timedelta(minutes=5),
        closed_at=NINE - timedelta(minutes=5),
        closed_by="done",
        role_asked_at=NINE - timedelta(minutes=5),
        naming_role=True,
    )
    session.add(entry)
    await session.flush()
    await sending(session, chat_id, "/tailor")
    await sending(session, chat_id, POSTING, NINE + timedelta(minutes=1))
    await session.flush()
    await session.refresh(entry)
    assert (entry.naming_role, entry.new_role) == (True, None)
    await sending(session, chat_id, "/done", NINE + timedelta(minutes=2))
    named = await sending(session, chat_id, "Data Engineer, Fabrikam", NINE + timedelta(minutes=3))
    await session.flush()
    await session.refresh(entry)
    assert entry.new_role == "Data Engineer, Fabrikam"
    assert named.calls[0] == send(chat_id, "New role for this entry: Data Engineer, Fabrikam.")


async def test_a_quiet_posting_is_tailored_at_the_next_message(
    session: AsyncSession, chat_id: int
) -> None:
    """As a quiet entry closes (FR-CAP-4); the message is then journaled as usual."""
    user_id = await linked(session, chat_id)
    await sending(session, chat_id, f"/tailor {POSTING}")
    handled = await sending(session, chat_id, "Wrote the runbook.", NINE + TIMEOUT)
    [draft] = await drafts(session, user_id)
    assert (handled.calls[0], handled.draft) == (send(chat_id, postings.SENT), draft.id)
    assert draft.posting_text == POSTING
    assert await journaled(session, user_id) == 1


async def test_a_new_tailor_sends_the_open_posting_first(
    session: AsyncSession, chat_id: int
) -> None:
    user_id = await linked(session, chat_id)
    await sending(session, chat_id, f"/tailor {POSTING}")
    handled = await sending(session, chat_id, "/tailor", NINE + timedelta(minutes=1))
    assert handled.calls == (send(chat_id, postings.SENT), send(chat_id, postings.STARTED))
    [draft] = await drafts(session, user_id)
    assert (handled.draft, draft.posting_text) == (draft.id, POSTING)


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("https://jobs.example.com/backend-engineer", postings.LINKS_ONLY),
        ("www.example.com/careers/42\n https://example.org/apply?id=7 ", postings.LINKS_ONLY),
        ("   ", postings.EMPTY),
        ("x" * (POSTING_LIMIT + 1), postings.TOO_LONG),
    ],
)
async def test_a_posting_that_cant_be_tailored_says_why(
    session: AsyncSession, chat_id: int, text: str, reply: str
) -> None:
    """The bot never opens a link: it asks for the posting's text."""
    user_id = await linked(session, chat_id)
    await sending(session, chat_id, "/tailor")
    await sending(session, chat_id, text, NINE + timedelta(minutes=1))
    handled = await sending(session, chat_id, "/done", NINE + timedelta(minutes=2))
    assert (handled.calls, handled.draft) == ((send(chat_id, reply),), None)
    assert await drafts(session, user_id) == []


async def test_a_link_with_the_postings_text_is_tailored(
    session: AsyncSession, chat_id: int
) -> None:
    user_id = await linked(session, chat_id)
    await sending(session, chat_id, f"/tailor https://jobs.example.com/42\n{POSTING}")
    await sending(session, chat_id, "/done", NINE + timedelta(minutes=1))
    assert len(await drafts(session, user_id)) == 1


async def test_done_without_a_posting_closes_the_entry(session: AsyncSession, chat_id: int) -> None:
    await linked(session, chat_id)
    await sending(session, chat_id, "A note.")
    handled = await sending(session, chat_id, "/done", NINE + timedelta(minutes=1))
    assert handled.calls == (send(chat_id, DONE),)


async def test_the_sweep_tailors_postings_that_went_quiet(
    session: AsyncSession, chat_id: int
) -> None:
    quiet = await linked(session, chat_id)
    await sending(session, chat_id, f"/tailor {POSTING}")
    recent_chat = chat_id + 1
    recent = await linked(session, recent_chat)
    await sending(session, recent_chat, f"/tailor {MORE}", NINE + timedelta(minutes=1))
    closed = await postings.close_quiet_postings(session, NINE + TIMEOUT, TIMEOUT)
    [draft] = await drafts(session, quiet)
    mine = [item for item in closed if item.draft == draft.id]
    assert [item.calls for item in mine] == [(send(chat_id, postings.SENT),)]
    assert await drafts(session, recent) == []


def test_help_lists_tailor() -> None:
    assert "/tailor - tailor a resume to a job posting you send next" in help_text(TIMEOUT)


def test_what_the_bot_says_once_its_tailored() -> None:
    """The draft's jobs send it (`test_tailor_jobs_db.py`)."""
    draft_id = uuid.uuid4()
    assert postings.failed_text(
        draft_id, "The posting couldn't be read because the model declined it.", None
    ) == (
        "The posting couldn't be read because the model declined it. You can try again on the "
        "Tailor page."
    )
    link = postings.draft_link("https://journal.example.com", draft_id)
    assert link == f"https://journal.example.com/tailor?draft={draft_id}"
