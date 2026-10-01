"""The built page in Chromium: accessibility (axe-core), a 360 px screen, and the scripts.

Uses Playwright's Chromium (`uv run playwright install chromium`), or the browser at
CHROMIUM_PATH. Without either the tests skip, except in CI, where they must run.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, sync_playwright

from app.portfolio.build import build_portfolio
from app.schema.profile import Bullet, Profile, Project

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        executable = os.environ.get("CHROMIUM_PATH") or playwright.chromium.executable_path
        if not Path(executable).exists():
            message = f"no Chromium at {executable}: run `uv run playwright install chromium`"
            if os.environ.get("CI"):
                pytest.fail(message)
            pytest.skip(message)
        chromium = playwright.chromium.launch(executable_path=executable)
        yield chromium
        chromium.close()


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    profile = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
    pipeline = Project(
        id="pipeline",
        name="Weather Pipeline",
        keep_for=("data",),
        highlights=(Bullet(id="wp_load", text="Loaded hourly weather data into a warehouse."),),
    )
    basics = profile.basics.model_copy(
        update={"summary": "I build backend services and data tools."}
    )
    profile = profile.model_copy(
        update={"basics": basics, "projects": (*profile.projects, pipeline)}
    )
    return build_portfolio(profile, tmp_path_factory.mktemp("site"), year=2026)


def open_page(browser: Browser, site: Path, width: int = 1280, javascript: bool = True) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, java_script_enabled=javascript
    )
    page = context.new_page()
    page.goto(site.as_uri())
    return page


@pytest.mark.parametrize("width", [1280, 360])
def test_no_serious_accessibility_issues(browser: Browser, site: Path, width: int) -> None:
    violations = Axe().run(open_page(browser, site, width)).response["violations"]
    serious = [(v["id"], v["impact"]) for v in violations if v["impact"] in ("serious", "critical")]
    assert serious == []


def test_the_layout_fits_a_360_px_screen(browser: Browser, site: Path) -> None:
    page = open_page(browser, site, width=360)
    assert page.evaluate("document.documentElement.scrollWidth") <= 360


def test_the_tabs_filter_projects(browser: Browser, site: Path) -> None:
    page = open_page(browser, site)
    data = page.get_by_role("button", name="Data engineering")
    data.click()
    assert data.get_attribute("aria-pressed") == "true"
    assert page.locator("[data-project]:visible h3").all_inner_texts() == ["Weather Pipeline"]
    page.get_by_role("button", name="All projects").click()
    assert page.locator("[data-project]:visible").count() == 2


def test_the_mobile_menu_opens_and_closes(browser: Browser, site: Path) -> None:
    page = open_page(browser, site, width=360)
    button = page.get_by_role("button", name="Menu")
    assert button.get_attribute("aria-expanded") == "false"
    button.click()
    assert button.get_attribute("aria-expanded") == "true"
    page.locator("#mobile-menu").get_by_role("link", name="Projects").click()
    assert button.get_attribute("aria-expanded") == "false"


def test_without_javascript_every_project_shows(browser: Browser, site: Path) -> None:
    page = open_page(browser, site, javascript=False)
    assert page.locator("[data-project]:visible").count() == 2
    assert not page.locator("#project-filters").is_visible()
