"""`/api/journal` for the signed-in owner, against the test database (T-041)."""

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import JournalEntry, JournalMessage, JournalMessageEdit, User
from app.db.profiles import save_version
from app.db.store import save_facts
from app.main import create_app
from app.schema.fact import Fact, Metric, SingleValue
from app.schema.profile import Profile

BROWSER = "https://testserver"
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
SEPTEMBER = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


def seed[T](database_url: str, build: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `build` in a transaction of its own, which commits, so the app sees its rows."""

    async def run() -> T:
        engine = create_async_engine(database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session, session.begin():
                return await build(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


class Owner:
    """A signed-in user of the test's own."""

    def __init__(self, database_url: str) -> None:
        self.login = f"owner-{uuid.uuid4().hex[:8]}"
        self.chat_id = _random.randrange(10**9, 10**12)

        async def sign_in(session: AsyncSession) -> tuple[uuid.UUID, str, str]:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login=self.login)
            session.add(user)
            await session.flush()
            token, row = start_session(session, user.id)
            return user.id, token, row.csrf_token

        self.id, self.session_token, self.csrf_token = seed(database_url, sign_in)


@pytest.fixture
def owner(database_url: str) -> Owner:
    return Owner(database_url)


@pytest.fixture
def client(database_url: str, owner: Owner) -> Iterator[TestClient]:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset({owner.login}),
    )
    cookies = {SESSION_COOKIE: owner.session_token}
    with TestClient(create_app(settings), base_url=BROWSER, cookies=cookies) as client:
        yield client


async def add_entry(
    session: AsyncSession,
    user_id: uuid.UUID,
    chat_id: int,
    opened_at: datetime,
    *texts: str,
    closed: bool = True,
    **state: Any,
) -> uuid.UUID:
    """An entry holding `texts`, one message a minute from `opened_at`."""
    last = opened_at + timedelta(minutes=len(texts) - 1)
    entry = JournalEntry(user_id=user_id, opened_at=opened_at, last_message_at=last, **state)
    if closed:
        entry.closed_at, entry.closed_by = last, "done"
    session.add(entry)
    await session.flush()
    for number, text in enumerate(texts):
        session.add(
            JournalMessage(
                user_id=user_id,
                chat_id=chat_id,
                message_id=_random.randrange(10**9),
                entry_id=entry.id,
                sender="owner",
                text=text,
                sent_at=opened_at + timedelta(minutes=number),
            )
        )
    await session.flush()
    return entry.id


def fact(entry_id: uuid.UUID, number: int, statement: str, **fields: Any) -> Fact:
    return Fact(
        id=f"{entry_id}_{number}",
        statement=statement,
        entry_id=str(entry_id),
        role_id="northwind",
        **fields,
    )


def citing(*fact_ids: str) -> Profile:
    """The fixture profile, with the export bullet citing `fact_ids`."""
    data = PROFILE.model_dump(by_alias=True)
    data["work"][0]["highlights"][1]["sources"] = list(fact_ids)
    return Profile.model_validate(data)


# --- Reading the journal -------------------------------------------------------------------------


def test_the_journal_needs_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/journal").status_code == 401


