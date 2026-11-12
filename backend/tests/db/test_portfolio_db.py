"""Publishing the portfolio page, against the test database (T-046): the version it shows, the
resumes it may offer, and whose page a handle names (FR-PRT-1, FR-PRT-3, FR-PRT-7)."""

import hashlib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApplicationRow, Refresh, User
from app.db.profiles import save_version
from app.db.settings import (
    get_portfolio_publishing,
    portfolio_owner,
    save_portfolio_publishing,
    save_portfolio_settings,
)
from app.db.store import store_artifact
from app.portfolio.publishing import (
    NotPublishable,
    choose_resumes,
    find_published,
    offered_resumes,
    outputs_version,
    portfolio_view,
    preview_content,
    publish,
    published_content,
    shown_version,
    unpublish,
)
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.schema.settings import PortfolioPublishing, PortfolioSettings, PublishedResume
from app.tailoring.applications import ResumeChecks
from app.validate.lint import Finding, LintReport

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
MASTER = PublishedResume(source="master", label="Resume")


async def new_user(session: AsyncSession, handle: str | None = None) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    if handle is not None:
        await save_portfolio_settings(session, user.id, PortfolioSettings(handle=handle))
    return user.id


def handle() -> str:
    return f"casey-{uuid.uuid4().hex[:8]}"


def with_label(label: str) -> Profile:
    """The fixture profile with another headline, to tell versions apart."""
    return PROFILE.model_copy(update={"basics": PROFILE.basics.model_copy(update={"label": label})})


async def versions(session: AsyncSession, user_id: uuid.UUID, count: int) -> None:
    for number in range(1, count + 1):
        await save_version(session, user_id, with_label(f"Version {number}"), author="owner")


async def refresh(
    session: AsyncSession,
    user_id: uuid.UUID,
    version: int,
    *,
    pdf: bytes | None = None,
    at: datetime = NOW,
) -> None:
    """A refresh whose outputs fell due at `version`; rendered when `pdf` is given."""
    sha = None if pdf is None else await store_artifact(session, user_id, pdf, "application/pdf")
    session.add(
        Refresh(
            user_id=user_id,
            status="nothing_new",
            finished_at=at,
            outputs="pending" if sha is None else "updated",
            output_version=version,
            outputs_at=at,
            master_pdf_sha256=sha,
            master_pages=None if sha is None else 2,
        )
    )
    await session.flush()


async def application(
    session: AsyncSession,
    user_id: uuid.UUID,
    application_id: str,
    pdf: bytes,
    *,
    checks: ResumeChecks | None = None,
    applied_on: date = date(2026, 10, 20),
) -> str:
    sha = await store_artifact(session, user_id, pdf, "application/pdf")
    logged = Application(
        id=application_id,
        applied_on=applied_on,
        company="Acme Example",
        position="Backend Engineer",
        posting=JobPosting(title="Backend Engineer", company="Acme Example"),
        posting_text="Backend Engineer at Acme Example",
        title_variant="Engineer",
        pdf_sha256=sha,
    )
    session.add(
        ApplicationRow(
            user_id=user_id,
            id=application_id,
            data=logged.model_dump(mode="json"),
            applied_on=applied_on,
            pdf_sha256=sha,
            verifier_report=None if checks is None else checks.model_dump(mode="json"),
        )
    )
    await session.flush()
    return sha


PASSED = ResumeChecks()
BROKEN = ResumeChecks(
    rules=LintReport(
        findings=(Finding(rule="R2", severity="error", location="work", message="TODO left in"),)
    )
)


# --- The version -----------------------------------------------------------------------------


def test_a_published_page_shows_its_version_or_a_later_refreshs() -> None:
    published = PortfolioPublishing(published=True, version=3, published_at=NOW)
    assert shown_version(published, None) == 3
    assert shown_version(published, 2) == 3
    assert shown_version(published, 5) == 5
    assert shown_version(PortfolioPublishing(), 5) is None


async def test_the_outputs_show_the_latest_refreshs_version_or_the_newest_before_any(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session)
    assert await outputs_version(session, user_id) is None
    await versions(session, user_id, 2)
    assert await outputs_version(session, user_id) == 2
    await refresh(session, user_id, 2)
    await save_version(session, user_id, with_label("A draft"), author="owner")
    assert await outputs_version(session, user_id) == 2  # version 3 waits for a refresh


async def test_publishing_needs_a_handle_and_a_profile(session: AsyncSession) -> None:
    user_id = await new_user(session)
    await versions(session, user_id, 1)
    with pytest.raises(NotPublishable, match="handle"):
        await publish(session, user_id, now=NOW)
    other = await new_user(session, handle())
    with pytest.raises(NotPublishable, match="no profile"):
        await publish(session, other, now=NOW)
    assert not (await get_portfolio_publishing(session, other)).published


async def test_a_published_page_moves_with_each_refresh_and_never_shows_a_draft(
    session: AsyncSession,
) -> None:
    name = handle()
    user_id = await new_user(session, name)
    await versions(session, user_id, 2)
    await refresh(session, user_id, 2)
    await save_version(session, user_id, with_label("A draft"), author="owner")  # version 3
    await publish(session, user_id, now=NOW)

    published = await find_published(session, name)
    assert published is not None
    content = await published_content(session, published)
    assert content is not None
    assert (content.version, content.profile.basics.label) == (2, "Version 2")

    await refresh(session, user_id, 3)  # the next refresh's outputs fall due
    content = await published_content(session, published)
    assert content is not None
    assert (content.version, content.profile.basics.label) == (3, "A draft")


