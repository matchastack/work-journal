"""Consistency linter and resume rules (T-009; requirements §8 and FR-PRF-6).

`lint_profile(profile)` checks the master profile, as `wj lint` does. `check_sendable(selection,
profile, confirmed)` checks what one sendable resume prints, that is, a resume tailored to a posting
(OQ-6). `require_sendable` raises `NotSendable` when that finds errors: errors block rendering a
sendable resume, and warnings don't.

Only what can be printed is checked, so benched, planned and private items are skipped (R8). Each
finding has a rule ID, a severity and a location: a path like `work[northwind].highlights[nw_x]`,
as in `wj` change lists.

- `dates` (warning): dates in the text are written in more than one format.
- `spelling` (warning): a skill isn't written the preferred way, e.g. `NodeJS` for `Node.js`.
- `duplicates` (warning): the same bullet appears twice.
- R1 (warning): a listed skill isn't in the skills catalogue.
- R2: placeholder text such as TODO (error); a project without dates (warning).
- R3: a skill on the gaps list (error). A skill marked verify-before-shipping needs confirmation
  for each sendable resume (error without it); in the profile, each use is a reminder (warning).
- R4 (warning): the same metric, value and unit, under two different roles or projects.
- R9: a title that isn't approved (error), or an approved title that adds seniority (warning).
- R10 (error): a breach of an education rule. Code reads two kinds of rule: never printing the GPA
  ("never print GPA") and how many courses to list ("keep 4-6 courses", "at most 6 courses").
  Rules it can't read are listed in the report's notes.
"""

import re
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Literal

from app.schema.common import Model
from app.schema.profile import Bullet, Education, Profile, Skill, SocialProfile
from app.selection import SelectedBullet, SelectedEducation, Selection
from app.validate.quantities import find_quantities

Severity = Literal["error", "warning"]
RuleId = Literal["dates", "spelling", "duplicates", "R1", "R2", "R3", "R4", "R9", "R10"]


class Finding(Model):
    rule: RuleId
    severity: Severity
    location: str
    """Where the problem is, e.g. `work[northwind].highlights[nw_export]`."""
    message: str


class LintReport(Model):
    findings: tuple[Finding, ...] = ()
    """Errors first, then warnings."""
    notes: tuple[str, ...] = ()
    """What code couldn't check, e.g. an education rule it can't read."""

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "warning")

    @property
    def ok(self) -> bool:
        """True when nothing blocks sending (warnings are allowed)."""
        return not self.errors


class NotSendable(Exception):
    """A sendable resume breaks a resume rule, so it must not be rendered."""

    def __init__(self, report: LintReport) -> None:
        self.report = report
        first, more = report.errors[0], len(report.errors) - 1
        extra = f" (and {more} more error{'s' if more > 1 else ''})" if more else ""
        super().__init__(f"{first.location}: {first.message}{extra}")


def lint_profile(profile: Profile) -> LintReport:
    """Check the master profile: everything in it that can reach a resume."""
    content = _profile_content(profile)
    findings = [
        *_placeholders(content),
        *_undated(
            f"projects[{project.id}]"
            for project in profile.projects
            if project.status == "active" and not (project.start_date or project.end_date)
        ),
        *_gaps(content, profile.skill_gaps),
        *_verify_before_shipping(content, profile.skills, confirmed=None),
        *_unknown_skills(content, profile),
        *_added_seniority(profile),
        *_shared_metrics(content, _allowed_terms(profile)),
        *_duplicates(content),
        *_spelling(content, profile.skills),
        *_dates(content),
    ]
    notes: list[str] = []
    for entry in profile.education:
        rules = _read_rules(entry.rules)
        where = f"education[{entry.id}]"
        notes.extend(_unread(where, entry.rules, rules))
        if rules.no_gpa:
            findings.extend(_gpa(_education_texts(content, where)))
        if rules.courses is not None:
            low, high = rules.courses
            findings.extend(
                Finding(
                    rule="R10",
                    severity="warning",
                    location=f"{where}.courseworkSubsets[{subset.role_type}]",
                    message=f"lists {len(subset.courses)} courses, but the education rules "
                    f"allow {low} to {high} on a resume",
                )
                for subset in entry.coursework_subsets
                if subset.courses and not low <= len(subset.courses) <= high
            )
    return LintReport(findings=_ordered(findings), notes=tuple(notes))


