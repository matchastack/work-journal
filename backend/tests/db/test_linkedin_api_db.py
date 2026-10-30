"""`/api/linkedin` for the signed-in owner, against the test database (T-047)."""

import asyncio
import hashlib
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.models import User
from app.db.profiles import save_version
from app.db.settings import get_linkedin_done, save_linkedin_done, save_linkedin_pack
from app.linkedin import DoneSection, LinkedInDone, LinkedInPack, PackSection
from app.main import create_app
from app.schema.profile import Profile
from app.schema.verification import VerifierFinding, VerifierReport

BROWSER = "https://testserver"
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
HEADLINE = "Software Engineer | Python services and data tooling"
ABOUT = "I build Python services at Northwind Traders.\n\nBefore, I built research tools."
NORTHWIND = "I build Python services with Kafka."
CONTOSO = "I built a Python tool that labelled sensor logs."
NORTHWIND_LABEL = "Position: Software Engineer at Northwind Traders"
CONTOSO_LABEL = "Position: Research Intern at Contoso Labs"
TITLE_NOTE = 'LinkedIn shows "Software Engineer" for Northwind Traders.'
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
    """A signed-in user of the test's own, with the fixture profile loaded as version 1."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.login = f"owner-{uuid.uuid4().hex[:8]}"

        async def sign_in(session: AsyncSession) -> tuple[uuid.UUID, str, str]:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login=self.login)
            session.add(user)
            await session.flush()
            await save_version(session, user.id, PROFILE, author="owner")
            token, row = start_session(session, user.id)
            return user.id, token, row.csrf_token

        self.id, self.session_token, self.csrf_token = seed(database_url, sign_in)

    @property
    def csrf(self) -> dict[str, str]:
        return {CSRF_HEADER: self.csrf_token}

    def save_pack(self, pack: LinkedInPack) -> None:
        async def save(session: AsyncSession) -> None:
            await save_linkedin_pack(session, self.id, pack)

        seed(self.database_url, save)

    def save_done(self, done: LinkedInDone) -> None:
        async def save(session: AsyncSession) -> None:
            await save_linkedin_done(session, self.id, done)

        seed(self.database_url, save)

    def done(self) -> LinkedInDone | None:
        async def get(session: AsyncSession) -> LinkedInDone | None:
            return await get_linkedin_done(session, self.id)

        return seed(self.database_url, get)


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


def report(text: str, *errors: str) -> VerifierReport:
    findings = tuple(
        VerifierFinding(check="claims", severity="error", code="added_tool", message=error)
        for error in errors
    )
    return VerifierReport(text=text, passed=not errors, findings=findings)


def section(key: str, text: str, *errors: str, **fields: Any) -> PackSection:
    kind = key.split(":")[0]
    labels = {"headline": "Headline", "about": "About"}
    data: dict[str, Any] = {
        "key": key,
        "kind": kind,
        "label": labels.get(key, NORTHWIND_LABEL if key == "position:northwind" else CONTOSO_LABEL),
        "text": text,
        "source": f"source-{key}",
        "changed": True,
        "report": report(text, *errors),
    }
    return PackSection.model_validate(data | fields)


def first_pack() -> LinkedInPack:
    """A pack written for the first time: four sections, one of them flagged."""
    return LinkedInPack(
        sections=(
            section("headline", HEADLINE),
            section("about", ABOUT),
            section("position:northwind", NORTHWIND, "No fact names Kafka.", notes=(TITLE_NOTE,)),
            section("position:contoso", CONTOSO),
        ),
        profile_version=1,
    )


def mark(client: TestClient, owner: Owner, digest: str) -> Any:
    return client.post("/api/linkedin/done", json={"digest": digest}, headers=owner.csrf)


def test_the_page_needs_a_signed_in_owner_and_marking_needs_the_csrf_header(
    owner: Owner, client: TestClient
) -> None:
    owner.save_pack(first_pack())
    digest = client.get("/api/linkedin").json()["pack"]["digest"]
    assert client.post("/api/linkedin/done", json={"digest": digest}).status_code == 403
    assert mark(client, owner, digest.upper()).status_code == 422
    assert mark(client, owner, digest + "0").status_code == 422
    assert owner.done() is None
    client.cookies.clear()
    assert client.get("/api/linkedin").status_code == 401
    assert mark(client, owner, digest).status_code == 401


def test_before_the_first_pack_there_is_nothing_to_paste_or_mark(
    owner: Owner, client: TestClient
) -> None:
    response = client.get("/api/linkedin")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"pack": None, "profileVersion": 1, "markedAt": None}
    refused = mark(client, owner, "0" * 64)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "there's no LinkedIn pack to mark done"


def test_a_first_pack_lists_every_section_with_its_length_limit_and_report(
    owner: Owner, client: TestClient
) -> None:
    """FR-LIN-1, FR-LIN-3."""
    owner.save_pack(first_pack())
    page = client.get("/api/linkedin").json()
    assert page["profileVersion"] == 1
    assert page["markedAt"] is None
    pack = page["pack"]
    assert (pack["profileVersion"], pack["unchanged"], pack["removed"]) == (1, 0, [])
    assert len(pack["digest"]) == 64
    headline, about, northwind, contoso = pack["sections"]
    assert headline | {"report": None} == {
        "key": "headline",
        "kind": "headline",
        "label": "Headline",
        "text": HEADLINE,
        "length": len(HEADLINE),
        "limit": 220,
        "flagged": False,
        "report": None,
        "notes": [],
    }
    assert headline["report"]["passed"] is True
    assert (about["limit"], contoso["limit"]) == (2600, 2000)
    assert (northwind["label"], northwind["flagged"]) == (NORTHWIND_LABEL, True)
    assert northwind["report"]["findings"][0]["message"] == "No fact names Kafka."
    assert northwind["notes"] == [TITLE_NOTE]


def test_marking_done_stores_the_snapshot_and_leaves_only_flagged_sections_listed(
    owner: Owner, client: TestClient
) -> None:
    """FR-LIN-2, FR-LIN-3."""
    owner.save_pack(first_pack())
    digest = client.get("/api/linkedin").json()["pack"]["digest"]
    response = mark(client, owner, digest)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"marked": 3, "flagged": [NORTHWIND_LABEL]}
    done = owner.done()
    assert done is not None and done.marked_at is not None
    assert [(s.key, s.text, s.source) for s in done.sections] == [
        ("headline", HEADLINE, "source-headline"),
        ("about", ABOUT, "source-about"),
        ("position:contoso", CONTOSO, "source-position:contoso"),
    ]
    page = client.get("/api/linkedin").json()
    assert page["markedAt"] == done.marked_at.isoformat().replace("+00:00", "Z")
    assert [s["key"] for s in page["pack"]["sections"]] == ["position:northwind"]
    assert page["pack"]["unchanged"] == 3


def test_a_pack_written_since_the_page_loaded_is_not_marked(
    owner: Owner, client: TestClient
) -> None:
    owner.save_pack(first_pack())
    digest = client.get("/api/linkedin").json()["pack"]["digest"]
    rewritten = first_pack().model_copy(update={"profile_version": 2})
    owner.save_pack(rewritten)
    refused = mark(client, owner, digest)
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "the pack was written again since the page loaded; check its sections first"
    )
    assert owner.done() is None
    assert client.get("/api/linkedin").json()["pack"]["digest"] != digest


def test_a_later_pack_lists_only_what_changed_and_what_to_take_off(
    owner: Owner, client: TestClient
) -> None:
    """FR-LIN-2: sections are compared with the snapshot last marked done."""
    owner.save_done(
        LinkedInDone(
            sections=(
                DoneSection(key="headline", label="Headline", text=HEADLINE, source="h"),
                DoneSection(key="about", label="About", text="Earlier About.", source="a"),
                DoneSection(key="position:old", label="Position: Tutor", text="T.", source="o"),
            )
        )
    )
    pack = LinkedInPack(
        sections=(
            section("headline", HEADLINE, report=None, changed=False),
            section("about", ABOUT),
            section("position:contoso", CONTOSO),
        ),
        profile_version=1,
    )
    owner.save_pack(pack)
    listed = client.get("/api/linkedin").json()["pack"]
    assert [s["key"] for s in listed["sections"]] == ["about", "position:contoso"]
    assert (listed["unchanged"], listed["removed"]) == (1, ["Position: Tutor"])
    assert mark(client, owner, listed["digest"]).json() == {"marked": 3, "flagged": []}
    after = client.get("/api/linkedin").json()["pack"]
    assert (after["sections"], after["unchanged"], after["removed"]) == ([], 3, [])


def test_the_page_says_which_version_the_pack_came_from(owner: Owner, client: TestClient) -> None:
    async def newer(session: AsyncSession) -> None:
        await save_version(session, owner.id, PROFILE, author="owner")

    owner.save_pack(first_pack())
    seed(owner.database_url, newer)
    page = client.get("/api/linkedin").json()
    assert (page["pack"]["profileVersion"], page["profileVersion"]) == (1, 2)


def test_only_the_owners_own_pack_is_shown_and_marked(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    other = Owner(database_url)
    other.save_pack(first_pack())
    assert client.get("/api/linkedin").json()["pack"] is None
    theirs = hashlib.sha256(first_pack().model_dump_json().encode("utf-8")).hexdigest()
    assert mark(client, owner, theirs).status_code == 409
    assert other.done() is None
