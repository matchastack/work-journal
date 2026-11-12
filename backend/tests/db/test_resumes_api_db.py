"""`/api/resumes` for the signed-in owner, against the test database (T-044)."""

import asyncio
import hashlib
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import SESSION_COOKIE, start_session
from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import Refresh, User
from app.db.profiles import save_version
from app.db.store import save_application, save_render, store_artifact
from app.main import create_app
from app.refresh import master_selection
from app.render.fit import Cut
from app.render.resume import RenderedResume
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.selection import Selection
from app.tailoring.applications import ResumeChecks
from app.validate.lint import Finding, LintReport, lint_profile

BROWSER = "https://testserver"
FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
PLACEHOLDER = PROFILE.model_copy(
    update={
        "work": tuple(
            role.model_copy(
                update={
                    "highlights": tuple(
                        bullet.model_copy(update={"text": "TODO: say what the export saved."})
                        if bullet.id == "nw_export"
                        else bullet
                        for bullet in role.highlights
                    )
                }
            )
            for role in PROFILE.work
        )
    }
)
"""Version 2: a bullet that still holds a placeholder (R2)."""
EARLIER = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
CLEAN = LintReport()
"""A tailored resume that breaks no rule."""
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

        async def sign_in(session: AsyncSession) -> tuple[uuid.UUID, str]:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login=self.login)
            session.add(user)
            await session.flush()
            await save_version(session, user.id, PROFILE, author="owner")
            token, _ = start_session(session, user.id)
            return user.id, token

        self.id, self.session_token = seed(database_url, sign_in)

    def seed[T](self, build: Callable[[AsyncSession], Awaitable[T]]) -> T:
        return seed(self.database_url, build)


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


def rendered(chosen: Selection, pdf: bytes, *cuts: Cut, pages: int = 1) -> RenderedResume:
    """`chosen` as LaTeX would render it into `pdf`, under a key of its own."""
    return RenderedResume(
        pdf=pdf,
        tex="\\documentclass{article}",
        pages=pages,
        text="Casey Morgan",
        cuts=cuts,
        warnings=("Some text uses a bitmap font.",),
        selection=chosen,
        key=hashlib.sha256(b"made from " + pdf).hexdigest(),
    )


def refresh(
    owner: Owner,
    outputs: str,
    version: int,
    at: datetime,
    *,
    pdf: bytes | None = None,
    pages: int = 2,
) -> uuid.UUID:
    """A refresh whose outputs show `version`, rendered into `pdf` when they're `updated`."""

    async def build(session: AsyncSession) -> uuid.UUID:
        sha256 = None
        if pdf is not None:
            master = rendered(master_selection(PROFILE), pdf, pages=pages)
            sha256 = await save_render(session, owner.id, master)
        row = Refresh(
            user_id=owner.id,
            requested_at=at - timedelta(hours=1),
            status="nothing_new",
            finished_at=at - timedelta(minutes=50),
            outputs=outputs,
            output_version=version,
            outputs_at=at,
            master_pdf_sha256=sha256,
            master_pages=pages if sha256 else None,
        )
        session.add(row)
        await session.flush()
        return row.id

    return owner.seed(build)


def application(
    owner: Owner,
    application_id: str,
    applied_on: date,
    pdf: bytes | None,
    *,
    rules: LintReport | None = CLEAN,
    cuts: tuple[Cut, ...] | None = (),
) -> str | None:
    """A tailored resume logged as `wj tailor --db` logs it, with its render unless `cuts` is
    None, as for one logged before renders were kept. Returns its PDF's hash."""

    async def build(session: AsyncSession) -> str | None:
        sha256 = None
        if pdf is not None and cuts is not None:
            chosen = master_selection(PROFILE).model_copy(update={"max_pages": 1})
            sha256 = await save_render(session, owner.id, rendered(chosen, pdf, *cuts))
        elif pdf is not None:
            sha256 = await store_artifact(session, owner.id, pdf, "application/pdf")
        logged = Application(
            id=application_id,
            applied_on=applied_on,
            company="Fabrikam Logistics",
            position="Backend Engineer",
            posting=JobPosting(title="Backend Engineer", company="Fabrikam Logistics"),
            posting_text="Fabrikam Logistics needs a backend engineer.",
            title_variant="Software Engineer",
            pdf_sha256=sha256,
        )
        report = None if rules is None else ResumeChecks(rules=rules).model_dump(mode="json")
        await save_application(session, owner.id, logged, verifier_report=report)
        return sha256

    return owner.seed(build)


