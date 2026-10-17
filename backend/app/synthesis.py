"""Profile synthesis: new facts become a change set for the owner to review (T-018).

A heavy-tier prompt reads the profile and the new facts and plans the changes, without writing
any text:
- new roles, such as one the owner named for a journal entry (FR-CAP-11), and new projects;
- benched or planned items that a fact shows now hold, and roles that a fact says ended;
- skills the facts name that the catalogue lacks;
- new bullets, and new versions of existing ones, each with the facts it rests on.

Code checks the plan before it becomes operations (`app/schema/changes.py`):
- Every operation cites new facts, and only those (FR-REV-1); a plan item without one is left out.
- Nothing is invented (R1): dates are dates of the facts cited, a new role's organisation and
  title and a new project's name are the owner's words, and project keywords and new skills are
  tools the facts name. Nothing on the gaps list is added (R3).
- A benched or planned item becomes active only with a fact, and a benched or planned bullet
  gets a new version only when the plan makes it active again.
- Each fact goes into one bullet at most, and a fact the owner filed under a role goes into a
  bullet under that role, or under a project.
A fact that answers an open question resolves it (FR-EXT-4): code adds that operation.

The writer (`app/writing.py`) writes each planned bullet and the verifier checks it, so every
text operation carries its verifier report (FR-FID-8), and a bullet that fails is proposed
flagged (FR-FID-7). The client is synchronous, so bullets are written up to `concurrency` at a
time in threads, which lets a refresh of months of facts finish within one function call
(T-064). The first is written alone, so that the profile context its calls cache serves the
rest. Each bullet's place, facts, ID and notes are settled from the plan before any is written,
so the change set doesn't depend on which bullet finishes first. The notes on what was left out
name facts by ID and never quote them.
"""

import json
import re
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.profile_ops import ProfileOpError, apply
from app.schema.changes import (
    AddBullet,
    AddProject,
    AddRole,
    AddSkill,
    ChangeOp,
    ChangeSet,
    EditBullet,
    ResolveOpenQuestion,
    ReviewedOp,
    SetBulletStatus,
    UpdateField,
)
from app.schema.common import Model, NonEmptyStr, YearMonth
from app.schema.fact import Fact
from app.schema.profile import Bullet, Profile, Project, Role, Skill, Strength
from app.schema.verification import VerifierReport
from app.writing import write_bullet

PROMPT_NAME = "profile_synthesis"
PROMPT_VERSION = 1

_PLANNING_FIELDS = {
    "id",
    "statement",
    "kind",
    "metrics",
    "ownership",
    "tools",
    "outcome",
    "date",
    "role_id",
    "project_id",
    "proposed_item",
    "proposed_role",
    "question_id",
}
"""What the planner sees of a fact: what happened, and what it links to."""


class PlannedRole(Model):
    key: NonEmptyStr
    """How the plan's bullets refer to the role."""
    organisation: NonEmptyStr
    title: NonEmptyStr
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    fact_ids: tuple[str, ...] = ()


class PlannedProject(Model):
    key: NonEmptyStr
    name: NonEmptyStr
    keywords: tuple[str, ...] = ()
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    fact_ids: tuple[str, ...] = ()


class PlannedReactivation(Model):
    item_id: NonEmptyStr
    """A benched or planned bullet, or a planned project."""
    fact_ids: tuple[str, ...] = ()


class PlannedEndDate(Model):
    role_id: NonEmptyStr
    end_date: YearMonth
    fact_ids: tuple[str, ...] = ()


class PlannedSkill(Model):
    name: NonEmptyStr
    category: NonEmptyStr
    fact_ids: tuple[str, ...] = ()


class PlannedBullet(Model):
    under: NonEmptyStr
    """The ID of a role, education entry or project, or the key of a planned role or project."""
    edit: str | None = None
    """The ID of the bullet this is a new version of, or None for a new bullet."""
    fact_ids: tuple[str, ...] = ()
    """The new facts it's written from; a new version also keeps its bullet's facts."""
    strength: Strength = "medium"
    tags: tuple[str, ...] = ()


