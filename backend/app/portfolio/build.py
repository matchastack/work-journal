"""The portfolio page's HTML (FR-PRT-2, FR-PRT-4, FR-PRT-5, FR-PRT-6, FR-PRT-8), and its build as
static files.

The page shows the profile's web selection: active items only (R8), and only fields visible on
the web, so the phone number stays hidden by default (FR-PRT-4). The website and the online
profiles, which have no visibility of their own, follow the owner's default visibility. Projects
can be filtered by role type: each role type that some project is kept for gets a tab. Projects
without keep-for tags show under "All projects" only.

The app serves the same page at `/p/<handle>` (`app/portfolio/routes.py`), with the owner's
settings: open to work, and indexing. `wj portfolio build` writes it as files.

`backend/templates/portfolio/` holds the Jinja template, the JavaScript for the menu and the tabs,
and the CSS, which Tailwind's standalone CLI builds (`scripts/build-portfolio-css.sh`).
"""

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

from app.schema.common import Visibility, YearMonth
from app.schema.profile import Profile, Project
from app.schema.variant import Variant
from app.selection import Selection, select

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "portfolio"
ASSETS = ("portfolio.css", "portfolio.js")
WEB_VARIANT = Variant(
    id="portfolio",
    name="Portfolio page",
    audience="web",
    section_order=("education", "work", "projects", "skills"),
    max_pages=None,
)
NO_DEFAULT_VISIBILITY = ("url", "profiles")
"""The contact details without a visibility of their own, which follow the default visibility."""
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SCRIPT_ESCAPES = {char: "\\" + f"u{ord(char):04x}" for char in "<>&"}
"""JSON-LD sits in a script element, so characters that could end it are written as escapes."""


@dataclass(frozen=True)
class Link:
    text: str
    href: str


@dataclass(frozen=True)
class Download:
    """A resume to offer on the page: its button label and the PDF to copy."""

    label: str
    source: Path


@dataclass(frozen=True)
class Assets:
    """Where the page loads its CSS and JavaScript from."""

    stylesheet: str
    script: str


STATIC_ASSETS = Assets(stylesheet="portfolio.css", script="portfolio.js")
"""Beside the page, as `build_portfolio` writes them."""


@dataclass(frozen=True)
class Tab:
    id: str
    name: str


@dataclass(frozen=True)
class Page:
    """Everything the template shows."""

    selection: Selection
    sections: tuple[Tab, ...]
    """The sections that have content, for the menu, in page order."""
    initials: str
    label: str | None
    about: str | None
    links: tuple[Link, ...]
    tabs: tuple[Tab, ...]
    categories: dict[str, tuple[str, ...]]
    """Each project's role types, by project ID."""
    downloads: tuple[Link, ...]
    site_url: str | None
    year: int
    description: str
    json_ld: Markup
    open_to_work: bool
    """Whether the page says the owner is open to work (FR-PRT-5)."""
    indexing: bool
    """Whether search engines may index the page. Off, it asks them not to (FR-PRT-5)."""
    assets: Assets


def build_portfolio(
    profile: Profile,
    out: Path,
    *,
    year: int,
    site_url: str | None = None,
    downloads: Sequence[Download] = (),
    open_to_work: bool = False,
    indexing: bool = True,
) -> Path:
    """Write `index.html`, its CSS and JavaScript, and the resume downloads to `out`."""
    out.mkdir(parents=True, exist_ok=True)
    links: list[Link] = []
    for download in downloads:
        target = out / "resumes" / download.source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(download.source, target)
        links.append(Link(text=download.label, href=f"resumes/{download.source.name}"))
    page = portfolio_page(
        profile,
        year=year,
        site_url=site_url,
        downloads=links,
        open_to_work=open_to_work,
        indexing=indexing,
    )
    for asset in ASSETS:
        shutil.copyfile(TEMPLATE_DIR / "static" / asset, out / asset)
    index = out / "index.html"
    index.write_text(render_page(page), encoding="utf-8")
    return index