def check_sendable(
    selection: Selection, profile: Profile, confirmed: Collection[str] = ()
) -> LintReport:
    """Check what a sendable resume prints. `confirmed` lists the skills marked
    verify-before-shipping that the owner confirmed for this resume (R3)."""
    content = _selection_content(selection)
    findings = [
        *_placeholders(content),
        *_undated(
            f"projects[{project.id}]"
            for project in selection.projects
            if not (project.start_date or project.end_date)
        ),
        *_gaps(content, profile.skill_gaps),
        *_verify_before_shipping(content, profile.skills, confirmed=confirmed),
        *_unknown_skills(content, profile),
        *_unapproved_titles(selection, profile),
        *_shared_metrics(content, _allowed_terms(profile)),
        *_duplicates(content),
        *_spelling(content, profile.skills),
        *_dates(content),
    ]
    notes: list[str] = []
    for entry in selection.education:
        rules = _read_rules(entry.rules)
        where = f"education[{entry.id}]"
        notes.extend(_unread(where, entry.rules, rules))
        if rules.no_gpa:
            findings.extend(_gpa(_education_texts(content, where)))
        if rules.courses is not None and entry.courses:
            low, high = rules.courses
            if not low <= len(entry.courses) <= high:
                findings.append(
                    Finding(
                        rule="R10",
                        severity="error",
                        location=f"{where}.courses",
                        message=f"lists {len(entry.courses)} courses, but the education rules "
                        f"allow {low} to {high}",
                    )
                )
    return LintReport(findings=_ordered(findings), notes=tuple(notes))


def require_sendable(
    selection: Selection, profile: Profile, confirmed: Collection[str] = ()
) -> LintReport:
    """`check_sendable`, raising `NotSendable` if there are errors. Otherwise returns the report,
    which then holds only warnings."""
    report = check_sendable(selection, profile, confirmed)
    if not report.ok:
        raise NotSendable(report)
    return report


def format_report(report: LintReport) -> str:
    """One line per finding, then the notes and a count."""
    lines = [
        f"{finding.severity:<7}  {finding.rule:<10}  {finding.location}: {finding.message}"
        for finding in report.findings
    ]
    if report.notes:
        lines.append("Not checked by code:")
        lines.extend(f"  {note}" for note in report.notes)
    errors, warnings = len(report.errors), len(report.warnings)
    if errors or warnings:
        lines.append(f"{_count(errors, 'error')}, {_count(warnings, 'warning')}.")
    else:
        lines.append("No problems found.")
    return "\n".join(lines)


# --- What can be printed ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Text:
    """A piece of printable text and where it is."""

    location: str
    text: str
    prose: bool = True
    """False for names, contact details, links and course names, which aren't searched for
    skills or dates: a company called Swift Logistics doesn't claim Swift."""
    entry: str | None = None
    """For a bullet, the role, project or education entry it's under (R4)."""


@dataclass(frozen=True)
class _Term:
    """A skill named in a skills line or a project's tech stack."""

    location: str
    name: str


@dataclass(frozen=True)
class _Content:
    texts: tuple[_Text, ...]
    terms: tuple[_Term, ...]

    @property
    def prose(self) -> Iterator[_Text]:
        return (text for text in self.texts if text.prose)

    @property
    def bullets(self) -> Iterator[_Text]:
        return (text for text in self.texts if text.entry is not None)


def _fields(where: str, values: dict[str, str | None], *, prose: bool = True) -> list[_Text]:
    return [_Text(f"{where}.{name}", value, prose) for name, value in values.items() if value]