def report(profile: Profile) -> dict[str, Any]:
    return lint_profile(profile).model_dump(mode="json")


# --- Listing -------------------------------------------------------------------------------------


def test_resumes_need_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/resumes").status_code == 401
    assert client.get(f"/api/resumes/pdf/{'a' * 64}").status_code == 401


def test_before_any_refresh_or_tailoring_there_are_no_resumes(client: TestClient) -> None:
    response = client.get("/api/resumes")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "profileVersion": 1,
        "master": None,
        "lastRendered": None,
        "tailored": [],
    }


def test_the_master_resume_from_the_latest_refresh(client: TestClient, owner: Owner) -> None:
    """FR-RES-5, FR-RES-6: with its page count, its cut report (the master document has no page
    limit, so nothing is cut), and the profile check of the version it shows."""
    pdf = b"%PDF-1.5 the master document"
    refresh(owner, "updated", 1, EARLIER, pdf=pdf, pages=3)
    body = client.get("/api/resumes").json()
    assert body["master"] == {
        "version": 1,
        "status": "updated",
        "changedAt": "2026-10-20T09:00:00Z",
        "pdf": {
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "pages": 3,
            "cuts": [],
            "warnings": ["Some text uses a bitmap font."],
        },
        "lint": report(PROFILE),
    }
    assert body["lastRendered"] is None


def test_a_master_resume_waiting_to_be_rendered_lists_the_last_one_rendered(
    client: TestClient, owner: Owner
) -> None:
    """C6: the hosted app can't run LaTeX, so a refresh's master waits for the owner's computer.
    Until then, the one rendered before stays to hand."""

    async def version_2(session: AsyncSession) -> None:
        await save_version(session, owner.id, PLACEHOLDER, author="owner")

    owner.seed(version_2)
    pdf = b"%PDF-1.5 version 1"
    refresh(owner, "updated", 1, EARLIER, pdf=pdf)
    refresh(owner, "pending", 2, LATER)
    body = client.get("/api/resumes").json()
    assert body["profileVersion"] == 2
    master = body["master"]
    assert (master["version"], master["status"], master["pdf"]) == (2, "pending", None)
    assert master["lint"] == report(PLACEHOLDER)
    assert master["lint"]["findings"][0]["rule"] == "R2", "the profile check of version 2"
    last = body["lastRendered"]
    assert (last["version"], last["status"]) == (1, "updated")
    assert last["pdf"]["sha256"] == hashlib.sha256(pdf).hexdigest()


def test_a_master_resume_that_failed_to_render_says_so(client: TestClient, owner: Owner) -> None:
    refresh(owner, "failed", 1, EARLIER)
    refresh(owner, "superseded", 1, EARLIER - timedelta(days=1))
    master = client.get("/api/resumes").json()["master"]
    assert (master["version"], master["status"], master["pdf"]) == (1, "failed", None)


def test_tailored_resumes_newest_first_with_their_cuts_and_rule_findings(
    client: TestClient, owner: Owner
) -> None:
    """FR-TLR-8, R2: one that breaks a rule is listed with its errors, and marked not to send."""
    placeholder = Finding(
        rule="R2", severity="error", location="work[northwind]", message="has TODO"
    )
    cut = Cut(kind="bullet", id="ct_datasets", text="Labelled the datasets.")
    clean = application(owner, "20261020-fabrikam-a1b2c3", date(2026, 10, 20), b"%PDF clean")
    broken = application(
        owner,
        "20261027-fabrikam-d4e5f6",
        date(2026, 10, 27),
        b"%PDF broken",
        rules=LintReport(findings=(placeholder,)),
        cuts=(cut,),
    )
    tailored = client.get("/api/resumes").json()["tailored"]
    assert [resume["id"] for resume in tailored] == [
        "20261027-fabrikam-d4e5f6",
        "20261020-fabrikam-a1b2c3",
    ]
    newest = tailored[0]
    assert newest | {"loggedAt": None} == {
        "id": "20261027-fabrikam-d4e5f6",
        "company": "Fabrikam Logistics",
        "position": "Backend Engineer",
        "appliedOn": "2026-10-27",
        "loggedAt": None,
        "pdf": {
            "sha256": broken,
            "pages": 1,
            "cuts": [{"kind": "bullet", "id": "ct_datasets", "text": "Labelled the datasets."}],
            "warnings": ["Some text uses a bitmap font."],
        },
        "rules": {"findings": [placeholder.model_dump(mode="json")], "notes": []},
        "sendable": False,
    }
    assert (tailored[1]["pdf"]["sha256"], tailored[1]["sendable"]) == (clean, True)


