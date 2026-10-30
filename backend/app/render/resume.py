"""Render a selection as a resume PDF within its page limit (FR-RES-2 to FR-RES-5, FR-RES-7).

The variant's template (`backend/templates/<name>/resume.tex`) is filled in with the selection,
compiled in the sandbox, fitted to the page limit by cutting bullets, and checked: the PDF must
have extractable text, and bitmap fonts are reported as a warning.

Renders are cached by content hash. A render's key (`render_key`) is the SHA-256 of what it's
made from: the LaTeX source the template makes from the whole selection, the template's other
files, and, when there's a page limit, the order fitting would cut bullets in, since which ones
it cuts depends on more than the source. The same key gives the same render, so a caller that
kept a render under its key passes it back (`StoredRender`), and it's rebuilt from the stored PDF
without compiling.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Template, TemplateError, TemplateNotFound

from app.render.compile import COMPILE_TIMEOUT_S, CompiledPdf, compile_latex, inspect_pdf
from app.render.fit import Cut, apply_cuts, cut_order, fit
from app.render.latex import TemplateValueError, load_template
from app.selection import Contact, Selection

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
"""One folder per template, holding `resume.tex` and any files it needs, such as a class file."""
_MAIN = "resume.tex"


@dataclass(frozen=True)
class RenderedResume:
    pdf: bytes
    tex: str
    """The LaTeX source the PDF was compiled from, after the cuts. It needs the template's other
    files, if it has any, to compile."""
    pages: int
    text: str
    """The PDF's extractable text."""
    cuts: tuple[Cut, ...]
    """What was cut to fit the page limit, in the order it was cut."""
    warnings: tuple[str, ...]
    selection: Selection
    """What the PDF shows: the selection without what was cut."""
    key: str
    """What the render was made from (`render_key`): a render kept under this key can be used
    again instead of compiling."""
    reused: bool = False
    """Whether it was rebuilt from a stored render, without compiling."""


@dataclass(frozen=True)
class StoredRender:
    """A render kept under its key: the PDF, and how many bullets fitting cut. With the
    selection, that's enough to rebuild the rest."""

    pdf: bytes
    bullet_cuts: int


class RenderError(Exception):
    """The template is missing or broken, or the PDF failed a check."""


def render_resume(
    selection: Selection,
    templates: Path = TEMPLATES_DIR,
    *,
    timeout: float = COMPILE_TIMEOUT_S,
    stored: StoredRender | None = None,
) -> RenderedResume:
    """Render `selection` with its variant's template, fitted to the variant's page limit. When
    `stored` is the render kept under `render_key(selection)`, it's rebuilt from that without
    compiling.

    Raises `RenderError`, `CompileError` when LaTeX fails, or `FitError` when the page limit
    can't be met.
    """
    template = _Template.load(selection.template, templates)
    key = template.key(selection)
    if stored is None:

        def render(chosen: Selection) -> CompiledPdf:
            return compile_latex(template.source(chosen), files=template.files, timeout=timeout)

        fitted = fit(selection, render)
        shown, cuts, pdf = fitted.selection, fitted.cuts, fitted.pdf
    else:
        shown, cuts = selection, ()
        if stored.bullet_cuts:
            shown, cuts = apply_cuts(selection, cut_order(selection)[: stored.bullet_cuts])
        pdf = inspect_pdf(stored.pdf)
    if not pdf.text.strip():
        raise RenderError("the PDF has no text that can be extracted")
    warnings: list[str] = []
    if pdf.bitmap_fonts:
        warnings.append(
            "Some text uses a bitmap font, which looks rough and may not extract; "
            "look for an unusual symbol."
        )
    return RenderedResume(
        pdf=pdf.pdf,
        tex=template.source(shown),
        pages=pdf.pages,
        text=pdf.text,
        cuts=cuts,
        warnings=tuple(warnings),
        selection=shown,
        key=key,
        reused=stored is not None,
    )


def render_key(selection: Selection, templates: Path = TEMPLATES_DIR) -> str:
    """The key a render of `selection` is kept under, found without compiling: the SHA-256 of
    the LaTeX source the template makes from the whole selection, the template's other files,
    and, with a page limit, the limit and the order fitting would cut bullets in.

    Raises `RenderError` when the template is missing or broken.
    """
    return _Template.load(selection.template, templates).key(selection)


@dataclass(frozen=True)
class _Template:
    name: str
    jinja: Template
    files: dict[str, bytes]
    """The template's other files, such as a class file, by name."""

    @classmethod
    def load(cls, name: str, templates: Path) -> "_Template":
        try:
            jinja = load_template(templates, name)
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
        return cls(name=name, jinja=jinja, files=files)

    def source(self, chosen: Selection) -> str:
        try:
            return self.jinja.render(selection=chosen, contact=contact_items(chosen.contact))
        except (TemplateError, TemplateValueError) as error:
            raise RenderError(f"the {self.name} template failed: {error}") from error

    def key(self, selection: Selection) -> str:
        limit = selection.max_pages
        made_from = {
            "source": self.source(selection),
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()},
            "maxPages": limit,
            "cutOrder": []
            if limit is None
            else [[ref.section, ref.item, ref.bullet] for ref in cut_order(selection)],
        }
        return hashlib.sha256(json.dumps(made_from, sort_keys=True).encode()).hexdigest()


def contact_items(contact: Contact) -> list[tuple[str, str | None]]:
    """The contact line as (text, link) pairs, in the order a resume shows them."""
    items: list[tuple[str, str | None]] = []
    if contact.phone:
        items.append((contact.phone, None))
    if contact.email:
        items.append((contact.email, f"mailto:{contact.email}"))
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