def _bullets(where: str, bullets: Iterable[Bullet | SelectedBullet]) -> list[_Text]:
    return [
        _Text(f"{where}.highlights[{bullet.id}]", bullet.text, entry=where) for bullet in bullets
    ]


def _profile_content(profile: Profile) -> _Content:
    basics = profile.basics
    texts = _fields("basics", {"label": basics.label, "summary": basics.summary})
    texts += _fields(
        "basics",
        {"name": basics.name, "email": basics.email, "phone": basics.phone, "url": basics.url},
        prose=False,
    )
    texts += _links("basics", basics.profiles)
    texts += [
        _Text(f"summaries[{summary.role_type}]", summary.text) for summary in profile.summaries
    ]
    terms: list[_Term] = []
    for role in profile.work:
        where = f"work[{role.id}]"
        texts += _fields(where, {"name": role.name, "location": role.location}, prose=False)
        texts += _fields(where, {"position": role.position, "summary": role.summary})
        texts += [_Text(f"{where}.titleVariants", title) for title in role.title_variants]
        texts += _bullets(where, _printable(role.highlights))
    for entry in profile.education:
        where = f"education[{entry.id}]"
        texts += _education_fields(where, entry)
        texts += _bullets(where, _printable(entry.highlights))
    for project in profile.projects:
        if project.status != "active":
            continue
        where = f"projects[{project.id}]"
        texts += _fields(where, {"name": project.name, "url": project.url}, prose=False)
        texts += _fields(where, {"description": project.description})
        texts += _bullets(where, _printable(project.highlights))
        terms += [_Term(f"{where}.keywords", keyword) for keyword in project.keywords]
    for preset in profile.skill_presets:
        for line in preset.lines:
            where = f"skillPresets[{preset.role_type}].lines[{line.label}]"
            terms += [_Term(where, skill) for skill in line.skills]
    return _Content(tuple(texts), tuple(terms))


def _selection_content(selection: Selection) -> _Content:
    contact = selection.contact
    texts = _fields(
        "contact",
        {
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "location": contact.location,
            "url": contact.url,
        },
        prose=False,
    )
    texts += _links("contact", contact.profiles)
    if selection.summary:
        texts.append(_Text("summary", selection.summary))
    terms: list[_Term] = []
    for role in selection.work:
        where = f"work[{role.id}]"
        texts += _fields(where, {"name": role.organisation, "location": role.location}, prose=False)
        texts += _fields(where, {"title": role.title})
        texts += _bullets(where, role.bullets)
    for entry in selection.education:
        where = f"education[{entry.id}]"
        texts += _education_fields(where, entry)
        texts += _bullets(where, entry.bullets)
    for project in selection.projects:
        where = f"projects[{project.id}]"
        texts += _fields(where, {"name": project.name, "url": project.url}, prose=False)
        texts += _bullets(where, project.bullets)
        terms += [_Term(f"{where}.keywords", keyword) for keyword in project.keywords]
    for line in selection.skills:
        terms += [_Term(f"skills[{line.label}]", skill) for skill in line.skills]
    return _Content(tuple(texts), tuple(terms))


def _printable(bullets: Iterable[Bullet]) -> list[Bullet]:
    return [
        bullet for bullet in bullets if bullet.status == "active" and bullet.visibility != "private"
    ]


def _links(where: str, links: Iterable[SocialProfile]) -> list[_Text]:
    return [
        _Text(f"{where}.profiles[{link.network}]", link.url or link.username or "", prose=False)
        for link in links
    ]


def _education_fields(where: str, entry: Education | SelectedEducation) -> list[_Text]:
    texts = _fields(
        where, {"institution": entry.institution, "location": entry.location}, prose=False
    )
    texts += _fields(
        where, {"studyType": entry.study_type, "area": entry.area, "honours": entry.honours}
    )
    return texts + [_Text(f"{where}.courses", course, prose=False) for course in entry.courses]


