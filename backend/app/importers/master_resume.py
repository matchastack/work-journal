"""Import the owner's master resume: a Jake's Resume LaTeX file with structured comments.

`import_master_resume(source)` is a pure function from the LaTeX text to a profile, a fact for
each active bullet and an import report (FR-IMP-1, FR-IMP-3, FR-IMP-4). The template's commands
give the structure and the structured comments give the metadata. Nothing is sent to an LLM, and
whatever can't be mapped is listed in the report.

The work is split by part of the file:
- `latex` reads the LaTeX, `tags` the structured comments and `values` the values in them;
- `walk` attaches each comment to the heading or bullet it describes;
- `items` builds roles, education entries and projects, and `skills` the skills section;
- this module reads the heading, summaries and open questions, and assembles the profile.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from app.importers.context import ImportContext
from app.importers.items import ItemReader, subset_labels
from app.importers.latex import Command, Comment, LatexError, Text, scan, unescape_comment
from app.importers.report import ImportReport
from app.importers.skills import preset_rows, read_skills
from app.importers.tags import (
    CommentLine,
    Segment,
    benched_entries,
    hanging_entries,
    id_fields,
    segments,
)
from app.importers.values import display_label, host, quote, slug
from app.importers.walk import ARITY, OPTIONAL_GROUP, Walk, walk
from app.schema.fact import Fact
from app.schema.profile import (
    Basics,
    Education,
    OpenQuestion,
    Profile,
    Project,
    Role,
    RoleType,
    SkillPreset,
    SocialProfile,
    Summary,
)


class MasterResumeError(ValueError):
    """The file can't be imported at all, e.g. it has no document body or no name."""


@dataclass(frozen=True)
class ImportResult:
    profile: Profile
    facts: tuple[Fact, ...]
    report: ImportReport


def import_master_resume(source: str) -> ImportResult:
    """Import a master resume written in LaTeX. Raises `MasterResumeError` if it can't be read."""
    begin = source.find("\\begin{document}")
    if begin == -1:
        raise MasterResumeError("the file has no \\begin{document}")
    body_start = begin + len("\\begin{document}")
    end = source.find("\\end{document}", body_start)
    first_line = source.count("\n", 0, body_start) + 1
    try:
        events = scan(
            source[body_start : end if end != -1 else len(source)],
            ARITY,
            optional_group=OPTIONAL_GROUP,
            first_line=first_line,
        )
    except LatexError as error:
        raise MasterResumeError(str(error)) from error
    report = ImportReport()
    report.skipped(None, f"lines 1-{first_line}: the template's preamble and its comments")
    profile, facts = _Import(walk(events, report), report).run()
    return ImportResult(profile, facts, report)


_PHONE = re.compile(r"\+?[\d\s().-]{7,}")
_NETWORKS = {
    "linkedin.com": "LinkedIn",
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "x.com": "X",
    "twitter.com": "Twitter",
    "medium.com": "Medium",
    "kaggle.com": "Kaggle",
    "stackoverflow.com": "Stack Overflow",
}
_SUMMARY_SECTION = re.compile(r"\\section\*?\{\s*summary\s*\}", re.IGNORECASE)
_SUMMARY_LABEL = re.compile(r"(?P<label>[A-Z0-9][A-Z0-9 /&+().-]*?)\s*:\s*(?P<text>.*)")
_SUMMARY_WRAPPER = re.compile(r"(?:\\small\s*)?[{}]")
_QUESTION_ID = re.compile(r"(q_[a-z0-9_]+)\s+(.+)")
_QUESTION_LABEL = re.compile(r"([A-Z][A-Z0-9 &/-]*?[A-Z0-9])\s{2,}(.+)")
_SKILL_TAGS = frozenset({"TIERS", "GAPS", "VERIFY"})
_MAPPED_TAGS = frozenset({"PLANNED", "PRESETS", "QUESTIONS", *_SKILL_TAGS})
"""Tags that loose comments may carry; loose comments with other tags aren't imported."""


