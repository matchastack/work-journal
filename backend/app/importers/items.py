"""Roles, education entries and projects, with their bullets and a fact for each active bullet.

Structured comments above a heading or bullet set its metadata (see `app.importers.tags`); other
comments become its notes. Each active bullet gets a fact whose metrics the number checker reads
from the bullet (FR-IMP-4), so later rewording can be verified against it.
"""

import re
from collections.abc import Sequence

from pydantic import ValidationError

from app.importers.context import ImportContext, Need
from app.importers.latex import Command, read_group, unescape_comment
from app.importers.tags import (
    BenchedEntry,
    CommentLine,
    Segment,
    benched_entries,
    hanging_entries,
    id_fields,
    needed_questions,
    swap_entries,
)
from app.importers.values import (
    append_phrase,
    dates_and_place,
    degree_parts,
    drop_phrase,
    note_of,
    parse_dates,
    parse_strength,
    parse_verification,
    quote,
    rule_text,
    snippet,
    split_list,
    strength_exception,
    title_parts,
)
from app.importers.walk import BulletDraft, ItemDraft
from app.schema.common import Note
from app.schema.fact import Fact
from app.schema.profile import (
    Bullet,
    CourseworkSubset,
    Education,
    OpenQuestion,
    Project,
    Role,
    Strength,
    StrengthOverride,
    Swap,
    Verification,
)
from app.validate.numbers import check_numbers
from app.validate.quantities import extract_metrics

_COURSEWORK = re.compile(r"(?:relevant\s+)?coursework\s*:\s*(.+)", re.IGNORECASE)
_ABOVE = re.compile(r"the\s+(\w+)\s+above\s*(?:\+|plus|and)?\s*(.*)", re.IGNORECASE)
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_PLANNED_HEADING = re.compile(r"(?P<name>[A-Z0-9][^()]*?)\s*\((?P<stack>[^()]+)\)")
_PHASE = re.compile(r"(?:phase|step|stage|milestone|part)\s+\d+\s*:", re.IGNORECASE)
_BLOCK_TAGS = frozenset({"SUBSETS", "TIERS", "GAPS", "PRESETS", "QUESTIONS", "PLANNED", "BENCHED"})
"""Tags that start a block; above a heading or bullet they're out of place."""


def subset_labels(items: Sequence[ItemDraft]) -> list[tuple[int, str]]:
    """The role-type labels of every coursework subset, so role types can be set up first."""
    labels: list[tuple[int, str]] = []
    for draft in items:
        if draft.section != "education":
            continue
        for segment in [*draft.comments, *draft.trailing]:
            if segment.tag == "SUBSETS":
                for first, entry in hanging_entries(segment.lines[1:]):
                    label, colon, _ = unescape_comment(entry).partition(":")
                    if colon:
                        labels.append((first.line, label.strip()))
    return labels