def portfolio_page(
    profile: Profile,
    *,
    year: int,
    site_url: str | None = None,
    downloads: Sequence[Link] = (),
    default_visibility: Visibility = "public",
    open_to_work: bool = False,
    indexing: bool = True,
    assets: Assets = STATIC_ASSETS,
) -> Page:
    selection = select(profile, web_variant(default_visibility))
    role_types = [role_type.id for role_type in profile.role_types]
    projects = {project.id: project for project in profile.projects}
    categories = {
        shown.id: project_categories(projects[shown.id], role_types) for shown in selection.projects
    }
    used = {role_type for kinds in categories.values() for role_type in kinds}
    tabs = [Tab(id=kind.id, name=kind.name) for kind in profile.role_types if kind.id in used]
    contact = selection.contact
    links = [Link(text=contact.email, href=f"mailto:{contact.email}")] if contact.email else []
    if contact.url and safe_url(contact.url):
        links.append(Link(text=_display(contact.url), href=contact.url))
    for social in contact.profiles:
        if social.url and safe_url(social.url):
            links.append(Link(text=social.network, href=social.url))
    label = profile.basics.label
    about = profile.basics.summary
    present = {
        "about": bool(about or selection.education),
        "experience": bool(selection.work),
        "projects": bool(selection.projects),
        "skills": bool(selection.skills),
        "contact": True,
    }
    return Page(
        selection=selection,
        sections=tuple(Tab(id=key, name=key.title()) for key, shown in present.items() if shown),
        initials="".join(word[0] for word in contact.name.split()[:3]).upper(),
        label=label,
        about=about,
        links=tuple(links),
        tabs=tuple(tabs) if len(tabs) > 1 else (),
        categories=categories,
        downloads=tuple(downloads),
        site_url=site_url if site_url and safe_url(site_url) else None,
        year=year,
        description=about or label or contact.name,
        json_ld=_script_json(person_json_ld(selection, label, about, site_url)),
        open_to_work=open_to_work,
        indexing=indexing,
        assets=assets,
    )


def web_variant(default_visibility: Visibility) -> Variant:
    """The page's variant. Only public fields show on the web, so unless the default visibility
    is public, the contact details without a visibility of their own are hidden."""
    if default_visibility == "public":
        return WEB_VARIANT
    return WEB_VARIANT.model_copy(update={"hidden": NO_DEFAULT_VISIBILITY})


def project_categories(project: Project, role_types: Sequence[str]) -> tuple[str, ...]:
    """The role types a project is filed under: the ones it's kept for, in profile order."""
    return tuple(kind for kind in role_types if kind in project.keep_for)


def render_page(page: Page) -> str:
    return _environment().get_template("index.html").render(page=page)


@cache
def _environment() -> SandboxedEnvironment:
    """Made once, so the template is compiled once per process, not for each request. It's still
    compiled again when the file changes."""
    environment = SandboxedEnvironment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters.update(url=_url_or_hash, dates=date_range)  # pyright: ignore[reportUnknownMemberType]
    return environment


def person_json_ld(
    selection: Selection, label: str | None, about: str | None, site_url: str | None
) -> dict[str, object]:
    """Schema.org `Person` data for search engines (FR-PRT-6)."""
    contact = selection.contact
    person: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": contact.name,
    }
    if label:
        person["jobTitle"] = label
    if about:
        person["description"] = about
    url = site_url or contact.url
    if url and safe_url(url):
        person["url"] = url
    if contact.email:
        person["email"] = contact.email
    same_as = [social.url for social in contact.profiles if social.url and safe_url(social.url)]
    if same_as:
        person["sameAs"] = same_as
    current = [role for role in selection.work if role.end_date is None]
    if current:
        person["worksFor"] = {"@type": "Organization", "name": current[0].organisation}
    if selection.education:
        person["alumniOf"] = [
            {"@type": "EducationalOrganization", "name": school.institution}
            for school in selection.education
        ]
    skills = [skill for line in selection.skills for skill in line.skills]
    if skills:
        person["knowsAbout"] = skills
    return person


def safe_url(url: str) -> bool:
    """Only web and email links: never `javascript:` or other schemes."""
    return urlsplit(url.strip()).scheme.lower() in _SAFE_SCHEMES


def date_range(start: YearMonth | None, end: YearMonth | None) -> str:
    """ "Mar 2024 - Present", with an ASCII hyphen (requirement FR-WRT-6)."""
    if start is None:
        return _month(end) if end else ""
    return f"{_month(start)} - {_month(end) if end else 'Present'}"


def _month(value: YearMonth) -> str:
    year, number = value.split("-")
    return f"{_MONTHS[int(number) - 1]} {year}"


def _url_or_hash(url: str) -> str:
    return url if safe_url(url) else "#"


def _display(url: str) -> str:
    parts = urlsplit(url)
    return (parts.netloc.removeprefix("www.") + parts.path).rstrip("/")


def _script_json(data: dict[str, object]) -> Markup:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return Markup("".join(_SCRIPT_ESCAPES.get(char, char) for char in text))
