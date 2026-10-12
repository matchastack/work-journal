"""`/api/inbox` for the signed-in owner, against the test database (T-040)."""

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import ChangeSetRow, JournalEntry, LlmCallRow, User
from app.db.profiles import latest_version, propose_change_set, save_version
from app.db.settings import get_style_notes
from app.db.store import save_facts
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import CallLog
from app.main import create_app
from app.schema.changes import (
    AddBullet,
    AddSkill,
    ChangeOp,
    ChangeSet,
    EditBullet,
    ReviewedOp,
    SetBulletStatus,
)
from app.schema.fact import Fact
from app.schema.profile import Bullet, Profile, Skill
from app.schema.settings import StyleNotes
from app.schema.verification import VerifierFinding, VerifierReport
from app.validate.claims import ClaimReply, JudgedClaim

BROWSER = "https://testserver"
FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
IMPORTED = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
REWORDED = "Cut the nightly export from 50 to 12 minutes by batching database writes."
RUNBOOK = "Wrote the runbook for the nightly export."
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="Wrote the runbook",
            reason="The fact says so.",
            verdict="supported",
            fact_ids=("f_runbook",),
        ),
    )
)
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
    later tests, such as key rotation's, then see no fact text under this test's key."""

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


@pytest.fixture
def owner(database_url: str) -> Owner:
    return Owner(database_url)


class Claude:
    """Stands in for Claude: the replies it's given, and a client that uses them."""

    def __init__(self) -> None:
        self.fake = FakeMessages()
        self.settings = Settings(llm_model_standard="standard-model")

    def client(self, log: CallLog) -> LLMClient:
        return LLMClient(self.settings, self.fake, log)


@pytest.fixture
def claude() -> Claude:
    return Claude()


@pytest.fixture
def client(database_url: str, owner: Owner, claude: Claude) -> Iterator[TestClient]:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset({owner.login}),
    )
    api = create_app(settings, llm=claude.client)
    cookies = {SESSION_COOKIE: owner.session_token}
    with TestClient(api, base_url=BROWSER, cookies=cookies) as client:
        yield client


def report(text: str, *errors: str) -> dict[str, Any]:
    findings = tuple(
        VerifierFinding(check="claims", severity="error", code="added_tool", message=error)
        for error in errors
    )
    return VerifierReport(text=text, passed=not errors, findings=findings).model_dump(mode="json")


def propose(database_url: str, user_id: uuid.UUID, *ops: ReviewedOp) -> uuid.UUID:
    async def build(session: AsyncSession) -> uuid.UUID:
        change_set = ChangeSet(author="ai", ops=ops, summary="From your journal", base_version=1)
        return await propose_change_set(session, user_id, change_set)

    return seed(database_url, build)


@pytest.fixture
def change_set(database_url: str, owner: Owner) -> uuid.UUID:
    """A refresh's proposal: a reworded bullet that passed the verifier, a new bullet it
    flagged, benching a bullet and a new skill. The new bullet cites a journal fact."""

    async def facts(session: AsyncSession) -> str:
        entry = JournalEntry(
            user_id=owner.id,
            opened_at=datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
            last_message_at=datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
        )
        session.add(entry)
        await session.flush()
        runbook = Fact(id="f_runbook", statement=RUNBOOK, entry_id=str(entry.id))
        await save_facts(session, owner.id, [*IMPORTED, runbook])
        return str(entry.id)

    entry = seed(database_url, facts)
    change_set = propose(
        database_url,
        owner.id,
        ReviewedOp(
            op=EditBullet(bullet_id="nw_export", text=REWORDED, fact_ids=("fact_nw_export",)),
            verifier_report=report(REWORDED),
        ),
        ReviewedOp(
            op=AddBullet(
                parent_id="northwind",
                bullet=Bullet(id="nw_runbook", text="Wrote the runbook in Go."),
                fact_ids=("f_runbook", "gone_fact"),
            ),
            verifier_report=report("Wrote the runbook in Go.", "No fact names Go."),
        ),
        ReviewedOp(op=SetBulletStatus(bullet_id="nw_orders", status="benched", reason="Older")),
        ReviewedOp(op=AddSkill(skill=Skill(name="Go", category="Languages"))),
    )
    assert entry
    return change_set


def latest_profile(database_url: str, user_id: uuid.UUID) -> tuple[int, Profile]:
    async def query(session: AsyncSession) -> tuple[int, Profile]:
        version = await latest_version(session, user_id)
        assert version is not None
        return version.number, version.profile

    return seed(database_url, query)


def decide(
    client: TestClient, owner: Owner, change_set: uuid.UUID, position: int, **body: Any
) -> Any:
    return client.post(
        f"/api/inbox/{change_set}/ops/{position}/decision", json=body, headers=owner.csrf
    )


# --- Reading the Inbox ---------------------------------------------------------------------------


def test_the_inbox_needs_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/inbox").status_code == 401


def test_an_empty_inbox_says_which_version_changes_apply_to(client: TestClient) -> None:
    response = client.get("/api/inbox")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"changeSets": [], "profileVersion": 1}