def test_what_wasnt_recorded_is_listed_as_unknown(client: TestClient, owner: Owner) -> None:
    """A PDF stored before renders were kept has no page count or cut report, and an application
    whose rules weren't kept isn't marked fit to send."""
    sha256 = application(owner, "20261001-old-aaaaaa", date(2026, 10, 1), b"%PDF old", cuts=None)
    application(owner, "20261002-unchecked-bbbbbb", date(2026, 10, 2), None, rules=None)
    unchecked, old = client.get("/api/resumes").json()["tailored"]
    assert old["pdf"] == {"sha256": sha256, "pages": None, "cuts": None, "warnings": []}
    assert (old["rules"], old["sendable"]) == ({"findings": [], "notes": []}, True)
    assert (unchecked["pdf"], unchecked["rules"], unchecked["sendable"]) == (None, None, False)


def test_another_users_resumes_arent_listed(
    client: TestClient, owner: Owner, database_url: str
) -> None:
    other = Owner(database_url)
    refresh(other, "updated", 1, EARLIER, pdf=b"%PDF theirs")
    application(other, "20261020-theirs-cccccc", date(2026, 10, 20), b"%PDF their tailored")
    body = client.get("/api/resumes").json()
    assert (body["master"], body["tailored"]) == (None, [])


# --- PDFs ----------------------------------------------------------------------------------------


def test_a_tailored_resume_previews_inline_and_downloads(client: TestClient, owner: Owner) -> None:
    pdf = b"%PDF-1.5 tailored"
    sha256 = application(owner, "20261027-fabrikam-d4e5f6", date(2026, 10, 27), pdf)
    preview = client.get(f"/api/resumes/pdf/{sha256}")
    assert preview.status_code == 200
    assert preview.content == pdf
    assert preview.headers["Content-Type"] == "application/pdf"
    assert preview.headers["Content-Disposition"] == (
        'inline; filename="20261027-fabrikam-d4e5f6.pdf"'
    )
    assert preview.headers["Cache-Control"] == "no-store"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    assert preview.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    download = client.get(f"/api/resumes/pdf/{sha256}", params={"download": "true"})
    assert download.headers["Content-Disposition"] == (
        'attachment; filename="20261027-fabrikam-d4e5f6.pdf"'
    )


def test_the_master_resume_downloads_named_by_its_version(client: TestClient, owner: Owner) -> None:
    pdf = b"%PDF-1.5 the master document"
    refresh(owner, "updated", 1, EARLIER, pdf=pdf)
    response = client.get(f"/api/resumes/pdf/{hashlib.sha256(pdf).hexdigest()}?download=true")
    assert response.content == pdf
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="master-resume-v1.pdf"'
    )


def test_only_the_owners_resume_pdfs_are_served(
    client: TestClient, owner: Owner, database_url: str
) -> None:
    other = Owner(database_url)
    theirs = application(other, "20261020-theirs-cccccc", date(2026, 10, 20), b"%PDF theirs")

    async def unlisted(session: AsyncSession) -> str:
        return await store_artifact(session, owner.id, b"%PDF not a resume", "application/pdf")

    loose = owner.seed(unlisted)
    assert client.get(f"/api/resumes/pdf/{theirs}").status_code == 404
    assert client.get(f"/api/resumes/pdf/{loose}").status_code == 404
    assert client.get(f"/api/resumes/pdf/{'0' * 64}").status_code == 404
    assert client.get("/api/resumes/pdf/not-a-hash").status_code == 422
    assert client.get(f"/api/resumes/pdf/{'A' * 64}").status_code == 422
