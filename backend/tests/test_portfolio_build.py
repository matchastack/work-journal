import json
import os
import re
from pathlib import Path

import pytest

from app.portfolio.build import (
    Download,
    build_portfolio,
    date_range,
    portfolio_page,
    project_categories,
    render_page,
    safe_url,
)
from app.schema.profile import Bullet, Profile, Project, SocialProfile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
GOLDEN = FIXTURES / "golden" / "portfolio.html"
SITE = "https://casey.example.com"


def html_for(profile: Profile = PROFILE, site_url: str | None = None) -> str:
    return render_page(portfolio_page(profile, year=2026, site_url=site_url))


def json_ld(html: str) -> dict[str, object]:
    match = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.S)
    assert match is not None
    return json.loads(match[1])


def with_basics(**changes: object) -> Profile:
    return PROFILE.model_copy(update={"basics": PROFILE.basics.model_copy(update=changes)})


def test_the_page_matches_its_snapshot() -> None:
    """After an intended change, regenerate with `UPDATE_GOLDEN=1 uv run pytest` and review it."""
    html = html_for(site_url=SITE)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(html, encoding="utf-8")
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_build_writes_the_page_its_assets_and_the_downloads(tmp_path: Path) -> None:
    resume = tmp_path / "backend.pdf"
    resume.write_bytes(b"%PDF-1.4")
    site = tmp_path / "site"
    index = build_portfolio(
        PROFILE, site, year=2026, downloads=[Download(label="Backend resume", source=resume)]
    )
    files = sorted(path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file())
    assert files == ["index.html", "portfolio.css", "portfolio.js", "resumes/backend.pdf"]
    assert '<a href="resumes/backend.pdf" download' in index.read_text(encoding="utf-8")


def test_only_public_active_content_shows() -> None:
    html = html_for()
    assert "Casey Morgan" in html
    assert "555 0100" not in html  # the phone is for resumes only
    assert "issue tracker" not in html  # a benched bullet
    assert "Bird Call Classifier" not in html  # a planned project


def test_link_previews_and_search_engine_data() -> None:
    html = html_for(site_url=SITE)
    assert '<meta property="og:title" content="Casey Morgan · Software Engineer">' in html
    assert f'<link rel="canonical" href="{SITE}">' in html
    person = json_ld(html)
    assert (person["@type"], person["name"], person["jobTitle"]) == (
        "Person",
        "Casey Morgan",
        "Software Engineer",
    )
    assert person["url"] == SITE
    assert person["worksFor"] == {"@type": "Organization", "name": "Northwind Traders"}
    assert person["sameAs"] == ["https://github.com/casey-example"]
    assert "telephone" not in person


def test_text_cant_break_out_of_the_page() -> None:
    summary = '</script><script>alert("hi")</script> & <b>bold</b>'
    html = html_for(with_basics(summary=summary))
    assert "<script>alert" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert json_ld(html)["description"] == summary


def test_only_web_and_email_links_are_kept() -> None:
    assert safe_url("https://example.com") and safe_url("mailto:casey@example.com")
    assert not safe_url("javascript:alert(1)") and not safe_url("data:text/html,hi")
    profiles = (SocialProfile(network="GitHub", url="javascript:alert(1)"),)
    projects = tuple(
        project.model_copy(update={"url": "javascript:alert(2)"}) for project in PROFILE.projects
    )
    html = html_for(
        with_basics(url="javascript:alert(3)", profiles=profiles).model_copy(
            update={"projects": projects}
        )
    )
    assert "javascript:" not in html
    assert "sameAs" not in json_ld(html)


def test_projects_are_filed_under_the_role_types_they_are_kept_for() -> None:
    kinds = ["backend", "data"]
    both = Project(id="a", name="A", keep_for=("data", "backend"))
    assert project_categories(both, kinds) == ("backend", "data")
    assert project_categories(Project(id="b", name="B", cut_for=("backend",)), kinds) == ()
    assert project_categories(Project(id="c", name="C"), kinds) == ()


def test_tabs_show_when_projects_span_role_types() -> None:
    assert portfolio_page(PROFILE, year=2026).tabs == ()  # the fixture's projects are all backend
    pipeline = Project(
        id="pipeline",
        name="Weather Pipeline",
        keep_for=("data",),
        highlights=(Bullet(id="wp_load", text="Loaded hourly weather data into a warehouse."),),
    )
    profile = PROFILE.model_copy(update={"projects": (*PROFILE.projects, pipeline)})
    page = portfolio_page(profile, year=2026)
    assert [(tab.id, tab.name) for tab in page.tabs] == [
        ("backend", "Backend / full stack"),
        ("data", "Data engineering"),
    ]
    assert page.categories == {"recipe_box": ("backend",), "pipeline": ("data",)}
    assert 'data-categories="data"' in render_page(page)


def test_sections_without_content_are_left_out() -> None:
    html = html_for(PROFILE.model_copy(update={"education": (), "projects": ()}))
    assert 'id="about"' not in html and 'href="#about"' not in html
    assert 'id="projects"' not in html and 'href="#projects"' not in html
    assert 'id="experience"' in html


@pytest.mark.parametrize(
    ("start", "end", "shown"),
    [
        ("2024-03", None, "Mar 2024 \N{EN DASH} Present"),
        ("2023-06", "2023-09", "Jun 2023 \N{EN DASH} Sep 2023"),
        (None, "2023-09", "Sep 2023"),
    ],
)
def test_date_ranges(start: str | None, end: str | None, shown: str) -> None:
    assert date_range(start, end) == shown
