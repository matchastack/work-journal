"""The portfolio page on the web, against the test database (T-046): the public page at
`/p/<handle>`, its resume downloads, and `/api/portfolio` for the signed-in owner."""

import asyncio
import random
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.sessions import CSRF_HEADER, SESSION_COOKIE, start_session
from app.config import Settings
from app.db.models import ApplicationRow, Refresh, User
from app.db.profiles import save_version
from app.db.settings import save_portfolio_settings
from app.db.store import store_artifact
from app.main import create_app
from app.portfolio.routes import ASSET_VERSIONS, PUBLIC_CACHE
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.schema.settings import PortfolioSettings
from app.tailoring.applications import ResumeChecks

BROWSER = "https://testserver"
APP_URL = "https://journal.example.com"
NOW = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
MASTER_PDF = b"%PDF-1.7 master resume"
TAILORED_PDF = b"%PDF-1.7 tailored resume"
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
    """The tests commit, so the users each one makes are deleted afterwards, with their rows."""

    async def existing(session: AsyncSession) -> set[uuid.UUID]:
        return set(await session.scalars(select(User.id)))

    users = seed(database_url, existing)
    yield

    async def delete_new(session: AsyncSession) -> None:
        await session.execute(delete(User).where(User.id.not_in(users)))

    seed(database_url, delete_new)


class Owner:
    """A signed-in user of the test's own, with a handle of their own unless told otherwise."""

    def __init__(self, database_url: str, *, handle: bool = True) -> None:
        self.database_url = database_url
        self.login = f"owner-{uuid.uuid4().hex[:8]}"
        self.handle = f"casey-{uuid.uuid4().hex[:8]}" if handle else None

        async def sign_in(session: AsyncSession) -> tuple[uuid.UUID, str, str]:
            user = User(github_id=_random.randrange(10**9, 10**12), github_login=self.login)
            session.add(user)
            await session.flush()
            if self.handle is not None:
                settings = PortfolioSettings(handle=self.handle)
                await save_portfolio_settings(session, user.id, settings)
            token, row = start_session(session, user.id)
            return user.id, token, row.csrf_token

        self.id, self.session_token, self.csrf_token = seed(database_url, sign_in)

    @property
    def csrf(self) -> dict[str, str]:
        return {CSRF_HEADER: self.csrf_token}

    @property
    def page(self) -> str:
        assert self.handle is not None
        return f"/p/{self.handle}"

    def version(self, profile: Profile = PROFILE) -> None:
        async def build(session: AsyncSession) -> None:
            await save_version(session, self.id, profile, author="owner")

        seed(self.database_url, build)

    def refresh(self, version: int, pdf: bytes | None = None) -> str | None:
        """A refresh whose outputs fell due at `version`, rendered when `pdf` is given."""

        async def build(session: AsyncSession) -> str | None:
            sha = None if pdf is None else await store_artifact(session, self.id, pdf, PDF_TYPE)
            session.add(
                Refresh(
                    user_id=self.id,
                    status="nothing_new",
                    finished_at=NOW,
                    outputs="pending" if sha is None else "updated",
                    output_version=version,
                    outputs_at=NOW,
                    master_pdf_sha256=sha,
                    master_pages=None if sha is None else 2,
                )
            )
            return sha

        return seed(self.database_url, build)

    def application(self, application_id: str, pdf: bytes, *, checked: bool = True) -> str:
        async def build(session: AsyncSession) -> str:
            sha = await store_artifact(session, self.id, pdf, PDF_TYPE)
            logged = Application(
                id=application_id,
                applied_on=date(2026, 10, 20),
                company="Acme Example",
                position="Backend Engineer",
                posting=JobPosting(title="Backend Engineer", company="Acme Example"),
                posting_text="Backend Engineer at Acme Example",
                title_variant="Engineer",
                pdf_sha256=sha,
            )
            session.add(
                ApplicationRow(
                    user_id=self.id,
                    id=application_id,
                    data=logged.model_dump(mode="json"),
                    applied_on=logged.applied_on,
                    pdf_sha256=sha,
                    verifier_report=ResumeChecks().model_dump(mode="json") if checked else None,
                )
            )
            return sha

        return seed(self.database_url, build)

    def settings(self, **changes: Any) -> None:
        async def build(session: AsyncSession) -> None:
            settings = PortfolioSettings(handle=self.handle, **changes)
            await save_portfolio_settings(session, self.id, settings)

        seed(self.database_url, build)