def test_an_empty_journal_lists_the_roles_and_projects_facts_can_link_to(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    assert client.get("/api/journal").json() == {"entries": [], "roles": [], "projects": []}

    async def load_profile(session: AsyncSession) -> None:
        await save_version(session, owner.id, PROFILE, author="owner")

    seed(database_url, load_profile)
    journal = client.get("/api/journal").json()
    assert journal["roles"] == [
        {"id": "northwind", "label": "Engineer, Northwind Traders (2025-now)"},
        {"id": "contoso", "label": "Research Intern, Contoso Research (2024-2024)"},
    ]
    assert journal["projects"] == [
        {"id": "recipe_box", "name": "Recipe Box"},
        {"id": "bird_calls", "name": "Bird Call Classifier"},
    ]


def test_the_journal_is_one_document_oldest_first_with_facts_inline(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """FR-JRN-5, and FR-JRN-3: each message keeps its earlier text."""

    async def build(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        october = await add_entry(
            session,
            owner.id,
            owner.chat_id,
            OCTOBER,
            "cut the nightly export to 12 min",
            "also wrote the runbook",
            role_id="northwind",
            triage="work",
            processed_at=OCTOBER + timedelta(hours=1),
        )
        september = await add_entry(
            session, owner.id, owner.chat_id, SEPTEMBER, "planning week", closed=False
        )
        message = await session.scalar(
            select(JournalMessage.id)
            .where(JournalMessage.entry_id == october)
            .order_by(JournalMessage.sent_at)
            .limit(1)
        )
        assert message is not None
        session.add(
            JournalMessageEdit(
                user_id=owner.id,
                message_id=message,
                text="cut the nightly export to 15 min",
                replaced_at=OCTOBER + timedelta(minutes=5),
            )
        )
        session.add(
            JournalMessage(
                user_id=owner.id,
                chat_id=owner.chat_id,
                message_id=_random.randrange(10**9),
                sender="bot",
                text="Saved 2 facts from this entry.",
                sent_at=OCTOBER + timedelta(hours=1),
            )
        )
        facts = [
            fact(october, number, f"Fact number {number}.")
            for number in (10, 2, 1, 3, 4, 5, 6, 7, 8, 9)
        ]
        await save_facts(session, owner.id, facts)
        return october, september, message

    october, september, message = seed(database_url, build)
    response = client.get("/api/journal")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    entries = response.json()["entries"]
    assert [entry["id"] for entry in entries] == [str(september), str(october)]
    assert entries[0] | {"messages": None} == {
        "id": str(september),
        "openedAt": "2026-09-28T18:00:00Z",
        "closedAt": None,
        "status": "open",
        "triage": None,
        "roleId": None,
        "newRole": None,
        "messages": None,
        "facts": [],
    }
    first, second = entries[1]["messages"]
    assert first == {
        "id": str(message),
        "sentAt": "2026-10-04T09:00:00Z",
        "editedAt": None,
        "text": "cut the nightly export to 12 min",
        "edits": [
            {"text": "cut the nightly export to 15 min", "replacedAt": "2026-10-04T09:05:00Z"}
        ],
    }
    assert second["text"] == "also wrote the runbook"
    assert entries[1]["status"] == "processed"
    assert [fact["statement"] for fact in entries[1]["facts"]] == [
        f"Fact number {number}." for number in range(1, 11)
    ], "facts keep their order in the entry"


def test_each_fact_lists_the_bullets_that_cite_it(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """FR-JRN-6: the web app warns before changing a fact the profile cites."""

    async def build(session: AsyncSession) -> uuid.UUID:
        entry = await add_entry(session, owner.id, owner.chat_id, OCTOBER, "export down to 12 min")
        metric = Metric(subject="export duration", value=SingleValue(value=12), unit="minutes")
        await save_facts(
            session,
            owner.id,
            [
                fact(entry, 1, "Cut the export to 12 minutes.", metrics=(metric,)),
                fact(entry, 2, "x."),
            ],
        )
        await save_version(session, owner.id, citing(f"{entry}_1"), author="owner")
        return entry

    entry = seed(database_url, build)
    cited, uncited = client.get("/api/journal").json()["entries"][0]["facts"]
    assert cited["id"] == f"{entry}_1"
    assert cited["metrics"][0]["value"] == {"kind": "single", "value": 12.0}
    assert cited["citedBy"] == [
        {
            "bulletId": "nw_export",
            "text": "Cut the nightly export job from 50 to 12 minutes by batching database writes.",
        }
    ]
    assert uncited["citedBy"] == []


def test_the_journal_shows_only_the_owners_entries(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> None:
        other = User()
        session.add(other)
        await session.flush()
        entry = await add_entry(session, other.id, owner.chat_id + 1, OCTOBER, "someone else")
        await save_facts(session, other.id, [fact(entry, 1, "Someone else's fact.")])

    seed(database_url, build)
    assert client.get("/api/journal").json()["entries"] == []
