"""`/api/journal` for the signed-in owner, against the test database (T-041)."""

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import FactRow, JournalEntry, JournalMessage, JournalMessageEdit, User
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
EXPORT_BULLET = {
    "bulletId": "nw_export",
    "text": "Cut the nightly export job from 50 to 12 minutes by batching database writes.",
}
"""The fixture profile's bullet that `citing` makes cite facts."""
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


@pytest.fixture(autouse=True)
def delete_users_made(database_url: str) -> Iterator[None]:
    """The tests commit, so the users each one makes are deleted afterwards, with all their rows:
    later tests, such as key rotation's, then see no journal text under this test's key."""

    async def users(session: AsyncSession) -> set[uuid.UUID]:
        return set(await session.scalars(select(User.id)))

    before = seed(database_url, users)
    yield

    async def delete_new(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.not_in(before)))

    seed(database_url, delete_new)


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

    @property
    def csrf(self) -> dict[str, str]:
        """The header that requests changing something must carry."""
        return {CSRF_HEADER: self.csrf_token}


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
    fields = {"role_id": "northwind"} | fields
    return Fact(id=f"{entry_id}_{number}", statement=statement, entry_id=str(entry_id), **fields)


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
    assert cited["citedBy"] == [EXPORT_BULLET]
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


# --- Correcting messages and entries -------------------------------------------------------------


def message_ids(database_url: str, entry_id: uuid.UUID) -> list[uuid.UUID]:
    async def query(session: AsyncSession) -> list[uuid.UUID]:
        ids = await session.scalars(
            select(JournalMessage.id)
            .where(JournalMessage.entry_id == entry_id)
            .order_by(JournalMessage.sent_at)
        )
        return list(ids)

    return seed(database_url, query)


def count(database_url: str, table: Any, *where: Any) -> int:
    async def query(session: AsyncSession) -> int:
        return await session.scalar(select(func.count()).select_from(table).where(*where)) or 0

    return seed(database_url, query)