PDF_TYPE = "application/pdf"


def client_for(database_url: str, owner: Owner) -> TestClient:
    settings = Settings(
        database_url=SecretStr(database_url),
        app_url=APP_URL,
        allowed_github_logins=frozenset({owner.login}),
    )
    cookies = {SESSION_COOKIE: owner.session_token}
    return TestClient(create_app(settings), base_url=BROWSER, cookies=cookies)


@pytest.fixture
def owner(database_url: str) -> Owner:
    return Owner(database_url)


@pytest.fixture
def client(database_url: str, owner: Owner) -> Iterator[TestClient]:
    with client_for(database_url, owner) as client:
        yield client


def published(client: TestClient, owner: Owner) -> dict[str, Any]:
    response = client.post("/api/portfolio/publish", headers=owner.csrf)
    assert response.status_code == 200, response.text
    return response.json()


def with_label(label: str, profile: Profile = PROFILE) -> Profile:
    return profile.model_copy(update={"basics": profile.basics.model_copy(update={"label": label})})


def download_links(html: str) -> list[str]:
    return re.findall(r'<a href="([^"]+)" download', html)


# --- The public page -----------------------------------------------------------------------------


def test_an_unknown_handle_and_an_unpublished_page_look_the_same(
    owner: Owner, client: TestClient
) -> None:
    owner.version()
    client.cookies.clear()  # visitors aren't signed in
    unknown = client.get("/p/nobody-has-this-one")
    unpublished = client.get(owner.page)
    for response in (unknown, unpublished):
        assert response.status_code == 404
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Robots-Tag"] == "noindex"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert unknown.text == unpublished.text
    assert owner.handle is not None and owner.handle not in unpublished.text


def test_publishing_shows_the_page_at_once_and_unpublishing_takes_it_down(
    owner: Owner, client: TestClient
) -> None:
    owner.version()
    view = published(client, owner)
    assert (view["published"], view["version"], view["path"]) == (True, 1, owner.page)

    page = client.get(owner.page)
    assert page.status_code == 200
    assert page.headers["Content-Type"] == "text/html; charset=utf-8"
    assert page.headers["Cache-Control"] == PUBLIC_CACHE
    assert "s-maxage=60" in PUBLIC_CACHE and "max-age=0" in PUBLIC_CACHE
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert "X-Robots-Tag" not in page.headers  # indexing is on by default
    assert "Casey Morgan" in page.text
    assert f'<link rel="canonical" href="{APP_URL}{owner.page}">' in page.text
    css = f"/p/_assets/portfolio.css?v={ASSET_VERSIONS['portfolio.css']}"
    assert f'href="{css}"' in page.text

    again = client.get(owner.page, headers={"If-None-Match": page.headers["ETag"]})
    assert (again.status_code, again.content) == (304, b"")
    assert again.headers["Cache-Control"] == PUBLIC_CACHE

    response = client.post("/api/portfolio/unpublish", headers=owner.csrf)
    assert response.status_code == 200 and response.json()["published"] is False
    gone = client.get(owner.page, headers={"If-None-Match": page.headers["ETag"]})
    assert gone.status_code == 404  # not a 304 for the copy browsers kept


