"""`/api/settings` for the signed-in owner, against the test database (T-048)."""

import asyncio
import base64
import hashlib
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, key_ring, new_key, use_key_ring
from app.db.models import (
    ApplicationRow,
    Artifact,
    Base,
    ChangeOpRow,
    ChangeSetRow,
    JobPostingRow,
    JournalEntry,
    JournalMessage,
    JournalMessageEdit,
    LlmCallRow,
    Refresh,
    TelegramLink,
    TelegramUpdate,
    User,
    VariantRow,
)
from app.db.profiles import save_version
from app.db.settings import (
    add_style_notes,
    get_confidential_terms,
    get_portfolio_settings,
    get_reminder_settings,
    get_reminder_state,
    get_style_notes,
    save_portfolio_settings,
    save_reminder_settings,
    save_reminder_state,
)
from app.db.store import save_facts
from app.main import create_app
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.schema.settings import (
    ConfidentialTerms,
    PortfolioSettings,
    ReminderSettings,
    ReminderState,
    StyleNote,
)
from app.settings.data import NOT_EXPORTED, user_tables
from app.telegram.linking import new_link_token

BROWSER = "https://testserver"
ADDED = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
NOTE = "Cut the nightly export \N{EN DASH} 50 to 12 minutes"
"""A journal message, with a character the export must keep as it was sent."""
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def keys() -> Iterator[None]:
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
    """The tests commit, so the users each one makes are deleted afterwards, with all their
    rows, and so are the raw updates it keeps, which other tests' keys can't read."""

    async def existing(session: AsyncSession) -> tuple[set[uuid.UUID], set[int]]:
        users = set(await session.scalars(select(User.id)))
        return users, set(await session.scalars(select(TelegramUpdate.update_id)))

    users, updates = seed(database_url, existing)
    yield

    async def delete_new(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.not_in(users)))
        await session.execute(
            delete(TelegramUpdate).where(TelegramUpdate.update_id.not_in(updates))
        )

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


def client_for(database_url: str, owner: Owner, **settings: Any) -> TestClient:
    app_settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset({owner.login}),
        **settings,
    )
    cookies = {SESSION_COOKIE: owner.session_token}
    return TestClient(create_app(app_settings), base_url=BROWSER, cookies=cookies)


@pytest.fixture
def client(database_url: str, owner: Owner) -> Iterator[TestClient]:
    with client_for(database_url, owner) as client:
        yield client


def link_chat(database_url: str, owner: Owner) -> None:
    async def build(session: AsyncSession) -> None:
        session.add(TelegramLink(user_id=owner.id, chat_id=owner.chat_id))

    seed(database_url, build)


# --- Reading the settings ------------------------------------------------------------------------


def test_the_settings_need_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/settings").status_code == 401
    for group in ("reminders", "portfolio", "style-notes", "confidential-terms"):
        assert client.put(f"/api/settings/{group}", json={}).status_code == 401


def test_a_new_owner_sees_the_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    default = {"days": ["fri"], "time": "18:00", "timeZone": "Asia/Singapore"}
    assert response.json() == {
        "telegram": {"linked": False, "linkedAt": None},
        "reminders": {
            "schedule": default,
            "default": default,
            "chosen": False,
            "paused": False,
            "summary": "Fridays at 18:00 (Asia/Singapore)",
        },
        "portfolio": {
            "handle": None,
            "defaultVisibility": "public",
            "openToWork": False,
            "indexing": True,
        },
        "styleNotes": {"avoid": [], "notes": []},
        "confidentialTerms": {"terms": []},
    }