class ItemReader:
    def __init__(self, ctx: ImportContext) -> None:
        self.ctx = ctx
        self.report = ctx.report
        self.heading_lines: dict[str, int] = {}
        """The heading's line for each role, for reporting."""

    def role(self, draft: ItemDraft) -> Role | None:
        """A work heading: `\\resumeSubheadingOneLine{Title, Company}{dates}` or Jake's 4-part.

        The official title comes from a `TITLE:` comment when it gives one; the heading's title
        and the comment's approved variants become the title variants.
        """
        command = draft.command
        args = [self.ctx.plain(arg) for arg in command.args]
        location: str | None = None
        if command.name == "resumeSubheadingOneLine" and len(args) == 2:
            title, _, company = args[0].partition(", ")
            dates_text = args[1]
        elif command.name == "resumeSubheading" and len(args) == 4:
            title, company = args[0], args[2]
            dates_text, location = dates_and_place(args[1], args[3])
        else:
            self._unreadable(command, "a work heading")
            return None
        what = f"{title}, {company}"
        dates = parse_dates(dates_text)
        if not title or not company or dates is None or dates[0] is None:
            self.report.problem(
                command.line,
                f"{quote(what)} needs a title, an organisation and a start date "
                f"(dates: {quote(dates_text)})",
            )
            return None

        explicit: str | None = None
        load_bearing = sensitive = False
        official: str | None = None
        approved: list[str] = []
        keep: list[str] = []
        cut: list[str] = []
        honesty: list[str] = []
        notes: list[Note] = []
        benched: list[BenchedEntry] = []
        questions: list[Segment] = []
        for segment in [*draft.comments, *draft.trailing]:
            match segment.tag:
                case "ID":
                    explicit = id_fields(segment).get("ID")
                case "LOAD_BEARING":
                    load_bearing = True
                case "SENSITIVE":
                    sensitive = True
                    notes.append(note_of(segment))
                case "TITLE":
                    official, approved, note = title_parts(segment)
                    notes.append(note)
                case "KEEP" | "CUT":
                    ids = self.ctx.keep_or_cut(segment, what)
                    (keep if segment.tag == "KEEP" else cut).extend(ids)
                    notes.append(note_of(segment))
                case "HONESTY":
                    honesty.append(unescape_comment(segment.value or segment.text))
                case "BENCHED":
                    benched.extend(benched_entries(segment))
                case "OPEN_QUESTION":
                    questions.append(segment)
                case tag if tag in _BLOCK_TAGS:
                    self.ctx.skip(segment, f"on {what}")
                case _:
                    notes.append(note_of(segment))
        role_id = self.ctx.claim_id(explicit, command.line, what) or self.ctx.generate_id(
            company, command.line, f"the role {what}"
        )
        self.heading_lines[role_id] = command.line
        highlights = self._bullets(draft.bullets, role_id, "work", honesty)
        highlights += self._benched(benched, role_id)
        self._questions(questions, role_id)
        position = official or title
        variants = [
            v for v in dict.fromkeys([title, *approved]) if v.casefold() != position.casefold()
        ]
        if official or approved:
            self.report.check(
                command.line,
                f"{what}: official title {quote(position)}, "
                f"approved variants: {', '.join(variants) or 'none'}",
            )
        try:
            return Role(
                id=role_id,
                name=company,
                position=position,
                title_variants=tuple(variants),
                location=location,
                start_date=dates[0],
                end_date=dates[1],
                highlights=tuple(highlights),
                load_bearing=load_bearing,
                keep_for=tuple(dict.fromkeys(keep)),
                cut_for=tuple(dict.fromkeys(cut)),
                sensitivity="sensitive" if sensitive else "normal",
                honesty_boundaries=tuple(honesty),
                notes=tuple(notes),
            )
        except ValidationError as error:
            self.report.problem(command.line, f"{what}: {_first_error(error)}")
            return None

    def education(self, draft: ItemDraft) -> Education | None:
        """`\\resumeSubheading{Institution}{dates or place}{Degree, Honours}{place or dates}`."""
        command = draft.command
        args = [self.ctx.plain(arg) for arg in command.args]
        if command.name != "resumeSubheading" or len(args) != 4:
            self._unreadable(command, "an education heading")
            return None
        institution, degree = args[0], args[2]
        dates_text, location = dates_and_place(args[1], args[3])
        dates = parse_dates(dates_text)
        if not institution or not degree or dates is None or dates[0] is None:
            self.report.problem(
                command.line,
                f"{quote(institution)} needs an institution, a degree and dates "
                f"(dates: {quote(dates_text)})",
            )
            return None
        parts = degree_parts(degree)
        if parts is None:
            self.report.check(
                command.line,
                f"{quote(degree)}: no ` in ` separates the degree from its subject, "
                "so both are the whole text",
            )
            parts = degree, degree, None

        explicit: str | None = None
        rules: list[str] = []
        subsets: list[Segment] = []
        questions: list[Segment] = []
        for segment in [*draft.comments, *draft.trailing]:
            match segment.tag:
                case "ID":
                    explicit = id_fields(segment).get("ID")
                case "RULE":
                    rules.append(rule_text(segment))
                case "SUBSETS":
                    subsets.append(segment)
                case "OPEN_QUESTION":
                    questions.append(segment)
                case _:
                    self.ctx.skip(segment, f"on {institution} (education entries have no notes)")
        education_id = self.ctx.claim_id(explicit, command.line, institution) or (
            self.ctx.generate_id(institution, command.line, f"the education entry {institution}")
        )

        courses: list[str] = []
        highlights: list[BulletDraft] = []
        for bullet in draft.bullets:
            plain = self.ctx.plain(bullet.command.args[0]) if bullet.command.args else ""
            coursework = _COURSEWORK.fullmatch(plain)
            if coursework is None:
                highlights.append(bullet)
                continue
            courses.extend(split_list(coursework[1]))
            for segment in bullet.comments:
                if segment.tag in _BLOCK_TAGS:
                    self.ctx.skip(segment, f"above the coursework of {institution}")
                else:
                    rules.append(rule_text(segment))
        bullets = self._bullets(highlights, education_id, "education", None)
        self._questions(questions, education_id)
        chosen = [subset for segment in subsets for subset in self._subsets(segment, courses)]
        try:
            return Education(
                id=education_id,
                institution=institution,
                study_type=parts[0],
                area=parts[1],
                honours=parts[2],
                location=location,
                start_date=dates[0],
                end_date=dates[1],
                courses=tuple(courses),
                coursework_subsets=tuple(chosen),
                highlights=tuple(bullets),
                rules=tuple(rules),
            )
        except ValidationError as error:
            self.report.problem(command.line, f"{institution}: {_first_error(error)}")
            return None

    def project(self, draft: ItemDraft) -> Project | None:
        """`\\resumeProjectHeading{\\textbf{Name} \\emph{(stack)}}{dates}`."""
        command = draft.command
        if command.name != "resumeProjectHeading" or not command.args:
            self._unreadable(command, "a project heading")
            return None
        heading = command.args[0]
        name = self.ctx.plain(_group_after(heading, "textbf") or heading.split("$|$")[0])
        stack = _group_after(heading, "emph") or (heading.split("$|$", 1)[1:] or [""])[0]
        dates_text = self.ctx.plain(command.args[1]) if len(command.args) > 1 else ""
        dates = parse_dates(dates_text)
        if not name:
            self.report.problem(command.line, "a project heading without a name")
            return None
        if dates is None:
            self.report.problem(
                command.line, f"{name}: can't read the dates {quote(dates_text)}; left undated"
            )
            dates = None, None

        explicit: str | None = None
        keep: list[str] = []
        cut: list[str] = []
        notes: list[Note] = []
        benched: list[BenchedEntry] = []
        questions: list[Segment] = []
        for segment in [*draft.comments, *draft.trailing]:
            match segment.tag:
                case "ID":
                    explicit = id_fields(segment).get("ID")
                case "KEEP" | "CUT":
                    ids = self.ctx.keep_or_cut(segment, name)
                    (keep if segment.tag == "KEEP" else cut).extend(ids)
                    notes.append(note_of(segment))
                case "BENCHED":
                    benched.extend(benched_entries(segment))
                case "OPEN_QUESTION":
                    questions.append(segment)
                case tag if tag in _BLOCK_TAGS:
                    self.ctx.skip(segment, f"on {name}")
                case _:
                    notes.append(note_of(segment))
        project_id = self.ctx.claim_id(explicit, command.line, name) or self.ctx.generate_id(
            name, command.line, f"the project {name}"
        )
        highlights = self._bullets(draft.bullets, project_id, "projects", None)
        highlights += self._benched(benched, project_id)
        self._questions(questions, project_id)
        try:
            return Project(
                id=project_id,
                name=name,
                keywords=tuple(split_list(self.ctx.plain(stack).strip("() "))),
                url=_group_after(heading, "href"),
                start_date=dates[0],
                end_date=dates[1],
                highlights=tuple(highlights),
                keep_for=tuple(dict.fromkeys(keep)),
                cut_for=tuple(dict.fromkeys(cut)),
                notes=tuple(notes),
            )
        except ValidationError as error:
            self.report.problem(command.line, f"{name}: {_first_error(error)}")
            return None

    def planned_project(self, segment: Segment) -> Project | None:
        """A `NOT YET BUILT` block: a `Name (stack)` line, then `Phase N:` bullets and notes."""
        lines = segment.lines
        index = next(
            (i for i, line in enumerate(lines) if i and _PLANNED_HEADING.fullmatch(line.text)), None
        )
        if index is None:
            self.ctx.skip(segment, "in a planned block without a `Name (stack)` line")
            return None
        heading = _PLANNED_HEADING.fullmatch(lines[index].text)
        assert heading is not None
        name = unescape_comment(heading["name"])
        project_id = self.ctx.generate_id(name, lines[index].line, f"the planned project {name}")
        phases: list[tuple[CommentLine, list[str]]] = []
        notes: list[list[str]] = []
        in_phase = False
        for line in lines[index + 1 :]:
            if _PHASE.match(line.text):
                phases.append((line, [line.text]))
                in_phase = True
            elif in_phase and line.indent > phases[-1][0].indent:
                phases[-1][1].append(line.text)
            elif not in_phase and notes:
                notes[-1].append(line.text)
            else:
                notes.append([line.text])
                in_phase = False
        highlights = [
            Bullet(
                id=self.ctx.generate_id(
                    f"{project_id}_{number}", first.line, f"a bullet of {name}"
                ),
                text=unescape_comment(" ".join(texts)),
                status="planned",
            )
            for number, (first, texts) in enumerate(phases, 1)
        ]
        return Project(
            id=project_id,
            name=name,
            keywords=tuple(split_list(unescape_comment(heading["stack"]))),
            status="planned",
            status_reason=unescape_comment(" ".join(line.text for line in lines[:index])) or None,
            highlights=tuple(highlights),
            notes=tuple(Note(text=unescape_comment(" ".join(texts))) for texts in notes),
        )

    def mark_during_education(self, role: Role, education: Sequence[Education]) -> Role:
        """A role held within a degree's dates can be cut without leaving a gap."""
        for entry in education:
            if (
                role.end_date is not None
                and entry.end_date is not None
                and entry.start_date <= role.start_date
                and role.end_date <= entry.end_date
            ):
                self.report.check(
                    self.heading_lines.get(role.id),
                    f"{role.position}, {role.name}: held during {entry.institution}, "
                    "so it can be cut without leaving a gap",
                )
                return role.model_copy(update={"during_education": True})
        return role

    # --- Bullets -----------------------------------------------------------------------------

    def _bullets(
        self,
        drafts: Sequence[BulletDraft],
        parent_id: str,
        section: str,
        honesty: list[str] | None,
    ) -> list[Bullet]:
        bullets: list[Bullet] = []
        for draft in drafts:
            if bullet := self._bullet(draft, parent_id, len(bullets) + 1, section, honesty):
                bullets.append(bullet)
        return bullets

    def _bullet(
        self,
        draft: BulletDraft,
        parent_id: str,
        number: int,
        section: str,
        honesty: list[str] | None,
    ) -> Bullet | None:
        """A `\\resumeItem`, with its `ID | STRENGTH | VERIFIED` line, swaps, needs and notes.

        A `HONESTY BOUNDARY:` above a work bullet goes to its role's honesty boundaries.
        """
        command = draft.command
        text = self.ctx.plain(command.args[0]) if command.args else ""
        if not text:
            self.report.problem(command.line, "an empty \\resumeItem")
            return None
        explicit: str | None = None
        strength: Strength = "medium"
        overrides: list[StrengthOverride] = []
        verification: Verification = "no"
        verification_note: str | None = None
        swaps: list[Swap] = []
        notes: list[Note] = []
        needs: list[tuple[str, Segment]] = []
        questions: list[Segment] = []
        for segment in draft.comments:
            needs.extend((question_id, segment) for question_id in needed_questions(segment.text))
            match segment.tag:
                case "ID":
                    fields = id_fields(segment)
                    explicit = fields.pop("ID", None)
                    if "STRENGTH" in fields:
                        strength, overrides = self._strength(fields.pop("STRENGTH"), segment, notes)
                    if "VERIFIED" in fields:
                        verification, verification_note = self._verification(
                            fields.pop("VERIFIED"), segment.line
                        )
                    notes.extend(
                        Note(text=unescape_comment(f"{k}: {v}")) for k, v in fields.items()
                    )
                case "SWAPS":
                    swaps.extend(self._swaps(segment, text, notes))
                case "HONESTY" if honesty is not None:
                    honesty.append(unescape_comment(segment.value or segment.text))
                case "OPEN_QUESTION":
                    questions.append(segment)
                case tag if tag in _BLOCK_TAGS:
                    self.ctx.skip(segment, "above a bullet")
                case _:
                    notes.append(note_of(segment))
        what = f"the bullet {quote(snippet(text, 40))}"
        bullet_id = self.ctx.claim_id(explicit, command.line, what) or self.ctx.generate_id(
            f"{parent_id}_{number}", command.line, what
        )
        self.ctx.needs.extend(Need(s.line, q, bullet_id, s.text) for q, s in needs)
        self._questions(questions, bullet_id)
        fact_id = f"fact_{bullet_id}"[:64]
        self._fact(fact_id, text, section, parent_id)
        try:
            return Bullet(
                id=bullet_id,
                text=text,
                strength=strength,
                strength_overrides=tuple(overrides),
                verification=verification,
                verification_note=verification_note,
                swaps=tuple(swaps),
                needs=tuple(dict.fromkeys(question_id for question_id, _ in needs)),
                notes=tuple(notes),
                sources=(fact_id,),
            )
        except ValidationError as error:
            self.report.problem(command.line, f"{what}: {_first_error(error)}")
            return None

    def _benched(self, entries: Sequence[BenchedEntry], parent_id: str) -> list[Bullet]:
        bullets: list[Bullet] = []
        for entry in entries:
            text = unescape_comment(entry.text)
            if not text:
                self.report.problem(entry.line, f"the benched bullet {entry.id} has no text")
                continue
            what = f"the benched bullet {quote(snippet(text, 40))}"
            reason = unescape_comment(entry.reason or "")
            if not reason:
                self.report.problem(entry.line, f"{what} has no REASON")
                reason = "No reason recorded in the master resume."
            bullet_id = self.ctx.claim_id(entry.id, entry.line, what) or self.ctx.generate_id(
                f"{parent_id}_benched", entry.line, what
            )
            bullets.append(Bullet(id=bullet_id, text=text, status="benched", status_reason=reason))
        return bullets

    def _fact(self, fact_id: str, text: str, section: str, parent_id: str) -> None:
        """The bullet as a fact, with its metrics, for verifying future rewording (FR-IMP-4)."""
        metrics = extract_metrics(text, self.ctx.allowed_terms)
        check = check_numbers(text, metrics, self.ctx.allowed_terms)
        if check.findings:
            codes = ", ".join(finding.code for finding in check.findings)
            self.report.problem(
                None, f"{fact_id}: the number check disagrees with the bullet's metrics ({codes})"
            )
        self.ctx.facts.append(
            Fact(
                id=fact_id,
                statement=text,
                kind="learning" if section == "education" else "accomplishment",
                metrics=metrics,
                role_id=parent_id if section == "work" else None,
                project_id=parent_id if section == "projects" else None,
                origin="import",
            )
        )

    def _strength(
        self, value: str, segment: Segment, notes: list[Note]
    ) -> tuple[Strength, list[StrengthOverride]]:
        """`low (HIGH for data roles)` -> low, and high for the `data` role type."""
        parsed = parse_strength(value)
        if parsed is None:
            self.report.problem(segment.line, f"strength {quote(value)} isn't high, medium or low")
            notes.append(Note(text=unescape_comment(f"STRENGTH: {value}")))
            return "medium", []
        strength, rest = parsed
        if not rest:
            return strength, []
        exception = strength_exception(rest)
        role_types = self.ctx.match_role_types(exception[1]) if exception else []
        if exception is None or not role_types:
            self.report.check(
                segment.line,
                f"strength {quote(value)}: the exception names no role type, so it's a note",
            )
            notes.append(Note(text=unescape_comment(f"STRENGTH: {value}")))
            return strength, []
        self.report.check(
            segment.line, f"strength {quote(value)}: {exception[0]} for {', '.join(role_types)}"
        )
        return strength, [
            StrengthOverride(role_type=rt, strength=exception[0]) for rt in role_types
        ]

    def _verification(self, value: str, line: int) -> tuple[Verification, str | None]:
        parsed = parse_verification(value)
        if parsed is None:
            self.report.problem(line, f"verified {quote(value)} isn't yes, partial or no")
            return "no", unescape_comment(value)
        return parsed

    def _swaps(self, segment: Segment, text: str, notes: list[Note]) -> list[Swap]:
        """Approved phrasings. `drop "x"` and `append "x"` become the whole reworded bullet."""
        entries, unparsed = swap_entries(segment)
        swaps: list[Swap] = []
        for entry in entries:
            phrase = unescape_comment(entry.phrase)
            context = unescape_comment(entry.context)
            contexts = (context,) if context else ()
            if not phrase:
                unparsed.append(entry.raw)
            elif entry.verb is None:
                swaps.append(Swap(text=phrase, contexts=contexts))
            elif entry.verb in ("drop", "append"):
                reworded = (
                    drop_phrase(text, phrase)
                    if entry.verb == "drop"
                    else append_phrase(text, phrase)
                )
                if reworded is None:
                    self.report.problem(
                        segment.line,
                        f"swap {quote(entry.raw)}: the phrase isn't in the bullet, so it's a note",
                    )
                    notes.append(Note(text=unescape_comment(f"SWAPS: {entry.raw}")))
                    continue
                self.report.check(segment.line, f"swap by {entry.verb}: {quote(reworded)}")
                swaps.append(Swap(text=reworded, contexts=contexts))
            else:
                swaps.append(Swap(text=phrase, contexts=(unescape_comment(entry.raw),)))
        for raw in unparsed:
            self.report.check(segment.line, f"swap {quote(raw)} has no quoted phrase; it's a note")
            notes.append(Note(text=unescape_comment(f"SWAPS: {raw}")))
        return swaps

    # --- Coursework and questions ------------------------------------------------------------

    def _subsets(self, segment: Segment, courses: Sequence[str]) -> list[CourseworkSubset]:
        """`Label : course, course` lines; `the three above + X` extends the subset above."""
        known = {course.casefold(): course for course in courses}
        previous: list[str] = []
        subsets: list[CourseworkSubset] = []
        for first, entry in hanging_entries(segment.lines[1:]):
            label, colon, names_text = unescape_comment(entry).partition(":")
            if not colon:
                self.report.skipped(first.line, f"coursework subset {quote(entry)}")
                continue
            names = split_list(names_text)
            if above := _ABOVE.fullmatch(names_text.strip()):
                word = above[1].casefold()
                count = _COUNTS.get(word) or (int(word) if word.isdigit() else None)
                if count != len(previous):
                    self.report.problem(
                        first.line,
                        f"{quote(label.strip())} says 'the {above[1]} above' but the subset "
                        f"above has {len(previous)} courses",
                    )
                names = [*previous, *split_list(above[2])]
            chosen: list[str] = []
            for name in names:
                if course := known.get(name.casefold()):
                    chosen.append(course)
                else:
                    self.report.problem(
                        first.line,
                        f"the coursework subset {quote(label.strip())} lists {name}, "
                        "which isn't in the coursework line",
                    )
            role_type = self.ctx.role_type(label, first.line)
            subsets.append(CourseworkSubset(role_type=role_type, courses=tuple(chosen)))
            previous = chosen
        return subsets

    def _questions(self, found: Sequence[Segment], owner_id: str) -> None:
        """`OPEN QUESTION:` comments on an item or bullet, with IDs made from the owner's ID."""
        for segment in found:
            question_id = self.ctx.generate_id(
                f"q_{owner_id}", segment.line, "an OPEN QUESTION comment"
            )
            question = unescape_comment(segment.value) or "(no text)"
            self.ctx.questions[question_id] = OpenQuestion(id=question_id, question=question)

    def _unreadable(self, command: Command, what: str) -> None:
        self.report.problem(
            command.line,
            f"\\{command.name} with {len(command.args)} arguments isn't {what} "
            "the importer can read",
        )


def _group_after(tex: str, command: str) -> str | None:
    """The first brace group after `\\command`, e.g. the name in `\\textbf{Name}`."""
    match = re.search(rf"\\{command}\s*\{{", tex)
    return read_group(tex, match.end() - 1)[0] if match else None


def _first_error(error: ValidationError) -> str:
    return str(error.errors()[0].get("msg", error))
