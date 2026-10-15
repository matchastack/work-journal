"""The keyword coverage report: which of a posting's terms a tailored resume shows (FR-TLR-6).

Each skill and key term of the posting is looked for, as whole words and ignoring case, in what
the resume prints: the summary, titles, bullets, education, project names and keywords, and
skills lines. A covered term lists where it appears, as a path like `skills[Languages]` or
`work[northwind].highlights[nw_export]`. A missing term is a gap to know about: nothing adds it.
Applicant tracking systems match terms as the posting spells them, so a different spelling of a
skill doesn't count.
"""

import re
from collections.abc import Iterator
from typing import Literal

from app.schema.common import Model, NonEmptyStr
from app.schema.jobs import JobPosting
from app.selection import SelectedBullet, Selection

TermKind = Literal["must_have", "nice_to_have", "key_term"]
KIND_NAMES: dict[TermKind, str] = {
    "must_have": "must-have",
    "nice_to_have": "nice-to-have",
    "key_term": "key term",
}


class TermCoverage(Model):
    term: NonEmptyStr
    kind: TermKind
    """Where the posting lists the term; the first, if it lists it twice."""
    locations: tuple[str, ...] = ()
    """Where the resume shows it. Empty for a missing term."""


class CoverageReport(Model):
    covered: tuple[TermCoverage, ...] = ()
    missing: tuple[TermCoverage, ...] = ()
    """Gaps: terms the resume doesn't show, which are never added to it."""


def coverage(selection: Selection, posting: JobPosting) -> CoverageReport:
    """Which of the posting's terms `selection`, the resume as printed, shows, and where."""
    texts = list(_texts(selection))
    covered: list[TermCoverage] = []
    missing: list[TermCoverage] = []
    seen: set[str] = set()
    lists: tuple[tuple[TermKind, tuple[str, ...]], ...] = (
        ("must_have", posting.must_have),
        ("nice_to_have", posting.nice_to_have),
        ("key_term", posting.key_terms),
    )
    for kind, terms in lists:
        for term in terms:
            if term.casefold() in seen:
                continue
            seen.add(term.casefold())
            pattern = _pattern(term)
            locations = tuple(where for where, text in texts if pattern.search(text))
            entry = TermCoverage(term=term, kind=kind, locations=locations)
            (covered if locations else missing).append(entry)
    return CoverageReport(covered=tuple(covered), missing=tuple(missing))


def report_lines(report: CoverageReport) -> list[str]:
    """The report as `wj tailor` prints it: each covered term and where, then the gaps."""
    total = len(report.covered) + len(report.missing)
    if not total:
        return ["The posting names no skills or key terms to cover."]
    lines = [f"Covers {len(report.covered)} of the posting's {total} skills and key terms:"]
    lines += [
        f"   {entry.term} ({KIND_NAMES[entry.kind]}): {', '.join(entry.locations)}"
        for entry in report.covered
    ]
    if report.missing:
        lines.append("Gaps, which are never added:")
        for kind, name in KIND_NAMES.items():
            terms = [entry.term for entry in report.missing if entry.kind == kind]
            if terms:
                lines.append(f"   {name}: {', '.join(terms)}")
    return lines


def _texts(selection: Selection) -> Iterator[tuple[str, str]]:
    """What the resume prints, as (location, text) pairs."""
    if selection.summary:
        yield "summary", selection.summary
    for role in selection.work:
        where = f"work[{role.id}]"
        yield f"{where}.title", role.title
        yield from _bullets(where, role.bullets)
    for entry in selection.education:
        where = f"education[{entry.id}]"
        yield where, " ".join(filter(None, (entry.study_type, entry.area, entry.honours)))
        if entry.courses:
            yield f"{where}.courses", ", ".join(entry.courses)
        yield from _bullets(where, entry.bullets)
    for project in selection.projects:
        where = f"projects[{project.id}]"
        yield f"{where}.name", project.name
        if project.keywords:
            yield f"{where}.keywords", ", ".join(project.keywords)
        yield from _bullets(where, project.bullets)
    for line in selection.skills:
        yield f"skills[{line.label}]", ", ".join(line.skills)


def _bullets(where: str, bullets: tuple[SelectedBullet, ...]) -> Iterator[tuple[str, str]]:
    for bullet in bullets:
        yield f"{where}.highlights[{bullet.id}]", bullet.text


def _pattern(term: str) -> re.Pattern[str]:
    """`term` as whole words, ignoring case and how the words are spaced."""
    body = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
