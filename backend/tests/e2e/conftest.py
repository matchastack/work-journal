"""What the end-to-end tests need: Chromium, the web app's build and a scratch database.

Without one of them the tests skip, saying which, except in CI, where they must run. Chromium is
Playwright's (`uv run playwright install chromium`) or the browser at CHROMIUM_PATH. The build is
`frontend/dist` (`npm run build` in `frontend/`). The database is a new one on the server at
TEST_DATABASE_URL, as for the database tests, but kept apart from theirs, since the app commits
what the browser does; it's dropped after the module's tests.
"""

import asyncio
import os
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from playwright.sync_api import Browser, sync_playwright
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.engine import async_url

BACKEND = Path(__file__).resolve().parents[2]
WEB_BUILD = BACKEND.parent / "frontend" / "dist"


def unavailable(message: str) -> NoReturn:
    """Skip, or fail in CI, where every end-to-end test must run."""
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """Chromium, for one module's tests. Playwright keeps an event loop running in this thread
    until it stops, and tests in other modules may run their own."""
    with sync_playwright() as playwright:
        executable = os.environ.get("CHROMIUM_PATH") or playwright.chromium.executable_path
        if not Path(executable).exists():
            unavailable(f"no Chromium at {executable}: run `uv run playwright install chromium`")
        chromium = playwright.chromium.launch(executable_path=executable)
        yield chromium
        chromium.close()


@pytest.fixture(scope="session")
def web_build() -> Path:
    if not (WEB_BUILD / "index.html").is_file():
        unavailable("the web app isn't built: run `npm ci && npm run build` in frontend/")
    return WEB_BUILD


@pytest.fixture(scope="module")
def e2e_database() -> Iterator[str]:
    """A new database, migrated to the latest revision, and dropped after the module's tests."""
    server = os.environ.get("TEST_DATABASE_URL")
    if not server:
        unavailable("set TEST_DATABASE_URL to run the end-to-end tests (see compose.yml)")
    server_url = make_url(async_url(server))
    name = f"wj_e2e_{uuid.uuid4().hex[:12]}"
    admin = server_url.render_as_string(hide_password=False)
    apart(asyncio.run, _execute(admin, f'CREATE DATABASE "{name}"'))
    url = server_url.set(database=name).render_as_string(hide_password=False)
    try:
        config = Config(BACKEND / "alembic.ini")
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        config.attributes["configure_logging"] = False
        apart(command.upgrade, config, "head")
        yield url
    finally:
        apart(asyncio.run, _execute(admin, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module", autouse=True)
def key_ring() -> Iterator[None]:
    """A key for journal and fact text, which the app in its thread uses too."""
    use_key_ring(KeyRing.parse(new_key("e2e")))
    yield
    use_key_ring(None)


def apart[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Call `function` in another thread, for code that runs an event loop of its own, such as
    `asyncio.run` and the migrations: Playwright's sync API keeps one running in this thread."""
    with ThreadPoolExecutor(1) as thread:
        return thread.submit(function, *args, **kwargs).result()


async def _execute(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(statement))
    await engine.dispose()