def test_the_settings_show_what_the_owner_set(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    link_chat(database_url, owner)

    async def build(session: AsyncSession) -> None:
        await save_reminder_settings(
            session, owner.id, ReminderSettings(time=time(9, 15), paused=True)
        )
        await save_portfolio_settings(session, owner.id, PortfolioSettings(open_to_work=True))
        await add_style_notes(
            session, owner.id, ["Say what it fixed"], source="rejection", now=ADDED
        )

    seed(database_url, build)
    settings = client.get("/api/settings").json()
    assert settings["telegram"]["linked"] is True
    assert datetime.fromisoformat(settings["telegram"]["linkedAt"]).tzinfo is not None
    assert settings["reminders"]["schedule"] == {
        "days": ["fri"],
        "time": "09:15",
        "timeZone": "Asia/Singapore",
    }
    assert (settings["reminders"]["chosen"], settings["reminders"]["paused"]) == (True, True)
    assert settings["portfolio"]["openToWork"] is True
    assert settings["styleNotes"]["notes"] == [
        {"text": "Say what it fixed", "source": "rejection", "addedAt": "2026-10-12T09:00:00Z"}
    ]


def test_the_default_schedule_comes_from_the_apps_settings(database_url: str, owner: Owner) -> None:
    defaults: dict[str, Any] = {
        "reminder_days": ("mon",),
        "reminder_time": time(8, 0),
        "reminder_time_zone": "Europe/London",
    }
    with client_for(database_url, owner, **defaults) as client:
        reminders = client.get("/api/settings").json()["reminders"]
    expected = {"days": ["mon"], "time": "08:00", "timeZone": "Europe/London"}
    assert reminders["schedule"] == reminders["default"] == expected


def test_the_settings_are_the_owners_own(database_url: str, owner: Owner) -> None:
    other = Owner(database_url)

    async def build(session: AsyncSession) -> None:
        await save_portfolio_settings(session, other.id, PortfolioSettings(open_to_work=True))

    seed(database_url, build)
    with client_for(database_url, owner) as client:
        assert client.get("/api/settings").json()["portfolio"]["openToWork"] is False


# --- Reminders -----------------------------------------------------------------------------------


def test_the_owner_sets_their_reminder_schedule_and_pause(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    body = {"days": ["thu", "mon"], "time": "07:30", "timeZone": "Europe/London", "paused": True}
    assert client.put("/api/settings/reminders", json=body).status_code == 403
    response = client.put("/api/settings/reminders", json=body, headers=owner.csrf)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "schedule": {"days": ["mon", "thu"], "time": "07:30", "timeZone": "Europe/London"},
        "default": {"days": ["fri"], "time": "18:00", "timeZone": "Asia/Singapore"},
        "chosen": True,
        "paused": True,
        "summary": "Mondays and Thursdays at 07:30 (Europe/London)",
    }

    async def stored(session: AsyncSession) -> ReminderSettings:
        return await get_reminder_settings(session, owner.id)

    assert seed(database_url, stored) == ReminderSettings(
        days=("mon", "thu"), time=time(7, 30), time_zone="Europe/London", paused=True
    )
    assert client.get("/api/settings").json()["reminders"] == response.json()


def test_nulls_go_back_to_the_default_schedule_and_the_record_stays(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """With a linked chat, the change takes the lock the bot's reminders take."""
    link_chat(database_url, owner)
    record = ReminderState(last_slot=datetime(2026, 10, 23, 10, 0, tzinfo=UTC), message_id=7)

    async def build(session: AsyncSession) -> None:
        await save_reminder_settings(session, owner.id, ReminderSettings(days=("mon",)))
        await save_reminder_state(session, owner.id, record)

    seed(database_url, build)
    body = {"days": None, "time": None, "timeZone": None, "paused": False}
    response = client.put("/api/settings/reminders", json=body, headers=owner.csrf)
    assert response.status_code == 200
    assert response.json()["chosen"] is False
    assert response.json()["schedule"] == response.json()["default"]

    async def stored(session: AsyncSession) -> tuple[ReminderSettings, ReminderState]:
        return (
            await get_reminder_settings(session, owner.id),
            await get_reminder_state(session, owner.id),
        )

    assert seed(database_url, stored) == (ReminderSettings(), record)


@pytest.mark.parametrize(
    "body",
    [
        {"days": []},
        {"days": ["friday"]},
        {"time": "18:00:30"},
        {"time": "25:00"},
        {"time": "18:00+08:00"},
        {"timeZone": "Mars/Olympus_Mons"},
        {"timeZone": "Asia"},
        {"timeZone": "../etc/passwd"},
        {"paused": "maybe"},
        {"snooze": True},
    ],
)
def test_a_schedule_that_cant_be_followed_is_refused(
    owner: Owner, client: TestClient, body: dict[str, object]
) -> None:
    response = client.put("/api/settings/reminders", json=body, headers=owner.csrf)
    assert response.status_code == 422, body


# --- Portfolio -----------------------------------------------------------------------------------


def test_the_owner_sets_their_portfolio_settings(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    body = {
        "handle": "Casey-Example",
        "defaultVisibility": "resume_only",
        "openToWork": True,
        "indexing": False,
    }
    assert client.put("/api/settings/portfolio", json=body).status_code == 403
    response = client.put("/api/settings/portfolio", json=body, headers=owner.csrf)
    assert response.status_code == 200
    assert response.json() == body | {"handle": "casey-example"}

    async def stored(session: AsyncSession) -> PortfolioSettings:
        return await get_portfolio_settings(session, owner.id)

    assert seed(database_url, stored) == PortfolioSettings(
        handle="casey-example", default_visibility="resume_only", open_to_work=True, indexing=False
    )
    cleared = client.put("/api/settings/portfolio", json={"handle": None}, headers=owner.csrf)
    assert cleared.json() == PortfolioSettings().model_dump(mode="json")


def test_a_handle_another_user_has_is_refused(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    other = Owner(database_url)
    handle = f"casey-{uuid.uuid4().hex[:8]}"

    async def build(session: AsyncSession) -> None:
        await save_portfolio_settings(session, other.id, PortfolioSettings(handle=handle))

    seed(database_url, build)
    taken = client.put("/api/settings/portfolio", json={"handle": handle}, headers=owner.csrf)
    assert taken.status_code == 409
    assert taken.json() == {"detail": "That handle is taken; choose another"}
    upper = client.put(
        "/api/settings/portfolio", json={"handle": handle.upper()}, headers=owner.csrf
    )
    assert upper.status_code == 409, "handles are compared in lowercase"
    mine = {"handle": f"{handle}-2"}
    assert client.put("/api/settings/portfolio", json=mine, headers=owner.csrf).status_code == 200
    again = client.put("/api/settings/portfolio", json=mine, headers=owner.csrf)
    assert again.status_code == 200, "the owner's own handle"


@pytest.mark.parametrize(
    "body",
    [
        {"handle": "casey example"},
        {"handle": "../admin"},
        {"handle": "c"},
        {"defaultVisibility": "hidden"},
        {"openToWork": "yes please"},
        {"published": True},
    ],
)
def test_portfolio_settings_that_cant_be_kept_are_refused(
    owner: Owner, client: TestClient, body: dict[str, object]
) -> None:
    assert client.put("/api/settings/portfolio", json=body, headers=owner.csrf).status_code == 422


# --- Style notes and confidential terms ----------------------------------------------------------


def test_the_owner_edits_their_style_notes(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def build(session: AsyncSession) -> None:
        await add_style_notes(
            session, owner.id, ["Keep the team's name out"], source="rejection", now=ADDED
        )

    seed(database_url, build)
    body = {
        "avoid": ["spearheaded", "Spearheaded", "synergy"],
        "notes": ["Keep the team\N{RIGHT SINGLE QUOTATION MARK}s name out", "Prefer plain verbs"],
    }
    assert client.put("/api/settings/style-notes", json=body).status_code == 403
    response = client.put("/api/settings/style-notes", json=body, headers=owner.csrf)
    assert response.status_code == 200
    saved = response.json()
    assert saved["avoid"] == ["spearheaded", "synergy"]
    kept, added = saved["notes"]
    assert kept == {
        "text": "Keep the team's name out",
        "source": "rejection",
        "addedAt": "2026-10-12T09:00:00Z",
    }
    assert (added["text"], added["source"]) == ("Prefer plain verbs", "owner")

    async def stored(session: AsyncSession) -> tuple[str, ...]:
        notes = await get_style_notes(session, owner.id)
        return (*notes.avoid, *(note.text for note in notes.notes))

    assert seed(database_url, stored) == (
        "spearheaded",
        "synergy",
        "Keep the team's name out",
        "Prefer plain verbs",
    )
    emptied = client.put("/api/settings/style-notes", json={}, headers=owner.csrf)
    assert emptied.json() == {"avoid": [], "notes": []}


def test_style_notes_that_cant_be_kept_are_refused(owner: Owner, client: TestClient) -> None:
    for body in ({"avoid": [" "]}, {"notes": ["x" * 501]}, {"notes": [{"text": "x"}]}):
        response = client.put("/api/settings/style-notes", json=body, headers=owner.csrf)
        assert response.status_code == 422, body


def test_the_owner_sets_their_confidential_terms(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    body = {"terms": ["Project Falcon", "project falcon", " Acme Labs "]}
    assert client.put("/api/settings/confidential-terms", json=body).status_code == 403
    response = client.put("/api/settings/confidential-terms", json=body, headers=owner.csrf)
    assert response.status_code == 200
    assert response.json() == {"terms": ["Project Falcon", "Acme Labs"]}

    async def stored(session: AsyncSession) -> ConfidentialTerms:
        return await get_confidential_terms(session, owner.id)

    assert seed(database_url, stored) == ConfidentialTerms(terms=("Project Falcon", "Acme Labs"))
    too_many = {"terms": [f"term {n}" for n in range(201)]}
    refused = client.put("/api/settings/confidential-terms", json=too_many, headers=owner.csrf)
    assert refused.status_code == 422


def test_a_rejection_reason_added_later_joins_the_notes(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """The page saves whole lists; the Inbox still adds a rejection's reason after them."""
    client.put("/api/settings/style-notes", json={"notes": ["Mine"]}, headers=owner.csrf)

    async def build(session: AsyncSession) -> None:
        await add_style_notes(
            session, owner.id, ["From a rejection"], source="rejection", now=ADDED
        )

    seed(database_url, build)
    notes = client.get("/api/settings").json()["styleNotes"]["notes"]
    assert [note["text"] for note in notes] == ["Mine", "From a rejection"]
    assert StyleNote.model_validate(notes[1]).source == "rejection"


# --- Export and delete all data ------------------------------------------------------------------


@dataclass(frozen=True)
class Filled:
    """What `fill_every_table` made."""

    entry_id: uuid.UUID
    refresh_id: uuid.UUID
    updates: tuple[int, ...]
    """Raw updates from the owner's chat."""
    other_update: int
    """A raw update from another chat."""


def update_payload(update_id: int, chat_id: int, *, press: bool = False) -> str:
    message = {
        "message_id": update_id % 1000,
        "chat": {"id": chat_id, "type": "private"},
        "date": 1790000000,
        "text": NOTE,
    }
    if press:
        query = {"id": str(update_id), "data": "+nothing", "message": message}
        return json.dumps({"update_id": update_id, "callback_query": query})
    return json.dumps({"update_id": update_id, "message": message})


def fill_every_table(database_url: str, owner: Owner) -> Filled:
    """A row of the owner's in every table that belongs to a user, and raw updates from their
    chat and from another."""

    async def build(session: AsyncSession) -> Filled:
        now = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
        session.add(TelegramLink(user_id=owner.id, chat_id=owner.chat_id))
        await new_link_token(session, owner.id)
        await save_portfolio_settings(session, owner.id, PortfolioSettings(handle=owner.login))
        entry = JournalEntry(
            user_id=owner.id, opened_at=now, last_message_at=now, closed_at=now, closed_by="done"
        )
        session.add(entry)
        await session.flush()
        message = JournalMessage(
            user_id=owner.id,
            chat_id=owner.chat_id,
            message_id=_random.randrange(10**9),
            entry_id=entry.id,
            sender="owner",
            text=NOTE,
            sent_at=now,
            edited_at=now,
        )
        session.add(message)
        await session.flush()
        session.add(
            JournalMessageEdit(
                user_id=owner.id, message_id=message.id, text="Cut the export", replaced_at=now
            )
        )
        fact = Fact(id=f"{entry.id}_1", statement="Cut the export to 12 minutes.")
        await save_facts(session, owner.id, [fact])
        change_set = ChangeSetRow(user_id=owner.id, author="ai", status="applied", base_version=1)
        session.add(change_set)
        await session.flush()
        session.add(
            ChangeOpRow(
                user_id=owner.id,
                change_set_id=change_set.id,
                position=0,
                op={"op": "update_field"},
                status="accepted",
            )
        )
        await save_version(session, owner.id, PROFILE, author="owner")
        await save_version(session, owner.id, PROFILE, author="ai", change_set_id=change_set.id)
        sha = hashlib.sha256(PDF).hexdigest()
        session.add(Artifact(user_id=owner.id, sha256=sha, media_type="application/pdf", data=PDF))
        await session.flush()
        refresh = Refresh(
            user_id=owner.id,
            status="proposed",
            finished_at=now,
            change_set_id=change_set.id,
            outputs="updated",
            output_version=2,
            outputs_at=now,
            master_pdf_sha256=sha,
            master_pages=1,
        )
        posting = JobPostingRow(user_id=owner.id, text="Backend engineer", posting={})
        session.add_all([refresh, posting, VariantRow(user_id=owner.id, id="master", data={})])
        await session.flush()
        session.add(
            ApplicationRow(
                user_id=owner.id,
                id="app_1",
                data={},
                applied_on=date(2026, 10, 30),
                posting_id=posting.id,
                pdf_sha256=sha,
            )
        )
        session.add(
            LlmCallRow(
                user_id=owner.id,
                at=now,
                task="extraction",
                tier="standard",
                model="model-standard",
                prompt="extraction@1",
                outcome="ok",
                attempts=1,
                input_tokens=10,
                output_tokens=5,
                cost_usd=Decimal("0.001234"),
                latency_ms=900,
            )
        )
        first = _random.randrange(10**9, 10**10)
        ids = (first, first + 1, first + 2)
        payloads = (
            update_payload(ids[0], owner.chat_id),
            update_payload(ids[1], owner.chat_id, press=True),
            update_payload(ids[2], owner.chat_id + 1),
        )
        session.add_all(
            [TelegramUpdate(update_id=i, payload=p) for i, p in zip(ids, payloads, strict=True)]
        )
        await session.flush()
        return Filled(entry.id, refresh.id, ids[:2], ids[2])

    return seed(database_url, build)


def add_job(database_url: str, task: str, status: str, **args: str) -> int:
    async def build(session: AsyncSession) -> int:
        statement = text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) VALUES"
            " ('default', :task, CAST(:args AS jsonb), CAST(:status AS procrastinate_job_status))"
            " RETURNING id"
        )
        values = {"task": task, "args": json.dumps(args), "status": status}
        return (await session.execute(statement, values)).scalar_one()

    return seed(database_url, build)


def jobs_left(database_url: str, ids: list[int]) -> set[int]:
    async def build(session: AsyncSession) -> set[int]:
        statement = text("SELECT id FROM procrastinate_jobs WHERE id = ANY(:ids)")
        return set((await session.execute(statement, {"ids": ids})).scalars())

    return seed(database_url, build)


def delete_jobs(database_url: str, ids: list[int]) -> None:
    async def build(session: AsyncSession) -> None:
        statement = text("DELETE FROM procrastinate_jobs WHERE id = ANY(:ids)")
        await session.execute(statement, {"ids": ids})

    seed(database_url, build)


def rows_of(database_url: str, user_id: uuid.UUID) -> dict[str, int]:
    """How many rows the user has in `users` and in each table that belongs to a user."""

    async def build(session: AsyncSession) -> dict[str, int]:
        counts: dict[str, int] = {}
        users = Base.metadata.tables["users"]
        query = select(func.count()).select_from(users).where(users.c.id == user_id)
        counts["users"] = (await session.execute(query)).scalar_one()
        for table in user_tables():
            query = select(func.count()).select_from(table).where(table.c.user_id == user_id)
            counts[table.name] = (await session.execute(query)).scalar_one()
        return counts

    return seed(database_url, build)


def updates_left(database_url: str, ids: tuple[int, ...]) -> set[int]:
    async def build(session: AsyncSession) -> set[int]:
        query = select(TelegramUpdate.update_id).where(TelegramUpdate.update_id.in_(ids))
        return set(await session.scalars(query))

    return seed(database_url, build)


def test_the_tests_fill_every_table_that_belongs_to_a_user(database_url: str, owner: Owner) -> None:
    """So the export and deletion tests cover a table added later, once it's filled here."""
    fill_every_table(database_url, owner)
    counts = rows_of(database_url, owner.id)
    assert [name for name, count in counts.items() if count == 0] == []
    every = {table.name for table in Base.metadata.sorted_tables} - {"telegram_updates"}
    assert counts.keys() == every


def test_the_export_holds_every_row_of_the_owners(
    database_url: str, owner: Owner, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    filled = fill_every_table(database_url, owner)
    other = Owner(database_url)
    fill_every_table(database_url, other)
    caplog.set_level(logging.DEBUG)
    response = client.get("/api/settings/export.json")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    today = datetime.now(UTC).date()
    disposition = f'attachment; filename="work-journal-data-{today:%Y-%m-%d}.json"'
    assert response.headers["Content-Disposition"] == disposition
    export = response.json()
    assert (export["format"], export["version"]) == ("work-journal-export", 1)
    assert datetime.fromisoformat(export["exported_at"]).date() == today
    assert export["account"]["id"] == str(owner.id)
    assert export["account"]["github_login"] == owner.login
    tables = export["tables"]
    expected = {table.name for table in user_tables()} - NOT_EXPORTED | {"telegram_updates"}
    assert tables.keys() == expected
    assert [name for name in expected if not tables[name]] == [], "a row in each"
    assert all("user_id" not in row for rows in tables.values() for row in rows)
    assert owner.session_token not in response.text
    assert owner.csrf_token not in response.text

    [message] = tables["journal_messages"]
    assert message["text"] == NOTE, "decrypted, as it was sent"
    assert message["entry_id"] == str(filled.entry_id)
    assert tables["journal_message_edits"][0]["text"] == "Cut the export"
    assert tables["facts"][0]["data"]["statement"] == "Cut the export to 12 minutes."
    assert [version["number"] for version in tables["profile_versions"]] == [1, 2]
    assert tables["profile_versions"][0]["profile"]["basics"]["name"] == PROFILE.basics.name
    assert base64.b64decode(tables["artifacts"][0]["data"]) == PDF
    assert tables["llm_calls"][0]["cost_usd"] == "0.001234"
    assert tables["refreshes"][0]["id"] == str(filled.refresh_id)
    assert tables["settings"] == [
        {
            "key": "portfolio",
            "value": PortfolioSettings(handle=owner.login).model_dump(mode="json"),
            "updated_at": tables["settings"][0]["updated_at"],
        }
    ]
    updates = tables["telegram_updates"]
    assert [update["update_id"] for update in updates] == list(filled.updates)
    assert updates[0]["payload"]["message"]["text"] == NOTE
    assert str(other.id) not in response.text, "nothing of another user's"
    assert other.login not in response.text
    assert NOTE not in caplog.text


def test_the_export_needs_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/settings/export.json").status_code == 401


def test_an_owner_with_nothing_yet_exports_empty_tables(client: TestClient) -> None:
    tables = client.get("/api/settings/export.json").json()["tables"]
    assert tables["journal_messages"] == []
    assert tables["telegram_updates"] == []


def test_deleting_all_data_needs_the_csrf_header_and_the_owners_username(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    fill_every_table(database_url, owner)
    body = {"confirm": owner.login}
    assert client.request("DELETE", "/api/settings/data", json=body).status_code == 403
    for wrong in ({"confirm": "someone-else"}, {"confirm": ""}, {}):
        refused = client.request("DELETE", "/api/settings/data", json=wrong, headers=owner.csrf)
        assert refused.status_code == 422, wrong
    assert rows_of(database_url, owner.id)["journal_messages"] == 1, "nothing deleted"
    assert client.get("/api/settings").status_code == 200


def test_deleting_all_data_leaves_nothing_of_the_owners(
    database_url: str, owner: Owner, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    filled = fill_every_table(database_url, owner)
    other = Owner(database_url)
    other_filled = fill_every_table(database_url, other)
    entry, refresh = str(filled.entry_id), str(filled.refresh_id)
    jobs = {
        "entry": add_job(database_url, "extract_entry", "todo", entry_id=entry),
        "refresh": add_job(database_url, "refresh_profile", "failed", refresh_id=refresh),
        "running": add_job(database_url, "update_outputs", "doing", refresh_id=refresh),
        "other": add_job(
            database_url, "extract_entry", "todo", entry_id=str(other_filled.entry_id)
        ),
    }
    caplog.set_level(logging.DEBUG)
    try:
        body = {"confirm": f"  {owner.login.upper()} "}
        response = client.request("DELETE", "/api/settings/data", json=body, headers=owner.csrf)
        assert response.status_code == 204
        assert response.headers["Cache-Control"] == "no-store"
        cookies = response.headers.get_list("set-cookie")
        assert any(c.startswith('__Host-wj_session=""') and "Max-Age=0" in c for c in cookies)
        assert any(c.startswith('__Host-wj_csrf=""') and "Max-Age=0" in c for c in cookies)

        assert set(rows_of(database_url, owner.id).values()) == {0}
        everything = (*filled.updates, filled.other_update)
        assert updates_left(database_url, everything) == {filled.other_update}
        left = jobs_left(database_url, list(jobs.values()))
        assert left == {jobs["running"], jobs["other"]}, "only a running job, and another's"
        assert 0 not in rows_of(database_url, other.id).values(), "another user's rows stay"
        assert updates_left(database_url, other_filled.updates) == set(other_filled.updates)
        assert client.get("/api/settings").status_code == 401, "signed out"
        assert NOTE not in caplog.text
    finally:
        delete_jobs(database_url, list(jobs.values()))


def test_an_account_with_nothing_in_it_can_be_deleted(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    body = {"confirm": owner.login}
    response = client.request("DELETE", "/api/settings/data", json=body, headers=owner.csrf)
    assert response.status_code == 204
    assert rows_of(database_url, owner.id)["users"] == 0


def test_a_raw_update_the_keys_cant_read_is_passed_over(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """It can't be told whose it is, so it stays until it's 7 days old."""
    link_chat(database_url, owner)
    unreadable = _random.randrange(10**9, 10**10)
    readable = unreadable + 1
    ours = key_ring()
    use_key_ring(KeyRing.parse(new_key("k1")))

    async def build(session: AsyncSession, update_id: int) -> None:
        payload = update_payload(update_id, owner.chat_id)
        session.add(TelegramUpdate(update_id=update_id, payload=payload))

    seed(database_url, lambda session: build(session, unreadable))
    use_key_ring(ours)
    seed(database_url, lambda session: build(session, readable))
    updates = client.get("/api/settings/export.json").json()["tables"]["telegram_updates"]
    assert [update["update_id"] for update in updates] == [readable]
    body = {"confirm": owner.login}
    response = client.request("DELETE", "/api/settings/data", json=body, headers=owner.csrf)
    assert response.status_code == 204
    assert updates_left(database_url, (unreadable, readable)) == {unreadable}
