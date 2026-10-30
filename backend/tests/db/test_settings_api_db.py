"""`/api/settings` for the signed-in owner, against the test database (T-048)."""

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.models import TelegramLink, User
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
from app.main import create_app
from app.schema.settings import (
    ConfidentialTerms,
    PortfolioSettings,
    ReminderSettings,
    ReminderState,
    StyleNote,
)

BROWSER = "https://testserver"
ADDED = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
_random = random.SystemRandom()


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
    rows."""

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