# --- Checks ------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(
    r"\b(?:TODO|TBD|TBC|FIXME|XXX+)\b"
    r"|(?i:lorem ipsum)"
    r"|\[[^\]]*\]|\{[^}]*\}|<[A-Za-z][A-Za-z _-]*>"
    r"|\?\?"
    r"|\b[XN]{1,3} ?%"
)
"""Upper-case markers such as TODO (a project may be called "Todo app"), bracketed stand-ins such
as `[metric]`, and `XX%` for a missing number."""


def _placeholders(content: _Content) -> Iterator[Finding]:
    items = [*content.texts, *(_Text(term.location, term.name) for term in content.terms)]
    for item in items:
        if match := _PLACEHOLDER.search(item.text):
            yield Finding(
                rule="R2",
                severity="error",
                location=item.location,
                message=f'placeholder text "{match[0]}"',
            )


def _undated(locations: Iterable[str]) -> Iterator[Finding]:
    for location in locations:
        yield Finding(rule="R2", severity="warning", location=location, message="has no dates")


def _gaps(content: _Content, gaps: Sequence[str]) -> Iterator[Finding]:
    for gap in gaps:
        for location in _uses(content, gap, ()):
            yield Finding(
                rule="R3",
                severity="error",
                location=location,
                message=f"{gap} is on the gaps list, so it must never be claimed",
            )


def _verify_before_shipping(
    content: _Content, skills: Sequence[Skill], confirmed: Collection[str] | None
) -> Iterator[Finding]:
    """In a sendable resume (`confirmed` given), each unconfirmed use is an error. In the profile,
    each use is a reminder that resumes listing the skill need confirmation."""
    done = {name.casefold() for name in confirmed or ()}
    for skill in skills:
        if not skill.verify_before_shipping or skill.name.casefold() in done:
            continue
        for location in _uses(content, skill.name, skill.aliases):
            if confirmed is None:
                yield Finding(
                    rule="R3",
                    severity="warning",
                    location=location,
                    message=f"{skill.name} is marked verify-before-shipping, so each resume that "
                    "lists it needs your confirmation",
                )
            else:
                yield Finding(
                    rule="R3",
                    severity="error",
                    location=location,
                    message=f"{skill.name} is marked verify-before-shipping and isn't confirmed "
                    "for this resume",
                )


def _uses(content: _Content, name: str, aliases: Sequence[str]) -> Iterator[str]:
    """Where a skill is listed or named. Text must match the case, so `Swift` isn't `swift`."""
    names = {name.casefold(), *(alias.casefold() for alias in aliases)}
    for term in content.terms:
        if term.name.casefold() in names:
            yield term.location
    for text in content.prose:
        if any(_term_pattern(spelling, False).search(text.text) for spelling in (name, *aliases)):
            yield text.location


def _unknown_skills(content: _Content, profile: Profile) -> Iterator[Finding]:
    known = {
        spelling.casefold() for skill in profile.skills for spelling in (skill.name, *skill.aliases)
    }
    known |= {gap.casefold() for gap in profile.skill_gaps}  # Reported by R3 instead.
    for term in content.terms:
        if term.name.casefold() not in known:
            yield Finding(
                rule="R1",
                severity="warning",
                location=term.location,
                message=f"{term.name} isn't in the skills catalogue; add it there, with its "
                "tier, or leave it out",
            )


_SENIORITY = re.compile(
    r"\b(?:senior|sr|lead|principal|staff|head|chief|director|manager|vp|vice president)\b",
    re.IGNORECASE,
)


def _added_seniority(profile: Profile) -> Iterator[Finding]:
    for role in profile.work:
        official = {word.casefold() for word in _SENIORITY.findall(role.position)}
        for title in role.title_variants:
            added = [word for word in _SENIORITY.findall(title) if word.casefold() not in official]
            if added:
                yield Finding(
                    rule="R9",
                    severity="warning",
                    location=f"work[{role.id}].titleVariants",
                    message=f'"{title}" adds "{added[0]}" to the official title '
                    f'"{role.position}"; keep it only if it was earned',
                )