class SynthesisPlan(Model):
    """The heavy-tier model's plan: what to change, and the facts each change rests on."""

    roles: tuple[PlannedRole, ...] = ()
    projects: tuple[PlannedProject, ...] = ()
    reactivations: tuple[PlannedReactivation, ...] = ()
    end_dates: tuple[PlannedEndDate, ...] = ()
    skills: tuple[PlannedSkill, ...] = ()
    bullets: tuple[PlannedBullet, ...] = ()


@dataclass(frozen=True)
class Synthesis:
    change_set: ChangeSet | None
    """The proposed changes, or None when there's nothing to propose."""
    unused: tuple[str, ...] = ()
    """The IDs of new facts that no change cites. They stay in the journal."""
    notes: tuple[str, ...] = ()
    """What code left out of the plan, and why."""


def synthesize(
    profile: Profile,
    facts: Sequence[Fact],
    client: LLMClient,
    *,
    sources: Sequence[Fact] = (),
    base_version: int | None = None,
    avoid: Sequence[str] = (),
    prompt: Prompt | None = None,
    concurrency: int = 1,
) -> Synthesis:
    """Propose changes to `profile` from `facts`, the new facts, with the heavy-tier model.

    `sources` are other facts, such as those existing bullets were written from: a new version
    of a bullet is checked against its own facts too. `base_version` is the number of the
    profile's version, and `avoid` the owner's words to avoid. `concurrency` is how many bullets
    are written and checked at once; 1 writes them one after another.
    """
    if concurrency < 1:
        raise ValueError("bullets are written at least one at a time")
    if not facts:
        return Synthesis(change_set=None)
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call(
        "profile_synthesis",
        prompt,
        SynthesisPlan,
        variables={"facts": planning_facts_json(facts)},
        context=synthesis_context(profile),
    )
    builder = _Builder(profile, facts, sources, client, avoid, concurrency)
    return builder.build(result.output, base_version)


def new_facts(
    profile: Profile, facts: Iterable[Fact], change_sets: Iterable[ChangeSet] = ()
) -> list[Fact]:
    """The journal facts that neither the profile nor any of `change_sets` cites yet."""
    cited = {source for bullet in profile.all_bullets() for source in bullet.sources}
    cited |= {answer for question in profile.open_questions for answer in question.answer_fact_ids}
    cited |= {
        fact_id
        for change_set in change_sets
        for reviewed in change_set.ops
        for fact_id in reviewed.op.fact_ids
    }
    return [fact for fact in facts if fact.origin == "journal" and fact.id not in cited]


def planning_facts_json(facts: Sequence[Fact]) -> str:
    data = [
        fact.model_dump(mode="json", include=_PLANNING_FIELDS, exclude_none=True) for fact in facts
    ]
    return json.dumps(data, indent=2, ensure_ascii=False)


def synthesis_context(profile: Profile) -> str:
    """The profile as the planner sees it, as JSON, without contact details."""
    data = {
        "roleTypes": [{"id": kind.id, "name": kind.name} for kind in profile.role_types],
        "roles": [
            {
                "id": role.id,
                "organisation": role.name,
                "title": role.position,
                "startDate": role.start_date,
                "endDate": role.end_date,
                **({"sensitive": True} if role.sensitivity == "sensitive" else {}),
                "bullets": _bullets(role.highlights),
            }
            for role in profile.work
        ],
        "education": [
            {
                "id": entry.id,
                "institution": entry.institution,
                "studyType": entry.study_type,
                "area": entry.area,
                "bullets": _bullets(entry.highlights),
            }
            for entry in profile.education
        ],
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "status": project.status,
                "keywords": list(project.keywords),
                "startDate": project.start_date,
                "endDate": project.end_date,
                "bullets": _bullets(project.highlights),
            }
            for project in profile.projects
        ],
        "skills": [{"name": skill.name, "category": skill.category} for skill in profile.skills],
        "gaps": list(profile.skill_gaps),
        "openQuestions": [
            {"id": question.id, "question": question.question, "status": question.status}
            for question in profile.open_questions
        ],
    }
    return "The person's profile:\n" + json.dumps(data, indent=2, ensure_ascii=False)


def _bullets(bullets: Sequence[Bullet]) -> list[dict[str, object]]:
    return [
        {
            "id": bullet.id,
            "text": bullet.text,
            "status": bullet.status,
            **({"statusReason": bullet.status_reason} if bullet.status_reason else {}),
            **({"needs": list(bullet.needs)} if bullet.needs else {}),
        }
        for bullet in bullets
    ]