async def test_publishing_again_keeps_the_page_as_it_is(session: AsyncSession) -> None:
    user_id = await new_user(session, handle())
    await versions(session, user_id, 1)
    await publish(session, user_id, now=NOW)
    await versions(session, user_id, 1)  # version 2, a draft
    await publish(session, user_id, now=datetime(2026, 11, 1, tzinfo=UTC))
    publishing = await get_portfolio_publishing(session, user_id)
    assert (publishing.version, publishing.published_at) == (1, NOW)


async def test_an_unpublished_page_isnt_found_and_keeps_its_resumes(
    session: AsyncSession,
) -> None:
    name = handle()
    user_id = await new_user(session, name)
    await versions(session, user_id, 1)
    await refresh(session, user_id, 1, pdf=b"%PDF master")
    await choose_resumes(session, user_id, [MASTER])
    assert await find_published(session, name) is None  # not published yet
    await publish(session, user_id, now=NOW)
    assert await find_published(session, name) is not None
    await unpublish(session, user_id)
    assert await find_published(session, name) is None
    assert await get_portfolio_publishing(session, user_id) == PortfolioPublishing(
        resumes=(MASTER,)
    )


async def test_a_handle_names_one_users_page(session: AsyncSession) -> None:
    mine, theirs = handle(), handle()
    me = await new_user(session, mine)
    them = await new_user(session, theirs)
    assert await portfolio_owner(session, mine) == me
    assert await portfolio_owner(session, theirs) == them
    assert await portfolio_owner(session, "nobody-has-this") is None
    await versions(session, them, 1)
    await publish(session, them, now=NOW)
    assert await find_published(session, mine) is None  # mine isn't published
    found = await find_published(session, theirs)
    assert found is not None and found.user_id == them


async def test_the_preview_shows_what_publishing_would(session: AsyncSession) -> None:
    user_id = await new_user(session)
    assert await preview_content(session, user_id) is None  # no profile
    await versions(session, user_id, 2)
    await refresh(session, user_id, 1)
    content = await preview_content(session, user_id)
    assert content is not None and content.version == 1
    assert content.handle is None


# --- The resumes -----------------------------------------------------------------------------


async def test_the_owner_may_offer_the_newest_master_and_tailored_resumes_that_passed(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session, handle())
    await versions(session, user_id, 2)
    await refresh(session, user_id, 1, pdf=b"%PDF master 1", at=datetime(2026, 10, 1, tzinfo=UTC))
    await refresh(session, user_id, 2, pdf=b"%PDF master 2")
    await application(session, user_id, "app_ok", b"%PDF ok", checks=PASSED)
    await application(session, user_id, "app_broken", b"%PDF broken", checks=BROKEN)
    await application(session, user_id, "app_unchecked", b"%PDF unchecked")

    view = await portfolio_view(session, user_id)
    assert [(option.source, option.application_id, option.label) for option in view.options] == [
        ("master", None, "Resume"),
        ("application", "app_ok", "Backend Engineer resume"),
    ]
    assert view.options[0].name == "Master resume (version 2)"
    assert "Acme Example" in view.options[1].name  # the owner sees the company; visitors don't

    tailored = PublishedResume(source="application", application_id="app_ok", label="One page")
    await choose_resumes(session, user_id, [tailored, MASTER])
    offered = await offered_resumes(
        session, user_id, (await get_portfolio_publishing(session, user_id)).resumes
    )
    assert [(resume.label, resume.sha256) for resume in offered] == [
        ("One page", hashlib.sha256(b"%PDF ok").hexdigest()),
        ("Resume", hashlib.sha256(b"%PDF master 2").hexdigest()),
    ]


@pytest.mark.parametrize("application_id", ["app_broken", "app_unchecked", "app_missing"])
async def test_a_resume_that_mustnt_be_sent_cant_be_offered(
    session: AsyncSession, application_id: str
) -> None:
    user_id = await new_user(session)
    await application(session, user_id, "app_broken", b"%PDF broken", checks=BROKEN)
    await application(session, user_id, "app_unchecked", b"%PDF unchecked")
    resume = PublishedResume(source="application", application_id=application_id, label="CV")
    with pytest.raises(NotPublishable, match="can't offer that tailored resume"):
        await choose_resumes(session, user_id, [resume])
    with pytest.raises(NotPublishable, match="can't offer the master resume"):
        await choose_resumes(session, user_id, [MASTER])  # no refresh has rendered one


async def test_another_users_resume_cant_be_offered(session: AsyncSession) -> None:
    me = await new_user(session)
    them = await new_user(session)
    await application(session, them, "app_theirs", b"%PDF theirs", checks=PASSED)
    await refresh(session, them, 1, pdf=b"%PDF their master")
    resume = PublishedResume(source="application", application_id="app_theirs", label="CV")
    for chosen in (resume, MASTER):
        with pytest.raises(NotPublishable):
            await choose_resumes(session, me, [chosen])
        assert await offered_resumes(session, me, [chosen]) == ()


async def test_a_resume_chosen_before_stays_chosen_while_it_isnt_offered(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session)
    tailored = PublishedResume(source="application", application_id="app_gone", label="CV")
    await save_portfolio_publishing(session, user_id, PortfolioPublishing(resumes=(tailored,)))
    await choose_resumes(session, user_id, [tailored])  # kept, though there's no PDF
    view = await portfolio_view(session, user_id)
    assert [(resume.label, resume.available) for resume in view.resumes] == [("CV", False)]
    assert await offered_resumes(session, user_id, [tailored]) == ()


async def test_a_handle_two_users_somehow_share_shows_neither_page(session: AsyncSession) -> None:
    shared = handle()
    for _ in range(2):
        user_id = await new_user(session, shared)  # saved without the check `handle_taken` makes
        await versions(session, user_id, 1)
        await publish(session, user_id, now=NOW)
    assert await portfolio_owner(session, shared) is None
    assert await find_published(session, shared) is None