class _Import:
    def __init__(self, walked: Walk, report: ImportReport) -> None:
        self.walked = walked
        self.report = report
        self.ctx = ImportContext(report)
        self.summary_lines: list[CommentLine | None] = []
        self.loose: list[tuple[str, Segment]] = []
        for where, lines in walked.loose:
            start = next(
                (i for i, line in enumerate(lines) if line and _SUMMARY_SECTION.search(line.text)),
                None,
            )
            if start is not None:
                self.summary_lines.extend(lines[start + 1 :])
                lines = lines[:start]
            self.loose.extend((where, segment) for segment in segments(lines))

    def run(self) -> tuple[Profile, tuple[Fact, ...]]:
        self._reserve_ids()
        loose = [segment for _, segment in self.loose]
        summaries = self._summary_rows()
        presets = preset_rows(loose, self.report)
        labels = [
            *((line, label) for line, label, _ in summaries),
            *((line, label) for line, label, _ in presets),
            *subset_labels(self.walked.items),
        ]
        for line, label in sorted(labels):
            self.ctx.role_type(label, line)

        basics = self._basics()
        skill_comments = [*self.walked.skill_comments, *(s for s in loose if s.tag in _SKILL_TAGS)]
        skills, gaps = read_skills(self.walked.skill_lines, skill_comments, self.ctx)
        self._question_block()

        items = ItemReader(self.ctx)
        work: list[Role] = []
        education: list[Education] = []
        projects: list[Project] = []
        for draft in self.walked.items:
            if draft.section == "work" and (role := items.role(draft)):
                work.append(role)
            elif draft.section == "education" and (entry := items.education(draft)):
                education.append(entry)
            elif draft.section == "projects" and (project := items.project(draft)):
                projects.append(project)
        for segment in loose:
            if segment.tag == "PLANNED" and (planned := items.planned_project(segment)):
                projects.append(planned)
        work = [items.mark_during_education(role, education) for role in work]
        self._needed_questions()

        for where, segment in self.loose:
            if segment.tag not in _MAPPED_TAGS:
                self.ctx.skip(segment, where)
        if self.ctx.unknown_commands:
            names = ", ".join(sorted(f"\\{name}" for name in self.ctx.unknown_commands))
            self.report.skipped(None, f"LaTeX commands dropped from the text: {names}")

        try:
            profile = Profile(
                basics=basics,
                role_types=tuple(RoleType(id=rt.id, name=rt.name) for rt in self.ctx.role_types),
                work=tuple(work),
                education=tuple(education),
                projects=tuple(projects),
                skills=tuple(skills),
                skill_gaps=tuple(gaps),
                skill_presets=tuple(
                    SkillPreset(role_type=self.ctx.role_type(label, line), lines=tuple(lines))
                    for line, label, lines in presets
                ),
                summaries=tuple(
                    Summary(role_type=self.ctx.role_type(label, line), text=text)
                    for line, label, text in summaries
                ),
                open_questions=tuple(self.ctx.questions.values()),
            )
        except ValidationError as error:
            raise MasterResumeError(f"the imported profile isn't valid: {error}") from error
        self.report.summary = _summary(profile, self.ctx.facts)
        return profile, tuple(self.ctx.facts)

    def _reserve_ids(self) -> None:
        """Set aside the IDs written in the file, so that generated IDs never take them."""
        for draft in self.walked.items:
            found = [*draft.comments, *draft.trailing]
            found.extend(segment for bullet in draft.bullets for segment in bullet.comments)
            for segment in found:
                if segment.tag == "ID":
                    self.ctx.reserved_ids.add(id_fields(segment).get("ID", ""))
                elif segment.tag == "BENCHED":
                    self.ctx.reserved_ids.update(entry.id for entry in benched_entries(segment))
        for _, segment in self.loose:
            if segment.tag == "QUESTIONS":
                self.ctx.reserved_ids.update(qid for _, qid, _ in _question_rows(segment))

    def _basics(self) -> Basics:
        """The name, phone, email and links between `\\begin{center}` and `\\end{center}`."""
        name: str | None = None
        email: str | None = None
        phone: str | None = None
        url: str | None = None
        profiles: list[SocialProfile] = []
        for event in self.walked.heading:
            if isinstance(event, Command) and event.name == "textbf" and not name and event.args:
                name = self.ctx.plain(event.args[0])
            elif isinstance(event, Command) and event.name == "href" and len(event.args) == 2:
                target = event.args[0].strip()
                network = _NETWORKS.get(host(target))
                if target.startswith("mailto:"):
                    email = target.removeprefix("mailto:")
                elif network:
                    username = target.rstrip("/").rsplit("/", 1)[-1] or None
                    profiles.append(SocialProfile(network=network, username=username, url=target))
                elif url is None:
                    url = target
                else:
                    self.report.skipped(event.line, f"heading link {target}")
            elif isinstance(event, Text):
                for part in event.text.split("$|$"):
                    plain = self.ctx.plain(part).strip(" |")
                    if plain and phone is None and _PHONE.fullmatch(plain):
                        phone = plain
                    elif plain:
                        self.report.skipped(event.line, f"heading text {quote(plain)}")
            elif isinstance(event, Comment):
                text = quote(unescape_comment(event.text))
                self.report.skipped(event.line, f"comment in the heading: {text}")
        if not name:
            raise MasterResumeError("the heading has no name (\\textbf{...} in \\begin{center})")
        return Basics(name=name, email=email, phone=phone, url=url, profiles=tuple(profiles))

    def _summary_rows(self) -> list[tuple[int, str, str]]:
        """Summary variants from the commented-out summary section: `LABEL:` then its text."""
        rows: list[tuple[int, str, list[str]]] = []
        current: list[str] | None = None
        for line in self.summary_lines:
            if line is None:
                current = None
            elif _SUMMARY_WRAPPER.fullmatch(line.text):
                continue
            elif (match := _SUMMARY_LABEL.fullmatch(line.text)) and not any(
                char.islower() for char in match["label"]
            ):
                current = [match["text"]] if match["text"] else []
                rows.append((line.line, match["label"], current))
            elif current is not None:
                current.append(line.text)
            else:
                text = quote(unescape_comment(line.text))
                self.report.skipped(line.line, f"summary text without a label: {text}")
        return [
            (line, label, unescape_comment(" ".join(text))) for line, label, text in rows if text
        ]

    def _question_block(self) -> None:
        """The `OPEN QUESTIONS` list: `q_id  question` or `LABEL  question` per line."""
        for _, segment in self.loose:
            if segment.tag != "QUESTIONS":
                continue
            rows = _question_rows(segment)
            for first, question_id, question in rows:
                if self.ctx.claim_id(question_id, first.line, "an open question"):
                    self.ctx.questions[question_id] = OpenQuestion(
                        id=question_id, question=question
                    )
            read = {first.line for first, _, _ in rows}
            for first, entry in hanging_entries(segment.lines[1:]):
                if first.line not in read:
                    self.report.skipped(
                        first.line,
                        f"open question {quote(entry)}: expected `q_id  question` or "
                        "`LABEL  question`",
                    )

    def _needed_questions(self) -> None:
        """Create the questions that a `NEEDS:` names but the open-questions list doesn't."""
        for need in self.ctx.needs:
            if need.question_id in self.ctx.questions:
                continue
            after = need.comment.split(need.question_id, 1)[1]
            question = unescape_comment(re.sub(r"^[\s.,;:-]+", "", after))
            if self.ctx.claim_id(need.question_id, need.line, "a NEEDS question"):
                self.ctx.questions[need.question_id] = OpenQuestion(
                    id=need.question_id,
                    question=question or f"What detail would strengthen {need.bullet_id}?",
                )
                self.report.check(
                    need.line,
                    f"{need.question_id} isn't in the open-questions list; "
                    f"created it from the NEEDS comment of {need.bullet_id}",
                )