def _unapproved_titles(selection: Selection, profile: Profile) -> Iterator[Finding]:
    roles = {role.id: role for role in profile.work}
    for selected in selection.work:
        role = roles.get(selected.id)
        approved = (role.position, *role.title_variants) if role else ()
        if selected.title not in approved:
            yield Finding(
                rule="R9",
                severity="error",
                location=f"work[{selected.id}].title",
                message=f'"{selected.title}" isn\'t an approved title for this role',
            )


def _allowed_terms(profile: Profile) -> list[str]:
    """Skill names, so that the number in `Python 3` isn't read as a metric."""
    names = [spelling for skill in profile.skills for spelling in (skill.name, *skill.aliases)]
    return names + [keyword for project in profile.projects for keyword in project.keywords]


def _shared_metrics(content: _Content, allowed_terms: Sequence[str]) -> Iterator[Finding]:
    first_seen: dict[tuple[object, ...], tuple[str | None, str]] = {}
    for text in content.bullets:
        for quantity in find_quantities(text.text, allowed_terms):
            key = (quantity.kind, quantity.values, quantity.unit)
            entry, location = first_seen.setdefault(key, (text.entry, text.location))
            if entry != text.entry:
                yield Finding(
                    rule="R4",
                    severity="warning",
                    location=text.location,
                    message=f'"{quantity.text}" also appears in {location}; make sure both '
                    "are right",
                )


def _duplicates(content: _Content) -> Iterator[Finding]:
    first_seen: dict[str, str] = {}
    for text in content.bullets:
        key = re.sub(r"[\W_]+", " ", text.text.casefold()).strip()
        location = first_seen.setdefault(key, text.location)
        if location != text.location:
            yield Finding(
                rule="duplicates",
                severity="warning",
                location=text.location,
                message=f"same text as {location}",
            )


def _spelling(content: _Content, skills: Sequence[Skill]) -> Iterator[Finding]:
    """Aliases are always wrong. Other capitalisations are wrong in lists, and in text when they
    can't be an ordinary word (`Javascript` for `JavaScript`, but not `react` for `React`)."""
    searched = [
        (spelling, skill.name)
        for skill in skills
        for spelling in (*skill.aliases, *([skill.name] if " " not in skill.name else []))
    ]
    for text in content.prose:
        found = {
            match.start(): (match[0], preferred)
            for spelling, preferred in searched
            for match in _term_pattern(spelling, True).finditer(text.text)
            if match[0] != preferred
            and (spelling != preferred or _miscapitalised(match[0], preferred))
        }
        for written, preferred in dict.fromkeys(found[start] for start in sorted(found)):
            yield _misspelt(text.location, written, preferred)
    preferred_for = {
        spelling.casefold(): skill.name
        for skill in skills
        for spelling in (skill.name, *skill.aliases)
    }
    for term in content.terms:
        preferred = preferred_for.get(term.name.casefold())
        if preferred is not None and term.name != preferred:
            yield _misspelt(term.location, term.name, preferred)


def _misspelt(location: str, written: str, preferred: str) -> Finding:
    return Finding(
        rule="spelling",
        severity="warning",
        location=location,
        message=f'write "{written}" as "{preferred}"',
    )


def _miscapitalised(written: str, name: str) -> bool:
    """Whether `written` is `name` with the wrong capitals, rather than an ordinary word: names
    with digits or `.+#/` can't be words, and neither can `javascript` for `JavaScript`. An
    all-lower-case acronym can (`rest` for REST), and so can a plain word (`react` for React)."""
    if re.search(r"[\d.+#/]", name):
        return True
    if written.islower() and name.isupper():
        return False
    return bool(re.search(r".[A-Z]", name))


@cache
def _term_pattern(term: str, ignore_case: bool) -> re.Pattern[str]:
    """`term` as a whole word, so `Go` doesn't match `Google`, `C` doesn't match `C++` and `SQL`
    doesn't match `T-SQL`. A suffix still counts: `Rust-based` and `Rust's` name Rust."""
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(rf"(?<![\w.+#&-]){re.escape(term)}(?![\w+#&]|\.\w)", flags)


