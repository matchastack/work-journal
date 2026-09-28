"""Render a selection as a resume PDF within its page limit (FR-RES-2 to FR-RES-5, FR-RES-7).

The variant's template (`backend/templates/<name>/resume.tex`) is filled in with the selection,
compiled in the sandbox, fitted to the page limit by cutting bullets, and checked: the PDF must
have extractable text, and bitmap fonts are reported as a warning.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import TemplateError, TemplateNotFound

from app.render.compile import COMPILE_TIMEOUT_S, CompiledPdf, compile_latex
from app.render.fit import Cut, fit
from app.render.latex import TemplateValueError, load_template
from app.selection import Contact, Selection

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
"""One folder per template, holding `resume.tex` and any files it needs, such as a class file."""
_MAIN = "resume.tex"


@dataclass(frozen=True)
class RenderedResume:
    pdf: bytes
    pages: int
    text: str
    """The PDF's extractable text."""
    cuts: tuple[Cut, ...]
    """What was cut to fit the page limit, in the order it was cut."""
    warnings: tuple[str, ...]


class RenderError(Exception):
    """The template is missing or broken, or the PDF failed a check."""


def render_resume(
    selection: Selection, templates: Path = TEMPLATES_DIR, *, timeout: float = COMPILE_TIMEOUT_S
) -> RenderedResume:
    """Render `selection` with its variant's template, fitted to the variant's page limit.

    Raises `RenderError`, `CompileError` when LaTeX fails, or `FitError` when the page limit
    can't be met.
    """
    name = selection.template
    try:
        template = load_template(templates, name)
    except TemplateNotFound:
        raise RenderError(f"no template {name!r} in {templates}") from None
    except TemplateError as error:
        raise RenderError(f"the {name} template is broken: {error}") from error
    folder = templates / name
    files = {
        path.name: path.read_bytes()
        for path in sorted(folder.iterdir())
        if path.is_file() and path.name != _MAIN
    }

    def render(chosen: Selection) -> CompiledPdf:
        try:
            source = template.render(selection=chosen, contact=contact_items(chosen.contact))
        except (TemplateError, TemplateValueError) as error:
            raise RenderError(f"the {name} template failed: {error}") from error
        return compile_latex(source, files=files, timeout=timeout)

    fitted = fit(selection, render)
    if not fitted.pdf.text.strip():
        raise RenderError("the PDF has no text that can be extracted")
    warnings: list[str] = []
    if fitted.pdf.bitmap_fonts:
        warnings.append(
            "Some text uses a bitmap font, which looks rough and may not extract; "
            "look for an unusual symbol."
        )
    return RenderedResume(
        pdf=fitted.pdf.pdf,
        pages=fitted.pdf.pages,
        text=fitted.pdf.text,
        cuts=fitted.cuts,
        warnings=tuple(warnings),
    )


def contact_items(contact: Contact) -> list[tuple[str, str | None]]:
    """The contact line as (text, link) pairs, in the order a resume shows them."""
    items: list[tuple[str, str | None]] = []
    if contact.email:
        items.append((contact.email, f"mailto:{contact.email}"))
    if contact.phone:
        items.append((contact.phone, None))
    if contact.location:
        items.append((contact.location, None))
    if contact.url:
        items.append((_display(contact.url), contact.url))
    for profile in contact.profiles:
        if profile.url:
            items.append((_display(profile.url), profile.url))
        elif profile.username:
            items.append((f"{profile.network}: {profile.username}", None))
    return items


def _display(url: str) -> str:
    """A URL as a resume shows it: "https://www.example.com/casey/" becomes "example.com/casey"."""
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