def test_only_the_published_version_shows_until_the_next_refresh(
    owner: Owner, client: TestClient
) -> None:
    owner.version(with_label("Published headline"))
    owner.refresh(1)
    published(client, owner)
    owner.version(with_label("Draft headline"))  # version 2, made after the refresh

    page = client.get(owner.page).text
    assert "Published headline" in page and "Draft headline" not in page
    view = client.get("/api/portfolio").json()
    assert (view["version"], view["newestVersion"]) == (1, 2)

    owner.refresh(2)  # a refresh's outputs fall due at version 2
    page = client.get(owner.page).text
    assert "Draft headline" in page and "Published headline" not in page


def test_hidden_fields_never_show(owner: Owner, client: TestClient) -> None:
    northwind, contoso = PROFILE.work
    work = (
        northwind.model_copy(
            update={
                "highlights": tuple(
                    bullet.model_copy(update={"visibility": "resume_only"})
                    if bullet.id == "nw_export"
                    else bullet
                    for bullet in northwind.highlights
                )
            }
        ),
        contoso.model_copy(
            update={
                "highlights": tuple(
                    bullet.model_copy(update={"visibility": "private"})
                    if bullet.id == "ct_datasets"
                    else bullet
                    for bullet in contoso.highlights
                )
            }
        ),
    )
    basics = PROFILE.basics.model_copy(update={"email_visibility": "private"})
    owner.version(PROFILE.model_copy(update={"work": work, "basics": basics}))
    published(client, owner)
    page = client.get(owner.page).text

    assert "Northwind Traders" in page and "Build and maintain Python services" in page
    hidden = {
        "+1 555 0100": "the phone, for resumes only",
        "casey@example.com": "a private email",
        "Cut the nightly export": "a bullet for resumes only",
        "Surveyed 40 open datasets": "a private bullet",
        "issue tracker": "a benched bullet",
        "Bird Call Classifier": "a planned project",
        "Classify bird species": "a planned bullet",
        "Did not own the deployment pipeline": "a sensitive role's honesty boundary",
        "Volume not yet known": "a verification note",
        "event-driven Python services": "a swap",
        "Labelling and flagging only": "a bullet's note",
        "Tool usage, not an achievement": "a status reason",
        "Show the minor after the major": "an education rule",
        "Personal projects only": "a skill's note",
        "How many order events": "an open question",
        "fact_nw_export": "a fact ID",
    }
    shown = [what for text, what in hidden.items() if text in page]
    assert shown == []


def test_open_to_work_and_indexing_follow_the_settings(owner: Owner, client: TestClient) -> None:
    owner.version()
    published(client, owner)
    page = client.get(owner.page)
    assert "Open to work" not in page.text and 'name="robots"' not in page.text

    owner.settings(open_to_work=True, indexing=False)
    page = client.get(owner.page)
    assert ">Open to work</span>" in page.text
    assert '<meta name="robots" content="noindex">' in page.text
    assert page.headers["X-Robots-Tag"] == "noindex"


def test_the_default_visibility_hides_the_website_and_profiles(
    owner: Owner, client: TestClient
) -> None:
    owner.version()
    published(client, owner)
    assert 'href="https://github.com/casey-example"' in client.get(owner.page).text
    owner.settings(default_visibility="resume_only")
    page = client.get(owner.page).text
    assert 'href="https://github.com/casey-example"' not in page
    assert 'href="https://example.com"' not in page