@dataclass(frozen=True)
class _Draft:
    """A planned bullet that passed the checks, placed and ready to be written."""

    planned: PlannedBullet
    entry_id: str
    """The role, education entry or project it goes under."""
    fact_ids: tuple[str, ...]
    """The new facts it cites."""
    written_from: tuple[Fact, ...]
    """The facts it's written and checked from: a new version's include its bullet's own."""
    bullet_id: str
    """The new bullet's ID, or the ID of the bullet it's a new version of."""


@dataclass
class _Builder:
    """Turns a plan into checked operations, keeping notes on what it leaves out."""

    profile: Profile
    facts: Sequence[Fact]
    sources: Sequence[Fact]
    client: LLMClient
    avoid: Sequence[str]
    concurrency: int = 1
    """How many bullets are written and checked at once."""
    notes: list[str] = field(default_factory=list[str])
    keys: dict[str, str] = field(default_factory=dict[str, str])
    """The IDs given to the plan's new roles and projects, by their keys."""
    reactivated: set[str] = field(default_factory=set[str])
    skills: set[str] = field(default_factory=set[str])
    """The skills the plan adds, in lower case."""
    used: set[str] = field(default_factory=set[str])
    """The facts already in a bullet."""

    def __post_init__(self) -> None:
        self.new = {fact.id: fact for fact in self.facts}
        self.known = {fact.id: fact for fact in self.sources} | self.new
        self.bullets = {
            bullet.id: (item.id, bullet)
            for item in (*self.profile.work, *self.profile.education, *self.profile.projects)
            for bullet in item.highlights
        }
        self.taken = {
            *(item.id for item in (*self.profile.work, *self.profile.education)),
            *(project.id for project in self.profile.projects),
            *self.bullets,
            *(question.id for question in self.profile.open_questions),
        }

    def build(self, plan: SynthesisPlan, base_version: int | None) -> Synthesis:
        # Each new role or project is inserted before the first older one, oldest first, so the
        # newest of several ends up first, and a rejected one leaves the others' places valid.
        roles = sorted(plan.roles, key=lambda role: role.start_date or "")
        projects = sorted(plan.projects, key=lambda p: p.start_date or p.end_date or "")
        planned: list[ChangeOp | None] = [
            *(self._role(role) for role in roles),
            *(self._project(project) for project in projects),
            *(self._skill(skill) for skill in plan.skills),
            *(self._end_date(end) for end in plan.end_dates),
            *(self._reactivation(item) for item in plan.reactivations),
        ]
        interim = self.profile
        structure: list[ChangeOp] = []
        for op in planned:
            if op is None:
                continue
            try:
                interim = apply(interim, [op])
            except ProfileOpError as error:
                self.notes.append(f"Left out a change ({op.op}): {error}")
                continue
            structure.append(op)
        ops = [ReviewedOp(op=op) for op in structure]
        drafts = [d for bullet in plan.bullets if (d := self._draft(bullet, interim)) is not None]
        reports = _in_order(drafts, lambda draft: self._write(draft, interim), self.concurrency)
        ops += [self._reviewed(d, report) for d, report in zip(drafts, reports, strict=True)]
        ops += [ReviewedOp(op=op) for op in self._answers()]
        cited = {fact_id for reviewed in ops for fact_id in reviewed.op.fact_ids}
        unused = tuple(fact.id for fact in self.facts if fact.id not in cited)
        if not ops:
            return Synthesis(change_set=None, unused=unused, notes=tuple(self.notes))
        change_set = ChangeSet(
            author="ai",
            ops=tuple(ops),
            summary=_summary([reviewed.op for reviewed in ops], len(self.facts) - len(unused)),
            base_version=base_version,
        )
        return Synthesis(change_set=change_set, unused=unused, notes=tuple(self.notes))

    def _cited(self, fact_ids: Sequence[str], what: str) -> tuple[str, ...]:
        """The new facts among `fact_ids`. Others are noted and dropped."""
        kept = tuple(dict.fromkeys(fact_id for fact_id in fact_ids if fact_id in self.new))
        if others := [fact_id for fact_id in fact_ids if fact_id not in self.new]:
            self.notes.append(f"{_upper(what)} cited facts that aren't new: {', '.join(others)}")
        if not kept:
            self.notes.append(f"Left out {what}: it cites no new fact")
        return kept

    def _id(self, base: str, *, numbered: bool = False) -> str:
        """An ID made from `base` that nothing in the profile or the plan has yet: `base`,
        `base_2`, and so on, or with `numbered`, `base_1`, `base_2`, and so on."""
        stem = re.sub(r"[^a-z0-9]+", "_", base.casefold()).strip("_")[:56] or "item"
        number = 1
        candidate = f"{stem}_1" if numbered else stem
        while candidate in self.taken:
            number += 1
            candidate = f"{stem}_{number}"
        self.taken.add(candidate)
        return candidate

    def _role(self, planned: PlannedRole) -> AddRole | None:
        what = f'the new role "{planned.key}"'
        fact_ids = self._cited(planned.fact_ids, what)
        if not fact_ids:
            return None
        facts = [self.new[fact_id] for fact_id in fact_ids]
        words = [text for fact in facts for text in (fact.proposed_role, fact.statement) if text]
        for value, part in ((planned.organisation, "organisation"), (planned.title, "title")):
            if not _in_words(value, words):
                self.notes.append(f"Left out {what}: its {part} isn't in your words")
                return None
        dates = sorted({fact.date for fact in facts if fact.date})
        if not dates:
            self.notes.append(f"Left out {what}: none of its facts has a date to start it from")
            return None
        start = planned.start_date if planned.start_date in dates else dates[0]
        rationale = None
        if planned.start_date != start:
            rationale = f"Its start date is the month of its earliest fact, {start}; check it."
        end = planned.end_date if planned.end_date in dates else None
        if end is not None and end < start:
            end = None
        role = Role(
            id=self._id(planned.organisation),
            name=planned.organisation,
            position=planned.title,
            start_date=start,
            end_date=end,
        )
        self.keys[planned.key] = role.id
        position = _position([older.start_date for older in self.profile.work], start)
        return AddRole(role=role, position=position, fact_ids=fact_ids, rationale=rationale)

    def _project(self, planned: PlannedProject) -> AddProject | None:
        what = f'the new project "{planned.key}"'
        fact_ids = self._cited(planned.fact_ids, what)
        if not fact_ids:
            return None
        facts = [self.new[fact_id] for fact_id in fact_ids]
        words = [text for fact in facts for text in (fact.proposed_item, fact.statement) if text]
        if not _in_words(planned.name, words):
            self.notes.append(f"Left out {what}: its name isn't in your words")
            return None
        tools = {tool.casefold(): tool for fact in facts for tool in fact.tools}
        keywords = tuple(
            dict.fromkeys(
                tools[keyword.casefold()]
                for keyword in planned.keywords
                if keyword.casefold() in tools
            )
        )
        dates = {fact.date for fact in facts if fact.date}
        start = planned.start_date if planned.start_date in dates else None
        end = planned.end_date if planned.end_date in dates else None
        if start and end and end < start:
            end = None
        project = Project(
            id=self._id(planned.name),
            name=planned.name,
            keywords=keywords,
            start_date=start,
            end_date=end,
        )
        self.keys[planned.key] = project.id
        dates_shown = [older.start_date or older.end_date for older in self.profile.projects]
        position = _position(dates_shown, start or end)
        return AddProject(project=project, position=position, fact_ids=fact_ids)

    def _skill(self, planned: PlannedSkill) -> AddSkill | None:
        what = f'the skill "{planned.name}"'
        fact_ids = self._cited(planned.fact_ids, what)
        if not fact_ids:
            return None
        tools = {tool.casefold(): tool for fact_id in fact_ids for tool in self.new[fact_id].tools}
        name = tools.get(planned.name.casefold())
        if name is None:
            self.notes.append(f"Left out {what}: the facts it cites don't name it")
            return None
        if name.casefold() in {gap.casefold() for gap in self.profile.skill_gaps}:
            self.notes.append(f"Left out {what}: it's on your gaps list")
            return None
        known = {
            spelling.casefold()
            for skill in self.profile.skills
            for spelling in (skill.name, *skill.aliases)
        }
        if name.casefold() in known | self.skills:
            return None
        self.skills.add(name.casefold())
        return AddSkill(
            skill=Skill(name=name, category=planned.category, verify_before_shipping=True),
            fact_ids=fact_ids,
            rationale="It's new to your catalogue, so it's marked verify-before-shipping: "
            "confirm it before a tailored resume lists it.",
        )

    def _end_date(self, planned: PlannedEndDate) -> UpdateField | None:
        what = f'the end date of "{planned.role_id}"'
        fact_ids = self._cited(planned.fact_ids, what)
        if not fact_ids:
            return None
        role = next((role for role in self.profile.work if role.id == planned.role_id), None)
        if role is None:
            self.notes.append(f"Left out {what}: there's no such role")
            return None
        if planned.end_date not in {self.new[fact_id].date for fact_id in fact_ids}:
            self.notes.append(f"Left out {what}: it isn't the date of a fact it cites")
            return None
        if planned.end_date < role.start_date or planned.end_date == role.end_date:
            return None
        return UpdateField(
            target_id=role.id, field="endDate", value=planned.end_date, fact_ids=fact_ids
        )

    def _reactivation(self, planned: PlannedReactivation) -> ChangeOp | None:
        what = f'making "{planned.item_id}" active'
        fact_ids = self._cited(planned.fact_ids, what)
        if not fact_ids:
            return None
        if planned.item_id in self.bullets:
            _, bullet = self.bullets[planned.item_id]
            if bullet.status == "active":
                return None
            self.reactivated.add(bullet.id)
            return SetBulletStatus(bullet_id=bullet.id, status="active", fact_ids=fact_ids)
        project = next((p for p in self.profile.projects if p.id == planned.item_id), None)
        if project is None:
            self.notes.append(f"Left out {what}: there's no such bullet or project")
            return None
        if project.status == "active":
            return None
        return UpdateField(target_id=project.id, field="status", value="active", fact_ids=fact_ids)

    def _draft(self, planned: PlannedBullet, interim: Profile) -> _Draft | None:
        """Check a planned bullet and settle where it goes, its facts and a new bullet's ID, or
        note why it's left out."""
        what = f'the new version of "{planned.edit}"' if planned.edit else "a new bullet"
        what += f' under "{planned.under}"' if not planned.edit else ""
        fact_ids = self._cited(planned.fact_ids, what)
        if repeated := [fact_id for fact_id in fact_ids if fact_id in self.used]:
            self.notes.append(
                f"{_upper(what)} cited facts another bullet uses: {', '.join(repeated)}"
            )
            fact_ids = tuple(fact_id for fact_id in fact_ids if fact_id not in self.used)
            if not fact_ids:
                self.notes.append(f"Left out {what}: all its facts are in other bullets")
        if not fact_ids:
            return None
        entry_id = self.keys.get(planned.under, planned.under)
        own: list[Fact] = []
        if planned.edit is not None:
            if planned.edit not in self.bullets:
                self.notes.append(f"Left out {what}: there's no such bullet")
                return None
            entry_id, current = self.bullets[planned.edit]
            if current.status != "active" and current.id not in self.reactivated:
                self.notes.append(
                    f"Left out {what}: it's {current.status}, and the plan doesn't make it active"
                )
                return None
            own = [self.known[source] for source in current.sources if source in self.known]
            if missing := [source for source in current.sources if source not in self.known]:
                self.notes.append(f"{what}: its facts {', '.join(missing)} weren't found")
        items = (*interim.work, *interim.education, *interim.projects)
        if not any(item.id == entry_id for item in items):
            self.notes.append(f"Left out {what}: there's nothing called that to put it under")
            return None
        if any(role.id == entry_id for role in interim.work):
            elsewhere = [f for f in fact_ids if self.new[f].role_id not in (None, entry_id)]
            if elsewhere:
                self.notes.append(
                    f"{_upper(what)} cited facts you filed under another role: "
                    f"{', '.join(elsewhere)}"
                )
                fact_ids = tuple(fact_id for fact_id in fact_ids if fact_id not in elsewhere)
                if not fact_ids:
                    self.notes.append(f"Left out {what}: its facts belong to another role")
                    return None
        self.used.update(fact_ids)
        written_from = (*own, *(self.new[fact_id] for fact_id in fact_ids))
        bullet_id = self._id(entry_id, numbered=True) if planned.edit is None else planned.edit
        return _Draft(planned, entry_id, fact_ids, written_from, bullet_id)

    def _write(self, draft: _Draft, interim: Profile) -> VerifierReport:
        """Write a bullet and verify it."""
        return write_bullet(
            draft.written_from,
            interim,
            self.client,
            entry_id=draft.entry_id,
            edit=draft.planned.edit,
            avoid=self.avoid,
        )

    def _reviewed(self, draft: _Draft, report: VerifierReport) -> ReviewedOp:
        """The operation that adds a written bullet, or its new version, with its report."""
        planned = draft.planned
        op: ChangeOp
        if planned.edit is not None:
            op = EditBullet(bullet_id=draft.bullet_id, text=report.text, fact_ids=draft.fact_ids)
        else:
            role_types = {kind.id for kind in self.profile.role_types}
            bullet = Bullet(
                id=draft.bullet_id,
                text=report.text,
                strength=planned.strength,
                verification="yes" if report.passed else "no",
                tags=tuple(dict.fromkeys(tag for tag in planned.tags if tag in role_types)),
            )
            op = AddBullet(parent_id=draft.entry_id, bullet=bullet, fact_ids=draft.fact_ids)
        return ReviewedOp(op=op, verifier_report=report.model_dump(mode="json"))

    def _answers(self) -> list[ResolveOpenQuestion]:
        """An answered open question for each open question a new fact answers."""
        resolved: list[ResolveOpenQuestion] = []
        for question in self.profile.open_questions:
            if question.status != "open":
                continue
            answers = tuple(fact.id for fact in self.facts if fact.question_id == question.id)
            if answers:
                resolved.append(ResolveOpenQuestion(question_id=question.id, fact_ids=answers))
        return resolved


