"""LaTeX-safe Jinja templates (FR-RES-1, FR-RES-2, NFR-SEC-3).

Templates use `\\VAR{...}` for values, `\\BLOCK{...}` for statements and `\\#{...}` for comments,
so they stay readable as LaTeX. Every printed value is escaped, so text can't inject LaTeX, and a
template that prints a missing value fails instead of printing "None". Templates run in Jinja's
sandbox and see only the data they're given.

The escapes suit pdfLaTeX with the default OT1 font encoding, which the resume template uses.
Symbols that would otherwise come from a bitmap font, such as the dollar and multiplication
signs or "~" for "about", become Computer Modern math symbols, so the text stays sharp and
extractable.
"""

import re
from pathlib import Path
from urllib.parse import quote

from jinja2 import FileSystemLoader, StrictUndefined, Template
from jinja2.sandbox import SandboxedEnvironment

from app.schema.common import YearMonth

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\ensuremath{\$}",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\ensuremath{\sim}",
    "<": r"\ensuremath{<}",
    ">": r"\ensuremath{>}",
    "|": r"\ensuremath{|}",
    "\u00d7": r"\ensuremath{\times}",
    "\u2192": r"\ensuremath{\rightarrow}",
    "\u2248": r"\ensuremath{\approx}",
    "\u2264": r"\ensuremath{\leq}",
    "\u2265": r"\ensuremath{\geq}",
    "\u00b1": r"\ensuremath{\pm}",
    "\u00b0": r"\ensuremath{^\circ}",
}
_SPECIAL = re.compile("|".join(re.escape(char) for char in _ESCAPES))
_WHITESPACE = re.compile(r"\s+")
_URL_SAFE = "-._~:/?#[]@!&'()*+,;=%"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class Latex(str):
    """Text that is already LaTeX, such as an escaped URL. Templates print it unchanged."""


class TemplateValueError(ValueError):
    """A template printed a missing value."""


def escape_latex(text: str) -> Latex:
    """LaTeX that prints `text` as written. Whitespace runs become one space, so a value can't
    start a paragraph."""
    collapsed = _WHITESPACE.sub(" ", text)
    return Latex(_SPECIAL.sub(lambda match: _ESCAPES[match[0]], collapsed))


def escape_url(url: str) -> Latex:
    """A URL for `\\href{...}`, safe even inside another macro's argument."""
    encoded = quote(url.strip(), safe=_URL_SAFE)
    return Latex(encoded.replace("%", r"\%").replace("#", r"\#"))


def month(value: YearMonth) -> str:
    """ "2024-03" -> "Mar 2024"."""
    year, number = value.split("-")
    return f"{_MONTHS[int(number) - 1]} {year}"


def dates(start: YearMonth | None, end: YearMonth | None) -> str:
    """A date range as a resume shows it: "Mar 2024 - Present", with an ASCII hyphen
    (requirement FR-WRT-6)."""
    if start is None:
        return month(end) if end else ""
    return f"{month(start)} - {month(end) if end else 'Present'}"


def latex_environment(templates: Path) -> SandboxedEnvironment:
    """A Jinja environment for LaTeX templates in the `templates` folder."""
    environment = SandboxedEnvironment(
        loader=FileSystemLoader(templates),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
        undefined=StrictUndefined,
        finalize=_finalize,
    )
    # Jinja leaves these registries unannotated.
    environment.filters.update(url=escape_url, month=month)  # pyright: ignore[reportUnknownMemberType]
    environment.globals.update(dates=dates)  # pyright: ignore[reportUnknownMemberType]
    return environment


def load_template(templates: Path, name: str) -> Template:
    """The template `<name>/resume.tex` in the `templates` folder."""
    return latex_environment(templates).get_template(f"{name}/resume.tex")


def _finalize(value: object) -> str:
    if value is None:
        raise TemplateValueError("the template printed a missing value; check it with BLOCK{if}")
    if isinstance(value, Latex):
        return value
    return escape_latex(str(value))