def test_profile_text_cant_break_out_of_the_page(owner: Owner, client: TestClient) -> None:
    summary = '<script>alert("hi")</script> & <img src=x onerror=alert(1)>'
    owner.version(
        PROFILE.model_copy(
            update={"basics": PROFILE.basics.model_copy(update={"summary": summary})}
        )
    )
    published(client, owner)
    page = client.get(owner.page).text
    assert "<script>alert" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_each_handle_shows_its_own_owners_page(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    other = Owner(database_url)
    robin = PROFILE.model_copy(
        update={"basics": PROFILE.basics.model_copy(update={"name": "Robin Sample"})}
    )
    owner.version()
    other.version(robin)
    published(client, owner)
    with client_for(database_url, other) as theirs:
        published(theirs, other)
    mine, their_page = client.get(owner.page).text, client.get(other.page).text
    assert "Casey Morgan" in mine and "Robin Sample" not in mine
    assert "Robin Sample" in their_page and "Casey Morgan" not in their_page


def test_addresses_are_tidied_and_nothing_else_under_p_is_found(
    owner: Owner, client: TestClient
) -> None:
    assert owner.handle is not None
    upper = client.get(f"/p/{owner.handle.upper()}", follow_redirects=False)
    assert (upper.status_code, upper.headers["Location"]) == (308, owner.page)
    slash = client.get(f"{owner.page}/", follow_redirects=False)
    assert (slash.status_code, slash.headers["Location"]) == (308, owner.page)
    for path in ("/p/", f"{owner.page}/more/paths", "/p/not_a_handle", "/p/_assets/app.py"):
        response = client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 404, path
        assert "Page not found" in response.text  # not the web app


def test_the_pages_css_and_javascript_are_served_and_cached(client: TestClient) -> None:
    css = client.get(f"/p/_assets/portfolio.css?v={ASSET_VERSIONS['portfolio.css']}")
    assert css.status_code == 200
    assert css.headers["Content-Type"].startswith("text/css")
    assert css.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    stale = client.get("/p/_assets/portfolio.js?v=old")
    assert stale.headers["Content-Type"].startswith("text/javascript")
    assert stale.headers["Cache-Control"] == "public, max-age=300"


# --- Resume downloads ----------------------------------------------------------------------------


def test_visitors_can_download_only_the_resumes_the_owner_chose(
    database_url: str, owner: Owner, client: TestClient
) -> None:
    owner.version()
    old_master = owner.refresh(1, b"%PDF-1.7 an older master")
    owner.version()
    master = owner.refresh(2, MASTER_PDF)
    tailored = owner.application("app_backend", TAILORED_PDF)
    unchosen = owner.application("app_other", b"%PDF-1.7 not chosen")
    unchecked = owner.application("app_unchecked", b"%PDF-1.7 unchecked", checked=False)
    other = Owner(database_url)
    theirs = other.application("app_theirs", b"%PDF-1.7 theirs")

    view = client.get("/api/portfolio").json()
    assert [option["applicationId"] for option in view["options"]] == [
        None,  # the master resume, then the tailored ones, newest first
        "app_other",
        "app_backend",
    ]
    chosen = [
        {"source": "master", "label": "Resume"},
        {"source": "application", "applicationId": "app_backend", "label": "One-page resume"},
    ]
    response = client.put("/api/portfolio/resumes", json={"resumes": chosen}, headers=owner.csrf)
    assert response.status_code == 200, response.text
    assert [resume["available"] for resume in response.json()["resumes"]] == [True, True]
    published(client, owner)

    links = download_links(client.get(owner.page).text)
    assert links == [f"{owner.page}/resumes/{master}.pdf", f"{owner.page}/resumes/{tailored}.pdf"]
    pdf = client.get(links[1])
    assert pdf.status_code == 200 and pdf.content == TAILORED_PDF
    assert pdf.headers["Content-Type"] == PDF_TYPE
    assert pdf.headers["Content-Disposition"] == (
        f'attachment; filename="{owner.handle}-one-page-resume.pdf"'
    )
    assert pdf.headers["Cache-Control"] == PUBLIC_CACHE
    assert pdf.headers["ETag"] == f'"{tailored}"'
    assert client.get(links[1], headers={"If-None-Match": f'"{tailored}"'}).status_code == 304

    for sha in (old_master, unchosen, unchecked, theirs, "0" * 64):
        assert client.get(f"{owner.page}/resumes/{sha}.pdf").status_code == 404, sha
    assert client.get(f"{other.page}/resumes/{tailored}.pdf").status_code == 404

    client.post("/api/portfolio/unpublish", headers=owner.csrf)
    assert client.get(links[0]).status_code == 404


def test_a_resume_that_cant_be_offered_is_refused(owner: Owner, client: TestClient) -> None:
    owner.application("app_unchecked", b"%PDF-1.7 unchecked", checked=False)
    for resume in (
        {"source": "master", "label": "Resume"},
        {"source": "application", "applicationId": "app_unchecked", "label": "Resume"},
        {"source": "application", "applicationId": "app_nobodys", "label": "Resume"},
    ):
        response = client.put(
            "/api/portfolio/resumes", json={"resumes": [resume]}, headers=owner.csrf
        )
        assert response.status_code == 409, resume
        assert "can't offer" in response.json()["detail"]
    master = {"source": "master", "label": "Resume"}
    twice = client.put(
        "/api/portfolio/resumes", json={"resumes": [master, master]}, headers=owner.csrf
    )
    assert twice.status_code == 422


# --- The owner's API -----------------------------------------------------------------------------


def test_the_portfolio_api_needs_a_signed_in_owner(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/portfolio/preview").status_code == 401
    assert client.post("/api/portfolio/publish").status_code == 401
    assert client.post("/api/portfolio/unpublish").status_code == 401
    assert client.put("/api/portfolio/resumes", json={"resumes": []}).status_code == 401


def test_changes_need_the_csrf_header(owner: Owner, client: TestClient) -> None:
    owner.version()
    assert client.post("/api/portfolio/publish").status_code == 403
    assert client.post("/api/portfolio/unpublish").status_code == 403
    assert client.put("/api/portfolio/resumes", json={"resumes": []}).status_code == 403
    assert client.get(owner.page).status_code == 404


def test_without_a_handle_the_page_cant_be_published(database_url: str) -> None:
    owner = Owner(database_url, handle=False)
    owner.version()
    with client_for(database_url, owner) as client:
        view = client.get("/api/portfolio")
        assert view.headers["Cache-Control"] == "no-store"
        assert view.json() == {
            "handle": None,
            "path": None,
            "published": False,
            "publishedAt": None,
            "version": 1,
            "newestVersion": 1,
            "settings": {
                "handle": None,
                "defaultVisibility": "public",
                "openToWork": False,
                "indexing": True,
            },
            "resumes": [],
            "options": [],
        }
        response = client.post("/api/portfolio/publish", headers=owner.csrf)
        assert response.status_code == 409
        assert response.json()["detail"] == "Choose the page's handle in Settings first"


def test_without_a_profile_there_is_nothing_to_publish_or_preview(
    owner: Owner, client: TestClient
) -> None:
    view = client.get("/api/portfolio").json()
    assert (view["version"], view["newestVersion"]) == (None, None)
    response = client.post("/api/portfolio/publish", headers=owner.csrf)
    assert response.status_code == 409
    assert response.json()["detail"] == "There's no profile to publish yet"
    assert client.get("/api/portfolio/preview").status_code == 404


def test_the_preview_shows_the_page_before_it_is_published(
    owner: Owner, client: TestClient
) -> None:
    owner.version()
    sha = owner.refresh(1, MASTER_PDF)
    owner.settings(open_to_work=True)
    client.put(
        "/api/portfolio/resumes",
        json={"resumes": [{"source": "master", "label": "Resume"}]},
        headers=owner.csrf,
    )
    preview = client.get("/api/portfolio/preview")
    assert preview.status_code == 200
    assert preview.headers["Cache-Control"] == "no-store"
    assert preview.headers["X-Robots-Tag"] == "noindex"
    assert "frame-ancestors 'self'" in preview.headers["Content-Security-Policy"]
    assert "Casey Morgan" in preview.text and "Open to work" in preview.text
    links = download_links(preview.text)
    assert links == [f"/api/portfolio/preview/resumes/{sha}.pdf"]
    pdf = client.get(links[0])
    assert pdf.status_code == 200 and pdf.content == MASTER_PDF
    assert pdf.headers["Cache-Control"] == "no-store"
    assert client.get(owner.page).status_code == 404  # still not published
    client.cookies.clear()
    assert client.get(links[0]).status_code == 401