def test_each_change_shows_its_diff_sources_outputs_and_report(
    client: TestClient, change_set: uuid.UUID
) -> None:
    """FR-REV-1."""
    (listed,) = client.get("/api/inbox").json()["changeSets"]
    assert listed["id"] == str(change_set)
    assert (listed["author"], listed["summary"], listed["baseVersion"]) == (
        "ai",
        "From your journal",
        1,
    )
    reworded, added, benched, skill = listed["ops"]
    assert reworded | {"verifierReport": None} == {
        "position": 0,
        "kind": "edit_bullet",
        "status": "proposed",
        "reason": None,
        "title": "Reword a bullet under Engineer, Northwind Traders",
        "context": None,
        "before": EXPORT,
        "after": REWORDED,
        "diff": [
            {"kind": "same", "text": "Cut the nightly export"},
            {"kind": "removed", "text": " job"},
            {"kind": "same", "text": " from 50 to 12 minutes by batching database writes."},
        ],
        "outputs": ["master_resume", "tailored_resumes", "portfolio", "linkedin"],
        "sources": [
            {
                "id": "fact_nw_export",
                "statement": IMPORTED[0].statement,
                "entryId": None,
            }
        ],
        "verifierReport": None,
        "editable": True,
    }
    assert reworded["verifierReport"]["passed"] is True
    assert added["diff"] == []
    assert added["after"] == "Wrote the runbook in Go."
    assert added["sources"][0]["statement"] == RUNBOOK
    assert added["sources"][0]["entryId"]
    assert added["sources"][1] == {"id": "gone_fact", "statement": None, "entryId": None}
    assert added["verifierReport"]["findings"][0]["message"] == "No fact names Go."
    assert (benched["verifierReport"], benched["editable"]) == (None, False)
    assert skill["outputs"] == ["master_resume", "tailored_resumes", "portfolio"]


# --- Deciding ------------------------------------------------------------------------------------