@pytest.fixture
def entry(database_url: str, owner: Owner) -> uuid.UUID:
    """A processed entry of two messages, with two facts. The profile cites the first."""

    async def build(session: AsyncSession) -> uuid.UUID:
        entry = await add_entry(
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
        await save_facts(
            session,
            owner.id,
            [fact(entry, 1, "Cut the export to 12 minutes."), fact(entry, 2, "Wrote a runbook.")],
        )
        await save_version(session, owner.id, citing(f"{entry}_1"), author="owner")
        return entry

    return seed(database_url, build)


def test_editing_a_message_keeps_its_earlier_text(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    """FR-JRN-6, with the history of FR-JRN-3. The text is stored in ASCII characters."""
    first, _ = message_ids(database_url, entry)
    response = client.patch(
        f"/api/journal/messages/{first}",
        json={"text": "  cut the nightly export to 13 min \u2013 not 12  "},
        headers=owner.csrf,
    )
    assert response.status_code == 200
    edited = response.json()
    assert edited["text"] == "cut the nightly export to 13 min - not 12"
    assert [edit["text"] for edit in edited["edits"]] == ["cut the nightly export to 12 min"]
    assert edited["editedAt"] == edited["edits"][0]["replacedAt"]
    shown = client.get("/api/journal").json()["entries"][0]["messages"][0]
    assert shown == edited
    again = client.patch(
        f"/api/journal/messages/{first}", json={"text": edited["text"]}, headers=owner.csrf
    )
    assert again.json() == edited, "the same text adds no history"


def test_text_from_telegram_that_differs_only_in_its_characters_is_not_an_edit(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """Telegram text is stored as sent: an en dash and spaces around it aren't a change."""

    async def build(session: AsyncSession) -> uuid.UUID:
        return await add_entry(session, owner.id, owner.chat_id, OCTOBER, " 2\u20133 hours \n")

    (message,) = message_ids(database_url, seed(database_url, build))
    response = client.patch(
        f"/api/journal/messages/{message}", json={"text": "2-3 hours"}, headers=owner.csrf
    )
    assert response.json()["edits"] == []


def test_a_message_edit_needs_text_and_the_csrf_header(
    owner: Owner, client: TestClient, entry: uuid.UUID, database_url: str
) -> None:
    first, _ = message_ids(database_url, entry)
    path = f"/api/journal/messages/{first}"
    assert client.patch(path, json={"text": "new"}).status_code == 403
    assert client.patch(path, json={"text": "   "}, headers=owner.csrf).status_code == 422
    too_long = {"text": "x" * 4097}
    assert client.patch(path, json=too_long, headers=owner.csrf).status_code == 422


def test_only_the_owners_own_messages_can_be_changed(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> list[uuid.UUID]:
        other = User()
        session.add(other)
        await session.flush()
        theirs = await add_entry(session, other.id, owner.chat_id + 1, OCTOBER, "a", "b")
        bot = JournalMessage(
            user_id=owner.id,
            chat_id=owner.chat_id,
            message_id=_random.randrange(10**9),
            sender="bot",
            text="Saved 2 facts from this entry.",
            sent_at=OCTOBER,
        )
        session.add(bot)
        await session.flush()
        others = await session.scalars(
            select(JournalMessage.id).where(JournalMessage.entry_id == theirs)
        )
        return [*others, bot.id]

    for message_id in [*seed(database_url, build), uuid.uuid4()]:
        path = f"/api/journal/messages/{message_id}"
        assert client.patch(path, json={"text": "x"}, headers=owner.csrf).status_code == 404
        assert client.delete(path, headers=owner.csrf).status_code == 404


def test_a_message_can_be_deleted_but_not_its_entrys_only_one(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    first, second = message_ids(database_url, entry)
    assert client.delete(f"/api/journal/messages/{first}").status_code == 403
    assert client.delete(f"/api/journal/messages/{first}", headers=owner.csrf).status_code == 204
    assert message_ids(database_url, entry) == [second]
    only = client.delete(f"/api/journal/messages/{second}", headers=owner.csrf)
    assert only.status_code == 409
    assert "delete the entry instead" in only.json()["detail"]


def test_deleting_an_entry_deletes_its_messages_and_facts(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        gone = await add_entry(session, owner.id, owner.chat_id, OCTOBER, "one", "two")
        kept = await add_entry(session, owner.id, owner.chat_id, SEPTEMBER, "three")
        await save_facts(
            session,
            owner.id,
            [fact(gone, 1, "Gone."), fact(gone, 2, "Gone."), fact(kept, 1, "Kept.")],
        )
        return gone, kept

    gone, kept = seed(database_url, build)
    first, _ = message_ids(database_url, gone)
    client.patch(f"/api/journal/messages/{first}", json={"text": "uno"}, headers=owner.csrf)
    assert client.delete(f"/api/journal/entries/{gone}").status_code == 403
    assert client.delete(f"/api/journal/entries/{gone}", headers=owner.csrf).status_code == 204
    assert [e["id"] for e in client.get("/api/journal").json()["entries"]] == [str(kept)]
    assert count(database_url, JournalMessage, JournalMessage.user_id == owner.id) == 1
    assert count(database_url, JournalMessageEdit, JournalMessageEdit.user_id == owner.id) == 0
    assert count(database_url, FactRow, FactRow.user_id == owner.id) == 1
    again = client.delete(f"/api/journal/entries/{gone}", headers=owner.csrf)
    assert again.status_code == 404


def test_deleting_an_entry_with_cited_facts_needs_confirming(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    """FR-JRN-6: the profile cites the entry's first fact."""
    refused = client.delete(f"/api/journal/entries/{entry}", headers=owner.csrf)
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "1 bullet in your profile cites this; confirm to delete anyway"
    )
    assert count(database_url, JournalEntry, JournalEntry.id == entry) == 1
    confirmed = client.delete(f"/api/journal/entries/{entry}?confirm=true", headers=owner.csrf)
    assert confirmed.status_code == 204
    assert count(database_url, JournalEntry, JournalEntry.id == entry) == 0
    assert count(database_url, FactRow, FactRow.user_id == owner.id) == 0


# --- Correcting facts ----------------------------------------------------------------------------


def edited(**changes: Any) -> dict[str, Any]:
    """A fact edit, with `changes` to its fields."""
    return {
        "statement": "Cut the nightly export from 50 to 13 minutes.",
        "kind": "accomplishment",
        "metrics": [
            {
                "subject": "export duration",
                "value": {"kind": "change", "before": 50, "after": 13},
                "unit": "minutes",
                "qualifier": "approximately",
            }
        ],
        "ownership": "owned",
        "tools": ["PostgreSQL"],
        "outcome": "Reports are ready by 9am.",
        "date": "2026-10",
        "roleId": "northwind",
        "projectId": None,
        "proposedRole": None,
    } | changes


def test_editing_a_fact_changes_its_fields_and_keeps_where_it_came_from(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    """FR-EXT-3. The profile still cites the fact; its bullets wait for a refresh."""
    response = client.put(f"/api/journal/facts/{entry}_1", json=edited(), headers=owner.csrf)
    assert response.status_code == 200
    fact = response.json()
    assert fact == edited() | {
        "id": f"{entry}_1",
        "entryId": str(entry),
        "origin": "journal",
        "metrics": [
            edited()["metrics"][0] | {"value": {"kind": "change", "before": 50.0, "after": 13.0}}
        ],
        "proposedItem": None,
        "questionId": None,
        "confidentialityFlags": [],
        "citedBy": [EXPORT_BULLET],
    }
    assert client.get("/api/journal").json()["entries"][0]["facts"][0] == fact


def test_a_fact_links_only_to_roles_and_projects_in_the_profile(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    path = f"/api/journal/facts/{entry}_2"

    def put(**changes: Any) -> int:
        return client.put(path, json=edited(**changes), headers=owner.csrf).status_code

    assert put(roleId="contoso", projectId="recipe_box") == 200
    assert put(roleId="nowhere") == 422
    assert put(projectId="nowhere") == 422
    assert put(roleId="contoso", proposedRole="Data Engineer, Example Co") == 422
    assert put(roleId=None, proposedRole="Data Engineer, Example Co") == 200
    assert put(roleId=None, proposedRole=None) == 200


def test_a_fact_keeps_a_link_that_left_the_profile(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> uuid.UUID:
        await save_version(session, owner.id, PROFILE, author="owner")
        entry = await add_entry(session, owner.id, owner.chat_id, OCTOBER, "note")
        await save_facts(
            session, owner.id, [fact(entry, 1, "Old.", role_id="gone", project_id="old")]
        )
        return entry

    entry = seed(database_url, build)
    response = client.put(
        f"/api/journal/facts/{entry}_1",
        json=edited(roleId="gone", projectId="old"),
        headers=owner.csrf,
    )
    assert response.status_code == 200


def test_a_fact_edit_must_be_a_whole_valid_fact(
    owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    path = f"/api/journal/facts/{entry}_2"
    assert client.put(path, json=edited()).status_code == 403
    invalid: list[dict[str, Any]] = [
        edited(statement=" "),
        edited(tools=["Python", ""]),
        edited(date="2026-13"),
        edited(kind="rumour"),
        {k: v for k, v in edited().items() if k != "outcome"},
    ]
    for body in invalid:
        assert client.put(path, json=body, headers=owner.csrf).status_code == 422, body


def test_only_the_owners_own_facts_can_be_changed(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> str:
        other = User()
        session.add(other)
        await session.flush()
        theirs = await add_entry(session, other.id, owner.chat_id + 1, OCTOBER, "a")
        await save_facts(session, other.id, [fact(theirs, 1, "Theirs.")])
        return f"{theirs}_1"

    for fact_id in (seed(database_url, build), "no_such_fact"):
        path = f"/api/journal/facts/{fact_id}"
        assert client.put(path, json=edited(), headers=owner.csrf).status_code == 404
        assert client.delete(path, headers=owner.csrf).status_code == 404


def test_deleting_a_fact_the_profile_cites_needs_confirming(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    """FR-JRN-6 and FR-EXT-3."""
    assert client.delete(f"/api/journal/facts/{entry}_2").status_code == 403
    assert client.delete(f"/api/journal/facts/{entry}_2", headers=owner.csrf).status_code == 204
    refused = client.delete(f"/api/journal/facts/{entry}_1", headers=owner.csrf)
    assert refused.status_code == 409
    assert "1 bullet in your profile cites this" in refused.json()["detail"]
    assert count(database_url, FactRow, FactRow.user_id == owner.id) == 1
    confirmed = client.delete(f"/api/journal/facts/{entry}_1?confirm=true", headers=owner.csrf)
    assert confirmed.status_code == 204
    assert count(database_url, FactRow, FactRow.user_id == owner.id) == 0
    assert client.get("/api/journal").json()["entries"][0]["facts"] == []


# --- Export --------------------------------------------------------------------------------------


def test_the_json_export_holds_every_entry_and_fact(
    database_url: str, owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    """FR-JRN-7, with the facts from the imported resume, which belong to no entry."""

    async def imported(session: AsyncSession) -> None:
        await save_facts(session, owner.id, [Fact(id="nw_orders", statement="x.", origin="import")])

    seed(database_url, imported)
    response = client.get("/api/journal/export.json")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    today = datetime.now(UTC).date()
    disposition = f'attachment; filename="work-journal-{today:%Y-%m-%d}.json"'
    assert response.headers["Content-Disposition"] == disposition
    export = response.json()
    journal = client.get("/api/journal").json()
    assert {key: export[key] for key in journal} == journal
    assert [fact["id"] for fact in export["importedFacts"]] == ["nw_orders"]
    assert datetime.fromisoformat(export["exportedAt"]).date() == today


def test_the_markdown_export_is_in_the_owners_time_zone(
    owner: Owner, client: TestClient, entry: uuid.UUID
) -> None:
    response = client.get("/api/journal/export.md", params={"tz": "Asia/Singapore"})
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert response.headers["Content-Disposition"].startswith('attachment; filename="work-journal-')
    markdown = response.text
    assert "## October 2026\n\n### Sun 4 Oct 2026, 17:00\n" in markdown
    assert "- Cut the export to 12 minutes.\n" in markdown
    assert "(Asia/Singapore)" in markdown
    assert "(UTC)" in client.get("/api/journal/export.md").text


@pytest.mark.parametrize("zone", ["", "Nowhere/City", "Asia", "../etc/passwd", "/etc/passwd"])
def test_the_markdown_export_needs_a_real_time_zone(client: TestClient, zone: str) -> None:
    assert client.get("/api/journal/export.md", params={"tz": zone}).status_code == 422


def test_the_exports_need_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/journal/export.json").status_code == 401
    assert client.get("/api/journal/export.md").status_code == 401


def test_a_thousand_entries_export_within_ten_seconds(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """FR-JRN-7. Each entry has two messages and two facts."""

    async def build(session: AsyncSession) -> None:
        entries = [
            JournalEntry(
                id=uuid.uuid4(),
                user_id=owner.id,
                opened_at=OCTOBER - timedelta(days=number),
                last_message_at=OCTOBER - timedelta(days=number),
                closed_at=OCTOBER - timedelta(days=number),
                closed_by="done",
                role_id="northwind",
                triage="work",
                processed_at=OCTOBER,
            )
            for number in range(1000)
        ]
        session.add_all(entries)
        await session.flush()
        session.add_all(
            JournalMessage(
                user_id=owner.id,
                chat_id=owner.chat_id,
                message_id=_random.randrange(10**12),
                entry_id=entry.id,
                sender="owner",
                text=f"note {part} about the nightly export, down from ~50 to 12 minutes",
                sent_at=entry.opened_at + timedelta(minutes=part),
            )
            for entry in entries
            for part in range(2)
        )
        await save_facts(
            session,
            owner.id,
            [
                fact(entry.id, number, "Cut the nightly export from about 50 to 12 minutes.")
                for entry in entries
                for number in (1, 2)
            ],
        )

    seed(database_url, build)
    for path in ("/api/journal/export.md", "/api/journal/export.json"):
        started = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - started
        assert response.status_code == 200
        assert elapsed < 10, f"{path} took {elapsed:.1f} s"
    assert "1,000 entries and 2,000 facts" in client.get("/api/journal/export.md").text
