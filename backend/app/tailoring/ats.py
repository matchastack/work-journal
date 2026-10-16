"""The ATS check: a resume's PDF read back the way an applicant tracking system reads it
(FR-TLR-9, FR-RES-4).

pypdf extracts the PDF's text, as the render does (`app/render/compile.py`), which is roughly what
an applicant tracking system reads. Then:
- **terms:** each term the coverage report found on the resume is in the text, as whole words,
  ignoring case and line breaks. A term split by a hyphen at the end of a line is reported, since
  a system reads it as two pieces.
- **sections:** each section's heading is a line of its own, in the order the resume prints them.
- **characters:** the text has no ligatures (such as "ﬁ" for "fi"), replacement or private-use
  characters, control characters or unmapped glyphs like `(cid:12)`, each a sign that text didn't
  extract as written.

Bitmap fonts, which also extract badly, are reported by the render itself.
"""

import re
from collections.abc import Sequence
from typing import Literal

from pypdf.errors import PyPdfError

from app.render.compile import inspect_pdf
from app.schema.common import Model, NonEmptyStr
from app.schema.variant import Section
from app.selection import Selection
from app.tailoring.coverage import term_pattern

SECTION_HEADINGS: dict[Section, str] = {
    "summary": "Summary",
    "education": "Education",
    "work": "Work Experience",
    "projects": "Projects",
    "skills": "Technical Skills",
}
"""The heading the resume template prints for each section."""

AtsCode = Literal[
    "unreadable",
    "no_text",
    "term_missing",
    "term_hyphenated",
    "section_missing",
    "section_order",
    "ligature",
    "garbled",
]

_LIGATURES = re.compile("[ﬀ-ﬆ]")
_GARBLED = re.compile(r"[�-\x00-\x08\x0b\x0c\x0e-\x1f]|\(cid:\d+\)")
_LINE_BREAK_HYPHEN = re.compile(r"-[ \t]*\n\s*")


class AtsFinding(Model):
    code: AtsCode
    message: NonEmptyStr


class AtsReport(Model):
    findings: tuple[AtsFinding, ...] = ()
    terms: int = 0
    """How many covered terms were looked for."""
    sections: tuple[str, ...] = ()
    """The headings found, in the order the text has them."""

    @property
    def passed(self) -> bool:
        return not self.findings


def check_pdf(pdf: bytes, selection: Selection, terms: Sequence[str]) -> AtsReport:
    """Extract the text of `pdf`, the resume `selection` shows, and check it for `terms`, the
    terms the coverage report found."""
    try:
        text = inspect_pdf(pdf).text
    except PyPdfError:
        return AtsReport(
            findings=(AtsFinding(code="unreadable", message="pypdf can't read the PDF."),),
            terms=len(terms),
        )
    return check_text(text, selection, terms)


def check_text(text: str, selection: Selection, terms: Sequence[str]) -> AtsReport:
    """Check `text`, as pypdf extracted it from the PDF of `selection`, for `terms`."""
    if not text.strip():
        message = "The PDF has no text an applicant tracking system can read."
        return AtsReport(findings=(AtsFinding(code="no_text", message=message),), terms=len(terms))
    findings = [*_terms(text, terms)]
    found, section_findings = _sections(text, selection)
    findings += section_findings
    findings += _characters(text)
    return AtsReport(findings=tuple(findings), terms=len(terms), sections=found)


def report_lines(report: AtsReport) -> list[str]:
    """The result as `wj tailor` prints it."""
    if report.passed:
        terms = f"{report.terms} covered term{'' if report.terms == 1 else 's'}"
        sections = f"{len(report.sections)} section{'' if len(report.sections) == 1 else 's'}"
        return [f"ATS check passed: the PDF's text has its {terms} and its {sections}, in order."]
    return [
        "The ATS check found problems in the PDF's text:",
        *(f"   {finding.message}" for finding in report.findings),
    ]


def printed_sections(selection: Selection) -> list[Section]:
    """The sections the resume prints: those in its order that have something to show."""
    shown: dict[Section, bool] = {
        "summary": bool(selection.summary),
        "education": bool(selection.education),
        "work": bool(selection.work),
        "projects": bool(selection.projects),
        "skills": bool(selection.skills),
    }
    return [section for section in selection.sections if shown[section]]


def _terms(text: str, terms: Sequence[str]) -> list[AtsFinding]:
    joined = _LINE_BREAK_HYPHEN.sub("", text)
    kept = _LINE_BREAK_HYPHEN.sub("-", text)
    findings: list[AtsFinding] = []
    for term in terms:
        pattern = term_pattern(term)
        if pattern.search(text):
            continue
        if pattern.search(joined) or pattern.search(kept):
            message = (
                f'"{term}" is split by a hyphen at the end of a line, so it reads as two words.'
            )
            findings.append(AtsFinding(code="term_hyphenated", message=message))
        else:
            message = f'"{term}" is on the resume but not in the text extracted from the PDF.'
            findings.append(AtsFinding(code="term_missing", message=message))
    return findings


def _sections(text: str, selection: Selection) -> tuple[tuple[str, ...], list[AtsFinding]]:
    """The headings found, in text order, and what's wrong with them."""
    lines = [" ".join(line.split()).casefold() for line in text.splitlines()]
    positions: dict[str, int] = {}
    findings: list[AtsFinding] = []
    for section in printed_sections(selection):
        heading = SECTION_HEADINGS[section]
        if heading.casefold() in lines:
            positions[heading] = lines.index(heading.casefold())
        else:
            message = f'The "{heading}" heading isn\'t a line of its own in the text.'
            findings.append(AtsFinding(code="section_missing", message=message))
    expected = list(positions)
    found = tuple(sorted(positions, key=positions.__getitem__))
    if list(found) != expected:
        message = (
            f"The sections read in the order {', '.join(found)}; "
            f"the resume prints {', '.join(expected)}."
        )
        findings.append(AtsFinding(code="section_order", message=message))
    return found, findings


def _characters(text: str) -> list[AtsFinding]:
    findings: list[AtsFinding] = []
    ligatures = sorted(set(_LIGATURES.findall(text)))
    if ligatures:
        written = ", ".join(f"U+{ord(char):04X}" for char in ligatures)
        message = (
            f"The text has ligatures ({written}) where letters should be, so words with them "
            "don't match."
        )
        findings.append(AtsFinding(code="ligature", message=message))
    garbled = _GARBLED.findall(text)
    if garbled:
        count = len(garbled)
        message = (
            f"The text has {count} character{'' if count == 1 else 's'} that didn't extract as "
            "written, such as an unmapped glyph or a replacement character."
        )
        findings.append(AtsFinding(code="garbled", message=message))
    return findings