def _in_order[T, R](items: Sequence[T], work: Callable[[T], R], concurrency: int) -> list[R]:
    """`work` done on each item, with the results in the items' order: the first item alone, then
    the rest up to `concurrency` at a time in threads. Once one fails, no more start; the error of
    the first that failed, in the items' order, is raised when those already started are done."""
    if concurrency == 1 or len(items) <= 1:
        return [work(item) for item in items]
    results = [work(items[0])]
    rest = items[1:]
    pool = ThreadPoolExecutor(max_workers=min(concurrency, len(rest)), thread_name_prefix="bullet")
    try:
        futures = [pool.submit(work, item) for item in rest]
        wait(futures, return_when=FIRST_EXCEPTION)
    finally:
        pool.shutdown(cancel_futures=True)
    # Items start in order, so any that never started come after every one that failed.
    return results + [future.result() for future in futures]


def _position(dates: Sequence[str | None], when: str | None) -> int | None:
    """Where an item from the month `when` goes among items from `dates`, newest first: before
    the first older one, or at the end (None) when it's the oldest or has no date."""
    if when is None:
        return None
    return next((index for index, date in enumerate(dates) if date and date < when), None)


def _upper(text: str) -> str:
    return text[:1].upper() + text[1:]


def _in_words(value: str, words: Sequence[str]) -> bool:
    """Whether `value` appears in one of `words`, ignoring case and spacing."""
    wanted = " ".join(value.casefold().split())
    return any(wanted in " ".join(text.casefold().split()) for text in words)


_KINDS: dict[str, tuple[str, str]] = {
    "add_role": ("new role", "new roles"),
    "add_project": ("new project", "new projects"),
    "add_skill": ("new skill", "new skills"),
    "update_field": ("date or status update", "date or status updates"),
    "set_bullet_status": ("bullet made active", "bullets made active"),
    "add_bullet": ("new bullet", "new bullets"),
    "edit_bullet": ("new version of a bullet", "new versions of bullets"),
    "resolve_open_question": ("answered open question", "answered open questions"),
}


def _summary(ops: Sequence[ChangeOp], facts: int) -> str:
    """What the change set holds, e.g. `From 3 new facts: 2 new bullets, 1 new skill.`"""
    counts: dict[str, int] = {}
    for op in ops:
        counts[op.op] = counts.get(op.op, 0) + 1
    parts = [
        f"{count} {_KINDS[kind][0 if count == 1 else 1]}"
        for kind in _KINDS
        if (count := counts.get(kind, 0))
    ]
    return f"From {facts} new fact{'' if facts == 1 else 's'}: {', '.join(parts)}."
