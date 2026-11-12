"""The core loop in the browser (T-053): sign in, accept a proposed change in the Inbox and apply
it, publish the portfolio page, and see the change on the public page (FR-REV-3, FR-PRT-3).

The app runs in a thread of the test process, under uvicorn on a free local port, with the web
app's build and a scratch database (`conftest.py`). Two stand-ins are passed to `create_app`, as
other tests do; nothing in the app changes for them:
- GitHub's OAuth endpoints are `FakeGitHub` (`app/auth/fake.py`), and the app's redirect to
  github.com gets the answer GitHub gives once the owner approves (`new_context`). So signing in
  goes through the app's real `/auth/login` and `/auth/callback`, with their state cookie and PKCE
  check, and the app has no sign-in of its own for tests.
- Claude is stubbed. The refresh proposes its changes from scripted replies, and the app's own
  client has none, so a call the journey shouldn't make fails rather than reaching the API.
The browser reaches nothing but the app.
"""

import asyncio
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, Route, expect
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.fake import FakeGitHub
from app.auth.github import AUTHORIZE_URL
from app.config import Settings
from app.db.profiles import load_master_profile
from app.db.settings import save_portfolio_settings
from app.db.store import save_facts
from app.db.users import user_for_login
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import CallLog, MemoryCallLog
from app.main import create_app
from app.refresh import begin_proposals, record_proposals, start_refresh
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.schema.settings import PortfolioSettings
from app.synthesis import PlannedBullet, SynthesisPlan, synthesize
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
OWNER = "casey-example"
"""The fictional owner's GitHub username, on the app's allowlist."""
OWNER_GITHUB_ID = 4242
HANDLE = "casey-morgan"
STARTUP_TIMEOUT_S = 15

ALERT = Fact(
    id="e1_1",
    statement="Added an alert that pages on-call when the nightly export fails.",
    date="2026-09",
    role_id="northwind",
)
RUNBOOK = Fact(
    id="e1_2",
    statement="Wrote the runbook for the nightly export.",
    date="2026-09",
    role_id="northwind",
)
ALERT_BULLET = "Added an alert that pages on-call when the nightly export job fails."
RUNBOOK_BULLET = "Wrote the runbook for the nightly export job."


@dataclass(frozen=True)
class LiveApp:
    url: str
    """The app's address, `http://127.0.0.1:<port>`, which is also its APP_URL."""
    github: FakeGitHub


@pytest.fixture(scope="module")
def live_app(e2e_database: str, web_build: Path) -> Iterator[LiveApp]:
    """The app, serving the API and the built web app until the module's tests end."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    github = FakeGitHub()
    settings = Settings(
        app_env="test",
        database_url=SecretStr(e2e_database),
        app_url=url,
        github_client_id=github.client_id,
        github_client_secret=SecretStr(github.client_secret),
        allowed_github_logins=frozenset({OWNER}),
        web_dist_dir=web_build,
    )

    def no_claude(log: CallLog) -> LLMClient:
        return LLMClient(settings, FakeMessages(), log)

    api = create_app(settings, transport=github.transport, llm=no_claude)
    server = uvicorn.Server(uvicorn.Config(api, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("the app didn't start: see the log above")
        time.sleep(0.05)
    try:
        yield LiveApp(url=url, github=github)
    finally:
        server.should_exit = True
        thread.join(STARTUP_TIMEOUT_S)
        listener.close()


def new_context(browser: Browser, app: LiveApp) -> BrowserContext:
    """A browser profile that reaches only the app.

    Signing in, the app sends the browser to GitHub. That redirect is answered here instead, with
    the address GitHub sends the browser back to once the owner approves. Anything else outside
    the app is blocked: requests by the route below, and redirects, which Playwright doesn't
    route, by a proxy that answers nothing, which only the app's address bypasses.
    """
    context = browser.new_context(proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"})
    context.set_default_timeout(10_000)

    def through_github(route: Route) -> None:
        """`/auth/login`, with its redirect to GitHub replaced by GitHub's answer."""
        response = route.fetch(max_redirects=0)
        location = response.headers.get("location", "")
        if not location.startswith(f"{AUTHORIZE_URL}?"):
            route.fulfill(response=response)
            return
        callback = app.github.approve(location, OWNER_GITHUB_ID, OWNER)
        headers = response.headers | {"location": f"{app.url}{callback}"}
        route.fulfill(response=response, headers=headers)

    context.route(lambda url: not url.startswith(f"{app.url}/"), lambda route: route.abort())
    context.route(f"{app.url}/auth/login", through_github)
    return context