def test_each_change_can_be_accepted_rejected_with_a_reason_or_undone(
    owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    """FR-REV-2."""
    accepted = decide(client, owner, change_set, 0, status="accepted", reason="ignored")
    assert accepted.status_code == 200
    assert accepted.json()["ops"][0] | {"verifierReport": None} == (
        client.get("/api/inbox").json()["changeSets"][0]["ops"][0] | {"verifierReport": None}
    )
    assert (accepted.json()["ops"][0]["status"], accepted.json()["ops"][0]["reason"]) == (
        "accepted",
        None,
    )
    rejected = decide(client, owner, change_set, 2, status="rejected", reason="Still current")
    assert rejected.json()["ops"][2]["status"] == "rejected"
    assert rejected.json()["ops"][2]["reason"] == "Still current"
    undone = decide(client, owner, change_set, 2, status="proposed")
    assert (undone.json()["ops"][2]["status"], undone.json()["ops"][2]["reason"]) == (
        "proposed",
        None,
    )


def test_a_decision_needs_the_csrf_header_and_a_real_change(
    owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    path = f"/api/inbox/{change_set}/ops/0/decision"
    assert client.post(path, json={"status": "accepted"}).status_code == 403
    assert decide(client, owner, change_set, 4, status="accepted").status_code == 404
    assert decide(client, owner, change_set, -1, status="accepted").status_code == 404
    assert decide(client, owner, uuid.uuid4(), 0, status="accepted").status_code == 404
    assert decide(client, owner, change_set, 0, status="applied").status_code == 422
    long = decide(client, owner, change_set, 0, status="rejected", reason="x" * 501)
    assert long.status_code == 422


def test_only_the_owners_change_sets_can_be_reviewed(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    async def other(session: AsyncSession) -> uuid.UUID:
        user = User()
        session.add(user)
        await session.flush()
        return user.id

    theirs = propose(
        database_url,
        seed(database_url, other),
        ReviewedOp(op=AddSkill(skill=Skill(name="Go", category="Languages"))),
    )
    assert client.get("/api/inbox").json()["changeSets"] == []
    assert decide(client, owner, theirs, 0, status="accepted").status_code == 404
    applied = client.post(f"/api/inbox/{theirs}/apply", headers=owner.csrf)
    assert applied.status_code == 404


def test_bulk_accept_leaves_flagged_changes_for_the_owner(
    owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    """FR-FID-7: text the verifier flagged is never accepted without the owner deciding."""
    decide(client, owner, change_set, 3, status="rejected")
    response = client.post(f"/api/inbox/{change_set}/accept-passed", headers=owner.csrf)
    assert response.status_code == 200
    statuses = [op["status"] for op in response.json()["ops"]]
    assert statuses == ["accepted", "proposed", "accepted", "rejected"]


# --- Applying ------------------------------------------------------------------------------------


def test_applying_makes_one_new_version_and_keeps_rejection_reasons(
    database_url: str, owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    """FR-REV-3 and FR-REV-4."""
    decide(client, owner, change_set, 0, status="accepted")
    waiting = client.post(f"/api/inbox/{change_set}/apply", headers=owner.csrf)
    assert waiting.status_code == 409
    assert waiting.json()["detail"] == "decide on every change first: 3 still waiting"
    decide(client, owner, change_set, 1, status="rejected", reason="Never used Go there")
    decide(client, owner, change_set, 2, status="rejected", reason="  ")
    decide(client, owner, change_set, 3, status="accepted")
    applied = client.post(f"/api/inbox/{change_set}/apply", headers=owner.csrf)
    assert applied.status_code == 200
    assert applied.json() == {"version": 2}
    number, profile = latest_profile(database_url, owner.id)
    assert number == 2
    bullets = {bullet.id: bullet for bullet in profile.all_bullets()}
    assert bullets["nw_export"].text == REWORDED
    assert "nw_runbook" not in bullets
    assert bullets["nw_orders"].status == "active"
    assert [skill.name for skill in profile.skills][-1] == "Go"

    async def notes(session: AsyncSession) -> StyleNotes:
        return await get_style_notes(session, owner.id)

    assert [note.text for note in seed(database_url, notes).notes] == ["Never used Go there"]
    assert client.get("/api/inbox").json()["changeSets"] == []
    again = client.post(f"/api/inbox/{change_set}/apply", headers=owner.csrf)
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "this change set is already applied",
    )
    assert decide(client, owner, change_set, 0, status="proposed").status_code == 409


def test_a_change_set_with_nothing_accepted_is_closed_as_rejected(
    database_url: str, owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    for position in range(4):
        decide(client, owner, change_set, position, status="rejected")
    applied = client.post(f"/api/inbox/{change_set}/apply", headers=owner.csrf)
    assert applied.json() == {"version": None}
    assert latest_profile(database_url, owner.id)[0] == 1

    async def status(session: AsyncSession) -> str | None:
        return await session.scalar(
            select(ChangeSetRow.status).where(ChangeSetRow.id == change_set)
        )

    assert seed(database_url, status) == "rejected"


def test_changes_that_no_longer_fit_the_profile_apply_nothing(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    """FR-REV-3: the accepted changes apply all together or not at all."""
    ops: list[ChangeOp] = [
        AddSkill(skill=Skill(name="Go", category="Languages")),
        EditBullet(bullet_id="gone", text="Did something."),
    ]
    change_set = propose(
        database_url, owner.id, *(ReviewedOp(op=op, status="accepted") for op in ops)
    )
    applied = client.post(f"/api/inbox/{change_set}/apply", headers=owner.csrf)
    assert applied.status_code == 409
    assert applied.json()["detail"].startswith("the changes don't fit your profile any more:")
    assert latest_profile(database_url, owner.id)[0] == 1
    assert len(client.get("/api/inbox").json()["changeSets"]) == 1


# --- Editing -------------------------------------------------------------------------------------


def test_edited_text_is_verified_again_and_waits_for_a_decision(
    database_url: str, owner: Owner, client: TestClient, claude: Claude, change_set: uuid.UUID
) -> None:
    """FR-REV-2 and FR-FID-7."""
    claude.fake.replies.append(reply(SUPPORTED))
    decide(client, owner, change_set, 1, status="accepted")
    path = f"/api/inbox/{change_set}/ops/1/text"
    assert client.put(path, json={"text": RUNBOOK}).status_code == 403
    response = client.put(path, json={"text": f" {RUNBOOK} "}, headers=owner.csrf)
    assert response.status_code == 200
    edited = response.json()["ops"][1]
    assert (edited["status"], edited["after"]) == ("proposed", RUNBOOK)
    assert edited["verifierReport"]["passed"] is True
    assert edited["verifierReport"]["text"] == RUNBOOK
    calls = select(func.count()).select_from(LlmCallRow).where(LlmCallRow.user_id == owner.id)

    async def count(session: AsyncSession) -> int:
        return await session.scalar(calls) or 0

    assert seed(database_url, count) == 1, "the call is logged (NFR-COST-1)"


def test_only_bullet_text_can_be_edited(
    owner: Owner, client: TestClient, change_set: uuid.UUID
) -> None:
    path = f"/api/inbox/{change_set}/ops/2/text"
    refused = client.put(path, json={"text": RUNBOOK}, headers=owner.csrf)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "only the text of a new or reworded bullet can be edited"
    too_long = {"text": "x" * 501}
    assert client.put(path, json=too_long, headers=owner.csrf).status_code == 422


def test_editing_says_so_when_claude_isnt_set_up(
    owner: Owner, client: TestClient, claude: Claude, change_set: uuid.UUID
) -> None:
    claude.settings = Settings()
    path = f"/api/inbox/{change_set}/ops/0/text"
    response = client.put(path, json={"text": REWORDED}, headers=owner.csrf)
    assert response.status_code == 503
    op = client.get("/api/inbox").json()["changeSets"][0]["ops"][0]
    assert op["after"] == REWORDED
    assert op["verifierReport"]["text"] == REWORDED, "the stored report is unchanged"
