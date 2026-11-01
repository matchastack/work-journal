"""`/api/tailor` for the signed-in owner, against the test database (T-045).

The jobs that parse, tailor and render a draft are tested in `test_tailor_jobs_db.py`; here a
draft is made ready as the tailoring job leaves it, and renders are kept as the render job keeps
them, so no model or LaTeX is needed.
"""

import asyncio
import hashlib
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.tailor.routes as routes_module
from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import ApplicationRow, ChangeSetRow, TailorDraftRow, User
from app.db.profiles import get_version, latest_version, save_version
from app.db.store import save_render
from app.main import create_app
from app.render.resume import RenderedResume, render_key
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.tailor.drafts import create_draft, record_parsed, record_tailoring
from app.tailoring.drafts import (
    DraftChoices,
    Tailoring,
    approve,
    decide_skill,
    draft_resume,
    first_choices,
)
from app.tailoring.posting import ParsedPosting
from app.tailoring.tailor import (
    Phrasing,
    PlannedBullet,
    PlannedItem,
    PlannedSkillLine,
    TailoringPlan,
)

BROWSER = "https://testserver"
FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
POSTING_TEXT = (FIXTURES / "postings" / "backend.txt").read_text(encoding="utf-8")
PDF = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
CONTEXT = "Senior Backend Engineer, Payments at Fabrikam Logistics"
PLAN = TailoringPlan(
    role_type="backend",
    work=(
        PlannedItem(
            id="northwind",
            bullets=(PlannedBullet(id="nw_export", relevance="high", rephrase=True),),
        ),
    ),
    skills=(PlannedSkillLine(label="Languages", skills=("Python", "Kotlin", "SQL")),),
)


def phrasing(text: str) -> Phrasing:
    report = VerifierReport(text=text, passed=True, fact_ids=("fact_nw_export",))
    return Phrasing(bullet_id="nw_export", original=EXPORT, report=report)


TAILORING = Tailoring(plan=PLAN, proposals=(phrasing(REPHRASED),))
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

    def seed[T](self, build: Callable[[AsyncSession], Awaitable[T]]) -> T:
        return seed(self.database_url, build)