async def propose_from_journal(database_url: str) -> None:
    """The owner's state after `wj db load-profile` and a `/refresh`: the fictional profile as
    version 1, a portfolio handle, two journal facts, and the refresh's change set from them,
    proposed by the real refresh code with scripted replies for Claude."""
    fake = FakeMessages(
        reply(
            SynthesisPlan(
                bullets=(
                    PlannedBullet(under="northwind", fact_ids=(ALERT.id,)),
                    PlannedBullet(under="northwind", fact_ids=(RUNBOOK.id,)),
                )
            )
        ),
        reply(WrittenBullet(text=ALERT_BULLET)),
        reply(ClaimReply(claims=(supported("Added an alert"),))),
        reply(WrittenBullet(text=RUNBOOK_BULLET)),
        reply(ClaimReply(claims=(supported("Wrote the runbook"),))),
    )
    models = Settings(llm_model_heavy="heavy", llm_model_standard="standard")
    claude = LLMClient(models, fake, MemoryCallLog())
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user_id = await user_for_login(session, OWNER, create=True)
            await load_master_profile(session, user_id, PROFILE)
            await save_portfolio_settings(session, user_id, PortfolioSettings(handle=HANDLE))
            await save_facts(session, user_id, [ALERT, RUNBOOK])
            refresh_id = await start_refresh(session, user_id)
            assert isinstance(refresh_id, uuid.UUID)
            inputs = await begin_proposals(session, refresh_id, now=datetime.now(UTC))
            assert inputs is not None
            synthesis = synthesize(
                inputs.version.profile,
                inputs.facts,
                claude,
                sources=inputs.sources,
                base_version=inputs.version.number,
            )
            assert synthesis.change_set is not None
            assert len(synthesis.change_set.ops) == 2
            assert fake.replies == []
            await record_proposals(session, inputs, synthesis, now=datetime.now(UTC))
            await session.commit()
    finally:
        await engine.dispose()


def seed(database_url: str) -> None:
    """`propose_from_journal`, on an event loop of its own in another thread: Playwright's sync
    API keeps one running in this one."""
    with ThreadPoolExecutor(1) as thread:
        thread.submit(asyncio.run, propose_from_journal(database_url)).result()


def supported(claim: str) -> JudgedClaim:
    return JudgedClaim(claim=claim, reason="As the fact states.", verdict="supported")


def sign_in(page: Page, app: LiveApp) -> None:
    """Sign in from the web app's sign-in page, as the owner does."""
    page.goto(app.url)
    page.get_by_role("link", name="Sign in with GitHub").click()
    expect(page.get_by_text(f"@{OWNER}")).to_be_visible()


def test_an_accepted_change_reaches_the_published_page(
    browser: Browser, live_app: LiveApp, e2e_database: str
) -> None:
    seed(e2e_database)
    owner = new_context(browser, live_app).new_page()
    visitor = new_context(browser, live_app).new_page()
    public_page = f"{live_app.url}/p/{HANDLE}"

    sign_in(owner, live_app)

    # The Inbox opens first. Accept one change, reject the other, and apply.
    expect(owner.get_by_role("heading", name="Inbox", level=1)).to_be_visible()
    alert = owner.get_by_role("article").filter(has_text=ALERT_BULLET)
    runbook = owner.get_by_role("article").filter(has_text=RUNBOOK_BULLET)
    alert.get_by_role("button", name="Accept").click()
    expect(alert).to_contain_text("Accepted")
    runbook.get_by_role("button", name="Reject").click()
    expect(runbook.get_by_role("textbox")).to_be_focused()  # the reason, left empty
    runbook.get_by_role("button", name="Reject").click()  # the form's
    expect(runbook).to_contain_text("Rejected")
    owner.get_by_role("button", name="Apply 1 accepted change").click()
    expect(owner.get_by_text("Applied: your profile is now at version 2.")).to_be_visible()
    expect(owner.get_by_text("Nothing to review.")).to_be_visible()

    # Nobody sees the page until it's published.
    response = visitor.goto(public_page)
    assert response is not None
    assert response.status == 404

    # Publish it from the Portfolio page.
    owner.get_by_role("navigation", name="Main").get_by_role("link", name="Portfolio").click()
    expect(owner.get_by_text("Not published.")).to_be_visible()
    expect(owner.get_by_text("It will show version 2 of your profile.")).to_be_visible()
    owner.get_by_role("button", name="Publish", exact=True).click()
    expect(owner.get_by_text("Published since")).to_be_visible()
    expect(owner.get_by_text("It shows version 2 of your profile.")).to_be_visible()

    # A visitor sees the accepted change, and not the rejected one.
    response = visitor.goto(public_page)
    assert response is not None
    assert response.status == 200
    expect(visitor.get_by_role("heading", level=1)).to_have_text(PROFILE.basics.name)
    expect(visitor.get_by_text(ALERT_BULLET)).to_be_visible()
    expect(visitor.get_by_text(PROFILE.work[0].highlights[0].text)).to_be_visible()
    expect(visitor.get_by_text(RUNBOOK_BULLET)).to_have_count(0)