_MONTHS = "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec"
_FULL_MONTHS = "January|February|March|April|June|July|August|September|October|November|December"
_DATE_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Month YYYY", re.compile(rf"\b(?:{_FULL_MONTHS}) \d{{4}}\b")),
    ("Mon YYYY", re.compile(rf"\b(?:{_MONTHS})\.? \d{{4}}\b")),
    ("MM/YYYY", re.compile(r"\b(?:0?[1-9]|1[0-2])/\d{4}\b")),
    ("YYYY-MM", re.compile(r"\b\d{4}-(?:0[1-9]|1[0-2])\b")),
)
"""May is left out: `May 2024` fits both month formats."""


def _dates(content: _Content) -> Iterator[Finding]:
    found = [
        (name, match[0], text.location)
        for text in content.prose
        for name, pattern in _DATE_FORMATS
        for match in pattern.finditer(text.text)
    ]
    counts = Counter(name for name, _, _ in found)
    if len(counts) < 2:
        return
    usual = counts.most_common(1)[0][0]
    example = next(written for name, written, _ in found if name == usual)
    for name, written, location in found:
        if name != usual:
            yield Finding(
                rule="dates",
                severity="warning",
                location=location,
                message=f'"{written}" is written as {name}, but most dates read like '
                f'"{example}" ({usual})',
            )


# --- Education rules (R10) ---------------------------------------------------------------------

_NO_GPA = re.compile(r"\b(?:never|don't|do not|no)\b[^.]*?\bC?GPA\b", re.IGNORECASE)
_COURSES = re.compile(r"\bcourse", re.IGNORECASE)
_COUNT_RANGE = re.compile(r"\b(\d{1,2}) ?(?:-|to) ?(\d{1,2})\b")
_COUNT_MAX = re.compile(r"\b(?:at most|max(?:imum)?|up to|no more than) (\d{1,2})\b", re.IGNORECASE)
_GPA_PRINTED = re.compile(r"\bC?GPA\b|\b\d\.\d{1,2} ?/ ?\d{1,2}(?:\.\d{1,2})?\b", re.IGNORECASE)


@dataclass(frozen=True)
class _EducationRules:
    no_gpa: bool = False
    courses: tuple[int, int] | None = None
    """How many courses a resume may list; listing none is always allowed."""
    unread: tuple[int, ...] = ()
    """Rules code can't read, numbered from 1."""


def _read_rules(rules: Sequence[str]) -> _EducationRules:
    no_gpa = False
    courses: tuple[int, int] | None = None
    unread: list[int] = []
    for number, rule in enumerate(rules, start=1):
        understood = False
        if _NO_GPA.search(rule):
            no_gpa = understood = True
        if _COURSES.search(rule):
            if match := _COUNT_RANGE.search(rule):
                courses, understood = (int(match[1]), int(match[2])), True
            elif match := _COUNT_MAX.search(rule):
                courses, understood = (1, int(match[1])), True
        if not understood:
            unread.append(number)
    return _EducationRules(no_gpa=no_gpa, courses=courses, unread=tuple(unread))


def _unread(where: str, rules: Sequence[str], read: _EducationRules) -> Iterator[str]:
    for number in read.unread:
        yield f'{where}.rules: rule {number} isn\'t checked: "{rules[number - 1]}"'


def _education_texts(content: _Content, where: str) -> Iterator[_Text]:
    return (text for text in content.texts if text.location.startswith(f"{where}."))


def _gpa(texts: Iterable[_Text]) -> Iterator[Finding]:
    for text in texts:
        if match := _GPA_PRINTED.search(text.text):
            yield Finding(
                rule="R10",
                severity="error",
                location=text.location,
                message=f'shows a GPA ("{match[0]}"), but the education rules say never to '
                "print it",
            )


def _ordered(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=lambda finding: finding.severity != "error"))


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