@pytest.fixture
def owner(database_url: str) -> Owner:
    return Owner(database_url)


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Stands in for `draft_now`: the drafts whose next job a request ran after answering."""
    drafts: list[uuid.UUID] = []

    async def run(draft_id: uuid.UUID) -> None:
        drafts.append(draft_id)

    monkeypatch.setattr(routes_module, "draft_now", run)
    return drafts


def client_for(database_url: str, owner: Owner) -> TestClient:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url="https://journal.example.com",
        allowed_github_logins=frozenset({owner.login}),
    )
    cookies = {SESSION_COOKIE: owner.session_token}
    return TestClient(create_app(settings), base_url=BROWSER, cookies=cookies)


@pytest.fixture
def client(database_url: str, owner: Owner, ran: list[uuid.UUID]) -> Iterator[TestClient]:
    with client_for(database_url, owner) as client:
        yield client


def resume_of(choices: DraftChoices, tailoring: Tailoring = TAILORING) -> Any:
    return draft_resume(PROFILE, POSTING, tailoring, choices).selection


def keep_render(owner: Owner, choices: DraftChoices, tailoring: Tailoring = TAILORING) -> None:
    """Keep a render of the resume `choices` make, as the render job keeps one."""
    selection = resume_of(choices, tailoring)

    async def build(session: AsyncSession) -> None:
        rendered = RenderedResume(
            pdf=PDF,
            tex="% source",
            pages=1,
            text="Casey Morgan",
            cuts=(),
            warnings=(),
            selection=selection,
            key=render_key(selection),
        )
        await save_render(session, owner.id, rendered)

    owner.seed(build)


def ready_draft(owner: Owner, tailoring: Tailoring = TAILORING) -> uuid.UUID:
    """A draft as the tailoring job leaves it, tailored from version 1."""

    async def build(session: AsyncSession) -> uuid.UUID:
        draft_id = await create_draft(session, owner.id, POSTING_TEXT)
        await record_parsed(session, draft_id, ParsedPosting(posting=POSTING, dropped=("Rust",)))
        await record_tailoring(session, draft_id, 1, tailoring)
        return draft_id

    return owner.seed(build)


def row_of(owner: Owner, draft_id: uuid.UUID) -> TailorDraftRow:
    async def read(session: AsyncSession) -> TailorDraftRow:
        row = await session.get(TailorDraftRow, draft_id)
        assert row is not None
        return row

    return owner.seed(read)


def test_pasting_a_posting_makes_a_draft_and_runs_its_jobs(
    client: TestClient, owner: Owner, ran: list[uuid.UUID]
) -> None:
    response = client.post("/api/tailor", json={"text": POSTING_TEXT}, headers=owner.csrf)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    draft = response.json()
    assert (draft["status"], draft["posting"], draft["render"]) == ("parsing", None, None)
    assert draft["blocked"] == ["It isn't tailored yet."]
    assert ran == [uuid.UUID(draft["id"])], "its jobs run right after the answer"
    page = client.get("/api/tailor").json()
    assert [(d["id"], d["status"], d["title"]) for d in page["drafts"]] == [
        (draft["id"], "parsing", None)
    ]
    assert page["applications"] == []
    stored = row_of(owner, uuid.UUID(draft["id"])).posting_text
    assert stored == routes_module.NewDraft(text=POSTING_TEXT).text, "as pasted, in ASCII"


def test_a_posting_needs_text_and_the_csrf_header(client: TestClient, owner: Owner) -> None:
    assert client.post("/api/tailor", json={"text": "   "}, headers=owner.csrf).status_code == 422
    assert client.post("/api/tailor", json={"text": POSTING_TEXT}).status_code == 403
    too_long = "x" * (routes_module.POSTING_LIMIT + 1)
    assert (
        client.post("/api/tailor", json={"text": too_long}, headers=owner.csrf).status_code == 422
    )


def test_a_ready_draft_shows_the_resume_and_its_checks(client: TestClient, owner: Owner) -> None:
    draft_id = ready_draft(owner)
    draft = client.get(f"/api/tailor/{draft_id}").json()
    assert draft["status"] == "ready"
    assert draft["posting"]["title"] == POSTING.title
    assert draft["droppedTerms"] == ["Rust"]
    assert (draft["profileVersion"], draft["roleType"]) == (1, "backend")
    [bullet] = draft["bullets"]
    assert (bullet["id"], bullet["under"], bullet["original"], bullet["use"]) == (
        "nw_export",
        "Northwind Traders",
        EXPORT,
        "own",
    )
    assert (bullet["phrasing"]["text"], bullet["phrasing"]["passed"]) == (REPHRASED, True)
    assert (bullet["swap"], bullet["approved"]) == (None, False)
    assert draft["skills"] == [
        {"name": "Kotlin", "decision": None, "locations": ["skills[Languages]"]}
    ]
    assert [f["rule"] for f in draft["rules"]["findings"] if f["severity"] == "error"] == ["R3"]
    assert draft["coverageFitted"] is False
    assert "Python" in [entry["term"] for entry in draft["coverage"]["covered"]]
    assert draft["render"] == {"status": "waiting", "pdf": None, "ats": None, "error": None}
    assert draft["blocked"] == [
        "Confirm or drop each skill marked verify-before-shipping: Kotlin.",
        "The PDF isn't rendered yet.",
    ]
    assert draft["outline"]["work"][0]["bullets"] == [{"id": "nw_export", "text": EXPORT}]
    assert draft["outline"]["skills"] == [
        {"label": "Languages", "skills": ["Python", "Kotlin", "SQL"]}
    ]
    assert client.get(f"/api/tailor/{draft_id}/pdf").status_code == 404
    page = client.get("/api/tailor").json()
    assert [(d["title"], d["company"], d["renderStatus"]) for d in page["drafts"]] == [
        (POSTING.title, POSTING.company, "waiting")
    ]


def test_a_render_kept_before_is_used_without_latex(client: TestClient, owner: Owner) -> None:
    """T-044's render key: a resume rendered before is read back from its PDF at once."""
    keep_render(owner, first_choices(TAILORING))
    draft_id = ready_draft(owner)
    draft = client.get(f"/api/tailor/{draft_id}").json()
    render = draft["render"]
    assert render["status"] == "rendered"
    assert render["pdf"] == {
        "sha256": hashlib.sha256(PDF).hexdigest(),
        "pages": 1,
        "cuts": [],
        "warnings": [],
    }
    assert render["ats"] is not None
    assert draft["coverageFitted"] is True
    pdf = client.get(f"/api/tailor/{draft_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content == PDF
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert pdf.headers["cache-control"] == "no-store"


def test_approving_a_phrasing_saves_it_as_a_swap(
    client: TestClient, owner: Owner, ran: list[uuid.UUID]
) -> None:
    """FR-WRT-5: in the profile's next version, by a change set by the owner."""
    draft_id = ready_draft(owner)
    response = client.post(f"/api/tailor/{draft_id}/bullets/nw_export/approve", headers=owner.csrf)
    assert response.status_code == 200, response.text
    [bullet] = response.json()["bullets"]
    assert (bullet["use"], bullet["approved"]) == ("phrasing", True)
    assert response.json()["outline"]["work"][0]["bullets"][0]["text"] == REPHRASED
    assert ran == [draft_id], "the new resume is rendered"

    async def check(session: AsyncSession) -> None:
        latest = await latest_version(session, owner.id)
        assert latest is not None and latest.number == 2
        assert latest.author == "owner" and latest.change_set_id is not None
        [swap] = next(b for b in latest.profile.work[0].highlights if b.id == "nw_export").swaps
        assert (swap.text, swap.contexts, swap.origin) == (REPHRASED, (CONTEXT,), "approved")
        change_set = await session.get(ChangeSetRow, latest.change_set_id)
        assert change_set is not None
        assert (change_set.author, change_set.status, change_set.base_version) == (
            "owner",
            "applied",
            1,
        )
        assert (await get_version(session, owner.id, 1)).profile == PROFILE, "kept as it was"

    owner.seed(check)
    again = client.post(f"/api/tailor/{draft_id}/bullets/nw_export/approve", headers=owner.csrf)
    assert again.status_code == 200

    async def versions(session: AsyncSession) -> int:
        latest = await latest_version(session, owner.id)
        assert latest is not None
        return latest.number

    assert owner.seed(versions) == 2, "approving again saves nothing"
    draft = client.get(f"/api/tailor/{draft_id}").json()
    assert draft["profileVersion"] == 1, "the draft still shows the version it was tailored from"


def test_choosing_words_again_uses_the_render_of_those_words(
    client: TestClient, owner: Owner, ran: list[uuid.UUID]
) -> None:
    keep_render(owner, first_choices(TAILORING))
    draft_id = ready_draft(owner)
    url = f"/api/tailor/{draft_id}/bullets/nw_export"
    early = client.put(url, json={"use": "phrasing"}, headers=owner.csrf)
    assert early.status_code == 409
    assert early.json()["detail"] == "Approve the new phrasing of nw_export before you use it"
    client.post(f"{url}/approve", headers=owner.csrf)
    assert client.get(f"/api/tailor/{draft_id}").json()["render"]["status"] == "waiting"
    ran.clear()
    back = client.put(url, json={"use": "own"}, headers=owner.csrf)
    assert back.status_code == 200, back.text
    assert back.json()["render"]["status"] == "rendered", "rendered before: nothing to compile"
    assert ran == []
    assert client.put(url, json={"use": "swap"}, headers=owner.csrf).status_code == 409
    unknown = client.put(
        f"/api/tailor/{draft_id}/bullets/nw_tracker", json={"use": "own"}, headers=owner.csrf
    )
    assert unknown.status_code == 409
    assert client.put(url, json={"use": "own"}).status_code == 403


def test_each_skill_marked_verify_before_shipping_is_decided_before_download(
    client: TestClient, owner: Owner
) -> None:
    """R3, as `wj tailor --confirm` asks."""
    keep_render(owner, first_choices(TAILORING))
    draft_id = ready_draft(owner)
    today = {"appliedOn": datetime.now(UTC).date().isoformat()}
    waiting = client.post(f"/api/tailor/{draft_id}/application", json=today, headers=owner.csrf)
    assert waiting.status_code == 409
    assert waiting.json()["detail"] == (
        "Confirm or drop each skill marked verify-before-shipping: Kotlin"
    )
    confirmed = decide_skill(first_choices(TAILORING), PROFILE, "Kotlin", "confirmed")
    keep_render(owner, confirmed)
    skills = f"/api/tailor/{draft_id}/skills"
    response = client.put(
        skills, json={"skill": "kotlin", "decision": "confirmed"}, headers=owner.csrf
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["skills"] == [
        {"name": "Kotlin", "decision": "confirmed", "locations": ["skills[Languages]"]}
    ]
    assert draft["rules"]["findings"] == []
    assert draft["blocked"] == []
    python = client.put(skills, json={"skill": "Python", "decision": "dropped"}, headers=owner.csrf)
    assert python.status_code == 409
    undone = client.put(skills, json={"skill": "Kotlin", "decision": None}, headers=owner.csrf)
    assert undone.json()["skills"][0]["decision"] is None


def test_downloading_logs_the_application_once(client: TestClient, owner: Owner) -> None:
    """FR-TLR-8: the history shows the posting, the date and the PDF."""
    choices = decide_skill(first_choices(TAILORING), PROFILE, "Kotlin", "dropped")
    keep_render(owner, choices)
    draft_id = ready_draft(owner)
    client.put(
        f"/api/tailor/{draft_id}/skills",
        json={"skill": "Kotlin", "decision": "dropped"},
        headers=owner.csrf,
    )
    today = datetime.now(UTC).date()
    url = f"/api/tailor/{draft_id}/application"
    response = client.post(url, json={"appliedOn": today.isoformat()}, headers=owner.csrf)
    assert response.status_code == 200, response.text
    application = response.json()["application"]
    assert application["appliedOn"] == today.isoformat()
    assert application["pdfSha256"] == hashlib.sha256(PDF).hexdigest()
    assert application["sendable"] is True
    assert response.json()["blocked"] == []
    assert application["id"].startswith(f"{today:%Y%m%d}-fabrikam-logistics-senior-backend")
    again = client.post(url, json={}, headers=owner.csrf)
    assert again.json()["application"] == application, "logged once"
    page = client.get("/api/tailor").json()
    assert page["drafts"] == [], "a downloaded draft leaves the list"
    [logged] = page["applications"]
    assert (logged["id"], logged["company"], logged["position"], logged["appliedOn"]) == (
        application["id"],
        "Fabrikam Logistics",
        POSTING.title,
        today.isoformat(),
    )
    assert logged["pdf"]["sha256"] == application["pdfSha256"]
    download = client.get(f"/api/resumes/pdf/{application['pdfSha256']}?download=true")
    assert download.status_code == 200
    assert download.content == PDF

    async def stored(session: AsyncSession) -> ApplicationRow:
        rows = list(
            await session.scalars(select(ApplicationRow).where(ApplicationRow.user_id == owner.id))
        )
        assert len(rows) == 1
        return rows[0]

    row = owner.seed(stored)
    assert row.posting_id is not None
    assert row.data["postingText"] == routes_module.NewDraft(text=POSTING_TEXT).text
    assert row.data["bulletIds"][0] == "nw_export"
    assert row.verifier_report is not None
    assert set(row.verifier_report) == {"texts", "rules", "coverage", "ats"}
    changed = client.put(
        f"/api/tailor/{draft_id}/bullets/nw_export", json={"use": "own"}, headers=owner.csrf
    )
    assert changed.status_code == 409, "a logged draft doesn't change"
    assert client.delete(f"/api/tailor/{draft_id}", headers=owner.csrf).status_code == 409


def test_a_resume_that_breaks_a_rule_is_logged_but_marked_not_to_send(
    client: TestClient, owner: Owner
) -> None:
    """R2: text that names a dropped skill marked verify-before-shipping breaks R3."""
    kotlin = "Cut the nightly export job from 50 to 12 minutes with Kotlin."
    tailoring = Tailoring(plan=PLAN, proposals=(phrasing(kotlin),))
    choices = decide_skill(
        approve(first_choices(tailoring), "nw_export"), PROFILE, "Kotlin", "dropped"
    )
    keep_render(owner, choices, tailoring)
    draft_id = ready_draft(owner, tailoring)
    client.post(f"/api/tailor/{draft_id}/bullets/nw_export/approve", headers=owner.csrf)
    draft = client.put(
        f"/api/tailor/{draft_id}/skills",
        json={"skill": "Kotlin", "decision": "dropped"},
        headers=owner.csrf,
    ).json()
    assert draft["skills"][0]["locations"] == ["work[northwind].highlights[nw_export]"]
    assert [f["rule"] for f in draft["rules"]["findings"] if f["severity"] == "error"] == ["R3"]
    assert draft["blocked"] == [], "it can be downloaded"
    response = client.post(f"/api/tailor/{draft_id}/application", json={}, headers=owner.csrf)
    assert response.status_code == 200, response.text
    assert response.json()["application"]["sendable"] is False
    [logged] = client.get("/api/tailor").json()["applications"]
    assert logged["sendable"] is False
    assert [f["rule"] for f in logged["rules"]["findings"]] == ["R3"]


def test_a_download_is_logged_on_today(client: TestClient, owner: Owner) -> None:
    choices = decide_skill(first_choices(TAILORING), PROFILE, "Kotlin", "confirmed")
    keep_render(owner, choices)
    draft_id = ready_draft(owner)
    client.put(
        f"/api/tailor/{draft_id}/skills",
        json={"skill": "Kotlin", "decision": "confirmed"},
        headers=owner.csrf,
    )
    url = f"/api/tailor/{draft_id}/application"
    long_ago = {"appliedOn": (datetime.now(UTC).date() - timedelta(days=3)).isoformat()}
    assert client.post(url, json=long_ago, headers=owner.csrf).status_code == 422
    assert client.post(url, json={}).status_code == 403
    response = client.post(url, json={}, headers=owner.csrf)
    assert response.json()["application"]["appliedOn"] == datetime.now(UTC).date().isoformat()


def test_a_draft_isnt_logged_until_its_rendered(client: TestClient, owner: Owner) -> None:
    draft_id = ready_draft(owner)
    client.put(
        f"/api/tailor/{draft_id}/skills",
        json={"skill": "Kotlin", "decision": "confirmed"},
        headers=owner.csrf,
    )
    response = client.post(f"/api/tailor/{draft_id}/application", json={}, headers=owner.csrf)
    assert response.status_code == 409
    assert response.json()["detail"] == "The PDF isn't rendered yet"


def test_another_owners_draft_isnt_found(
    client: TestClient, owner: Owner, database_url: str
) -> None:
    keep_render(owner, first_choices(TAILORING))
    draft_id = ready_draft(owner)
    other = Owner(database_url)
    with client_for(database_url, other) as theirs:
        assert theirs.get(f"/api/tailor/{draft_id}").status_code == 404
        assert theirs.get(f"/api/tailor/{draft_id}/pdf").status_code == 404
        put = theirs.put(
            f"/api/tailor/{draft_id}/bullets/nw_export", json={"use": "own"}, headers=other.csrf
        )
        assert put.status_code == 404
        approved = theirs.post(
            f"/api/tailor/{draft_id}/bullets/nw_export/approve", headers=other.csrf
        )
        assert approved.status_code == 404
        assert theirs.delete(f"/api/tailor/{draft_id}", headers=other.csrf).status_code == 404
        assert theirs.get("/api/tailor").json()["drafts"] == []
    assert client.get(f"/api/tailor/{uuid.uuid4()}").status_code == 404


def test_a_draft_can_be_discarded_or_tried_again(
    client: TestClient, owner: Owner, ran: list[uuid.UUID]
) -> None:
    draft_id = ready_draft(owner)
    assert client.post(f"/api/tailor/{draft_id}/retry", headers=owner.csrf).status_code == 409
    assert client.delete(f"/api/tailor/{draft_id}").status_code == 403
    response = client.delete(f"/api/tailor/{draft_id}", headers=owner.csrf)
    assert response.status_code == 204
    assert client.get(f"/api/tailor/{draft_id}").status_code == 404

    async def failed(session: AsyncSession) -> uuid.UUID:
        failed_id = await create_draft(session, owner.id, POSTING_TEXT)
        await session.execute(
            update(TailorDraftRow)
            .where(TailorDraftRow.id == failed_id)
            .values(status="failed", error="The posting couldn't be read because x.")
        )
        return failed_id

    failed_id = owner.seed(failed)
    shown = client.get(f"/api/tailor/{failed_id}").json()
    assert (shown["status"], shown["error"]) == (
        "failed",
        "The posting couldn't be read because x.",
    )
    retried = client.post(f"/api/tailor/{failed_id}/retry", headers=owner.csrf)
    assert retried.status_code == 200
    assert (retried.json()["status"], retried.json()["error"]) == ("parsing", None)
    assert ran == [failed_id]


def test_a_render_that_failed_can_be_tried_again(
    client: TestClient, owner: Owner, ran: list[uuid.UUID]
) -> None:
    """Such as one that ran out of time."""
    draft_id = ready_draft(owner)

    async def fail(session: AsyncSession) -> None:
        await session.execute(
            update(TailorDraftRow)
            .where(TailorDraftRow.id == draft_id)
            .values(render_status="failed", render_error="LaTeX took longer than 30 s.")
        )

    owner.seed(fail)
    failed = client.get(f"/api/tailor/{draft_id}").json()
    assert failed["render"]["error"] == "LaTeX took longer than 30 s."
    assert failed["blocked"][-1] == "The PDF couldn't be rendered."
    retried = client.post(f"/api/tailor/{draft_id}/retry", headers=owner.csrf)
    assert retried.status_code == 200, retried.text
    assert retried.json()["render"] == {
        "status": "waiting",
        "pdf": None,
        "ats": None,
        "error": None,
    }
    assert ran == [draft_id]


def test_a_draft_whose_profile_version_is_gone_says_so(client: TestClient, owner: Owner) -> None:
    draft_id = ready_draft(owner)

    async def forget(session: AsyncSession) -> None:
        await session.execute(
            update(TailorDraftRow).where(TailorDraftRow.id == draft_id).values(profile_version=9)
        )

    owner.seed(forget)
    draft = client.get(f"/api/tailor/{draft_id}").json()
    assert (
        draft["error"] == "Profile version 9, which it was tailored from, is gone; tailor it again."
    )
    assert draft["bullets"] == []


def test_the_drafts_listed_are_the_newest(client: TestClient, owner: Owner) -> None:
    async def many(session: AsyncSession) -> int:
        for _ in range(3):
            await create_draft(session, owner.id, POSTING_TEXT)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(TailorDraftRow)
                .where(TailorDraftRow.user_id == owner.id)
            )
            or 0
        )

    assert owner.seed(many) == 3
    drafts = client.get("/api/tailor").json()["drafts"]
    assert len(drafts) == 3
    assert drafts == sorted(drafts, key=lambda d: d["createdAt"], reverse=True)