def _question_rows(segment: Segment) -> list[tuple[CommentLine, str, str]]:
    rows: list[tuple[CommentLine, str, str]] = []
    for first, entry in hanging_entries(segment.lines[1:]):
        if match := _QUESTION_ID.fullmatch(entry):
            rows.append((first, match[1], unescape_comment(match[2])))
        elif match := _QUESTION_LABEL.fullmatch(entry):
            question = f"{display_label(match[1])}: {unescape_comment(match[2])}"
            rows.append((first, f"q_{slug(match[1])}", question))
    return rows


def _summary(profile: Profile, facts: Sequence[Fact]) -> str:
    bullets = Counter(bullet.status for bullet in profile.all_bullets())
    planned = sum(1 for project in profile.projects if project.status == "planned")
    return (
        f"Imported {_count(len(profile.work), 'role')}, "
        f"{_count(len(profile.education), 'education entry', 'education entries')} and "
        f"{_count(len(profile.projects), 'project')} ({planned} planned), with "
        f"{bullets['active']} active, {bullets['benched']} benched and {bullets['planned']} "
        f"planned bullets; {_count(len(profile.skills), 'skill')}, "
        f"{_count(len(profile.skill_gaps), 'skill gap')}, "
        f"{_count(len(profile.skill_presets), 'skills preset')}, "
        f"{_count(len(profile.summaries), 'summary', 'summaries')} and "
        f"{_count(len(profile.open_questions), 'open question')}.\n"
        f"Created {_count(len(facts), 'fact')} with "
        f"{_count(sum(len(fact.metrics) for fact in facts), 'metric')}."
    )


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"
