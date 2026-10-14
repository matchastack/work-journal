"""Tailoring: a one-page resume chosen from the master profile for one job posting (T-020).

A heavy-tier prompt reads the profile and the parsed posting and chooses, without writing
(FR-TLR-2, FR-TLR-4, FR-TLR-5):
- the roles and projects to show, and their bullets, most relevant first, each with its relevance;
- for each bullet, the approved swap that fits the posting, or that it needs a new phrasing;
- each role's title, 4 to 6 courses per education entry, the skills lines, whether to show the
  role type's summary, and whether the skills go above the work experience.

Code checks the choices against the profile before they become a `Selection`:
- Load-bearing roles always appear (R7), and so do the roles and projects kept for the role type;
  those cut for it don't. Benched and planned items never appear (R8), and a bullet only under
  its own role or project. A role or project with no valid bullet chosen shows all of its own.
- Titles are approved ones (R9). Courses are the entry's own, 4 to 6 of them where it has that
  many: too few are topped up from the role type's subset, then from the full list.
- Skills are catalogue skills, never on the gaps list (R1, R3), and use the posting's spelling
  where it spells one differently. A skill marked verify-before-shipping is listed only when the
  owner confirmed it for this resume; text that names it still needs that confirmation, or the
  resume rules block the render. Each line's label is a catalogue category or preset label.
- A bullet's relevance becomes its priority, so fitting to one page cuts the least relevant
  bullets first (FR-RES-7).

Then the wording (FR-TLR-3). A bullet whose chosen swap fits gets it worked in (`apply_swap`),
and a bullet that needs a new phrasing gets one in the posting's words (`write_bullet`), each
checked by the verifier against the bullet's own facts. A swap that passes is used. A new
phrasing that passes is offered for approval, and `use_phrasings` puts the approved ones in the
resume; `swap_op` saves each as a swap (FR-WRT-5). Text that fails keeps the bullet's own words.

Notes name items by ID and never quote fact text.
"""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model, NonEmptyStr, Priority, Tag
from app.schema.fact import Fact
from app.schema.jobs import JobPosting, SwapUse
from app.schema.profile import Bullet, Education, Profile, Skill, SkillLine
from app.schema.variant import Section, TitleChoice, Variant
from app.schema.verification import VerifierReport
from app.selection import SelectedBullet, SelectedProject, SelectedRole, Selection, select
from app.writing import LINKEDIN_CONTEXT, apply_swap, write_bullet

PROMPT_NAME = "tailoring_selection"
PROMPT_VERSION = 1

VARIANT_ID = "tailored"
MIN_COURSES, MAX_COURSES = 4, 6
"""How many courses a tailored resume lists for each education entry (FR-TLR-4)."""
MAX_NEW_PHRASINGS = 5
"""At most this many new phrasings are written for one resume: each costs a writer call and a
claim check."""

Relevance = Literal["high", "medium", "low"]
_PRIORITY: dict[Relevance, Priority] = {"high": 1, "medium": 2, "low": 3}
_SECTIONS: tuple[Section, ...] = ("education", "work", "projects", "skills")
_SKILLS_FIRST: tuple[Section, ...] = ("education", "skills", "work", "projects")


class PlannedBullet(Model):
    id: NonEmptyStr
    relevance: Relevance = "medium"
    swap: int | None = Field(default=None, ge=1)
    """The number of the bullet's approved swap to use, 1 for its first, if one fits."""
    rephrase: bool = False
    """Offer a new phrasing in the posting's words; only when no swap fits."""


class PlannedItem(Model):
    """A role or project to show, with its bullets, most relevant first."""

    id: NonEmptyStr
    bullets: tuple[PlannedBullet, ...] = ()


class PlannedTitle(Model):
    role_id: NonEmptyStr
    title: NonEmptyStr


class PlannedCourses(Model):
    education_id: NonEmptyStr
    courses: tuple[str, ...] = ()


class PlannedSkillLine(Model):
    label: NonEmptyStr
    skills: tuple[str, ...] = ()


class TailoringPlan(Model):
    """The heavy-tier model's choices for one posting. Code checks each one."""

    role_type: str | None = None
    work: tuple[PlannedItem, ...] = ()
    projects: tuple[PlannedItem, ...] = ()
    titles: tuple[PlannedTitle, ...] = ()
    courses: tuple[PlannedCourses, ...] = ()
    skills: tuple[PlannedSkillLine, ...] = ()
    summary: bool = False
    skills_first: bool = False


@dataclass(frozen=True)
class Phrasing:
    """New words for one bullet of the resume, and what the verifier found in them."""

    bullet_id: str
    original: str
    report: VerifierReport

    @property
    def text(self) -> str:
        return self.report.text


@dataclass(frozen=True)
class Tailored:
    posting: JobPosting
    selection: Selection
    """What the resume shows, with the swaps worked in, before it's fitted to one page."""
    role_type: Tag | None = None
    swaps: tuple[Phrasing, ...] = ()
    """Approved swaps worked into bullets, all of which passed the verifier."""
    proposals: tuple[Phrasing, ...] = ()
    """New phrasings that passed the verifier, waiting for the owner's approval."""
    notes: tuple[str, ...] = ()
    """What code changed in or left out of the plan, and why."""

    @property
    def swaps_used(self) -> tuple[SwapUse, ...]:
        return tuple(SwapUse(bullet_id=swap.bullet_id, text=swap.text) for swap in self.swaps)


def tailor(
    profile: Profile,
    posting: JobPosting,
    client: LLMClient,
    *,
    facts: Sequence[Fact] = (),
    confirmed: Collection[str] = (),
    avoid: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> Tailored:
    """Choose a one-page resume from `profile` for `posting` with the heavy-tier model.

    `facts` are the facts the profile's bullets were written from: a swap or new phrasing is
    checked against its bullet's own. `confirmed` lists the skills marked verify-before-shipping
    that the owner confirmed for this resume, and `avoid` the owner's words to avoid.
    """
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call(
        "tailoring_selection",
        prompt,
        TailoringPlan,
        variables={"posting": posting_json(posting)},
        context=tailoring_context(profile),
    )
    notes: list[str] = []
    selection, role_type, chosen = _select(profile, posting, result.output, confirmed, notes)
    swaps, proposals = _reword(profile, posting, client, selection, chosen, facts, avoid, notes)
    selection = _with_texts(selection, {swap.bullet_id: swap.text for swap in swaps})
    return Tailored(
        posting=posting,
        selection=selection,
        role_type=role_type,
        swaps=tuple(swaps),
        proposals=tuple(proposals),
        notes=tuple(notes),
    )


def use_phrasings(tailored: Tailored, accepted: Collection[str]) -> Selection:
    """The resume with the new phrasings of the bullets in `accepted`, which the owner approved."""
    texts = {p.bullet_id: p.text for p in tailored.proposals if p.bullet_id in accepted}
    return _with_texts(tailored.selection, texts)


def posting_spellings(posting: JobPosting) -> tuple[str, ...]:
    """The posting's skills and key terms, spelled as it spells them."""
    return tuple(dict.fromkeys((*posting.must_have, *posting.nice_to_have, *posting.key_terms)))


def posting_json(posting: JobPosting) -> str:
    data = posting.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"url"})
    return json.dumps(data, indent=2, ensure_ascii=False)


def tailoring_context(profile: Profile) -> str:
    """The profile as the planner sees it, as JSON: what a resume may show, without contact
    details, benched or planned items, private bullets or swaps written for LinkedIn."""
    data = {
        "roleTypes": [{"id": kind.id, "name": kind.name} for kind in profile.role_types],
        "roles": [
            {
                "id": role.id,
                "organisation": role.name,
                "title": role.position,
                "titleVariants": list(role.title_variants),
                "startDate": role.start_date,
                "endDate": role.end_date,
                **({"loadBearing": True} if role.load_bearing else {}),
                **({"duringEducation": True} if role.during_education else {}),
                "keptFor": list(role.keep_for),
                "cutFor": list(role.cut_for),
                "bullets": _bullets_json(role.highlights),
            }
            for role in profile.work
        ],
        "education": [
            {
                "id": entry.id,
                "institution": entry.institution,
                "studyType": entry.study_type,
                "area": entry.area,
                "courses": list(entry.courses),
                "courseSubsets": {s.role_type: list(s.courses) for s in entry.coursework_subsets},
            }
            for entry in profile.education
        ],
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "keywords": list(project.keywords),
                "keptFor": list(project.keep_for),
                "cutFor": list(project.cut_for),
                "bullets": _bullets_json(project.highlights),
            }
            for project in profile.projects
            if project.status == "active"
        ],
        "skills": [
            {
                "name": skill.name,
                "category": skill.category,
                **({"tier": skill.tier} if skill.tier else {}),
                **({"aliases": list(skill.aliases)} if skill.aliases else {}),
                **({"verifyBeforeShipping": True} if skill.verify_before_shipping else {}),
            }
            for skill in profile.skills
        ],
        "gaps": list(profile.skill_gaps),
        "skillPresets": {
            preset.role_type: [
                {"label": line.label, "skills": list(line.skills)} for line in preset.lines
            ]
            for preset in profile.skill_presets
        },
        "summaries": {summary.role_type: summary.text for summary in profile.summaries},
    }
    return "The person's profile:\n" + json.dumps(data, indent=2, ensure_ascii=False)


def _bullets_json(bullets: Sequence[Bullet]) -> list[dict[str, object]]:
    return [
        {
            "id": bullet.id,
            "text": bullet.text,
            "strength": bullet.strength,
            **(
                {"strengthFor": {o.role_type: o.strength for o in bullet.strength_overrides}}
                if bullet.strength_overrides
                else {}
            ),
            "verification": bullet.verification,
            "swaps": [
                {
                    "number": number,
                    "text": swap.text,
                    **({"replaces": swap.replaces} if swap.replaces else {}),
                    "contexts": list(swap.contexts),
                }
                for number, swap in enumerate(bullet.swaps, start=1)
                if LINKEDIN_CONTEXT not in swap.contexts
            ],
        }
        for bullet in bullets
        if bullet.status == "active" and bullet.visibility != "private"
    ]


# --- Selection --------------------------------------------------------------------------------


def _select(
    profile: Profile,
    posting: JobPosting,
    plan: TailoringPlan,
    confirmed: Collection[str],
    notes: list[str],
) -> tuple[Selection, Tag | None, dict[str, PlannedBullet]]:
    """The checked selection, the role type, and the planned bullets it shows, by ID."""
    role_type: Tag | None = plan.role_type
    if role_type is not None and role_type not in {kind.id for kind in profile.role_types}:
        notes.append(f"{role_type} isn't one of your role types, so none is used")
        role_type = None
    variant = Variant(
        id=VARIANT_ID,
        name=posting.title,
        role_type=role_type,
        section_order=_SKILLS_FIRST if plan.skills_first else _SECTIONS,
        include_summary=plan.summary,
        titles=_titles(profile, plan.titles, notes),
    )
    base = select(profile, variant)
    if plan.summary and base.summary is None:
        notes.append("there's no summary for this role type, so none is shown")
    chosen: dict[str, PlannedBullet] = {}
    roles = {role.id: role for role in profile.work}
    work = _items(
        base.work,
        plan.work,
        "role",
        forced={
            role.id
            for role in profile.work
            if role.load_bearing or (role_type is not None and role_type in role.keep_for)
        },
        known=set(roles),
        chosen=chosen,
        notes=notes,
    )
    projects = _items(
        base.projects,
        plan.projects,
        "project",
        forced={
            project.id
            for project in profile.projects
            if role_type is not None and role_type in project.keep_for
        },
        known={project.id for project in profile.projects},
        chosen=chosen,
        notes=notes,
    )
    courses = {planned.education_id: planned.courses for planned in plan.courses}
    entries = {entry.id: entry for entry in profile.education}
    education = tuple(
        selected.model_copy(
            update={
                "courses": _courses(
                    entries[selected.id], courses.get(selected.id), role_type, notes
                )
            }
        )
        for selected in base.education
    )
    skills = _skills(profile, posting, plan.skills, base.skills, confirmed, notes)
    selection = base.model_copy(
        update={
            "work": work,
            "projects": projects,
            "education": education,
            "skills": skills,
            "max_pages": 1,
            "notes": (),
        }
    )
    return selection, role_type, chosen


def _titles(
    profile: Profile, planned: Sequence[PlannedTitle], notes: list[str]
) -> tuple[TitleChoice, ...]:
    """The approved titles the plan chose; any other is noted, and the role keeps its default."""
    roles = {role.id: role for role in profile.work}
    choices: dict[str, TitleChoice] = {}
    for title in planned:
        role = roles.get(title.role_id)
        if role is None or title.role_id in choices:
            continue
        if title.title in (role.position, *role.title_variants):
            choices[role.id] = TitleChoice(role_id=role.id, title=title.title)
        else:
            notes.append(f'"{title.title}" isn\'t an approved title for {role.id}')
    return tuple(choices.values())


def _items[T: (SelectedRole, SelectedProject)](
    selected: Sequence[T],
    planned: Sequence[PlannedItem],
    kind: str,
    *,
    forced: set[str],
    known: set[str],
    chosen: dict[str, PlannedBullet],
    notes: list[str],
) -> tuple[T, ...]:
    """The roles or projects to show, in the profile's order: the planned ones and the forced
    ones, each with its chosen bullets."""
    plans: dict[str, PlannedItem] = {}
    for item in planned:
        plans.setdefault(item.id, item)
    available = {item.id for item in selected}
    for item_id in plans:
        if item_id not in known:
            notes.append(f"there's no {kind} {item_id}, so it's left out")
        elif item_id not in available:
            notes.append(f"{item_id} is cut for this role type or not active, so it's left out")
    kept: list[T] = []
    for item in selected:
        plan = plans.get(item.id)
        if plan is None and item.id not in forced:
            continue
        bullets = _bullets(item.id, item.bullets, plan, chosen, notes)
        kept.append(item.model_copy(update={"bullets": bullets}))
    return tuple(kept)


def _bullets(
    item_id: str,
    bullets: tuple[SelectedBullet, ...],
    plan: PlannedItem | None,
    chosen: dict[str, PlannedBullet],
    notes: list[str],
) -> tuple[SelectedBullet, ...]:
    """The planned bullets, most relevant first, with their relevance as priority. When none of
    them is one of the item's own active bullets, all of those."""
    own = {bullet.id: bullet for bullet in bullets}
    picked: list[SelectedBullet] = []
    for planned in plan.bullets if plan else ():
        bullet = own.get(planned.id)
        if bullet is None:
            notes.append(f"{planned.id} isn't an active bullet of {item_id}, so it's left out")
        elif planned.id not in chosen:
            chosen[planned.id] = planned
            picked.append(bullet.model_copy(update={"priority": _PRIORITY[planned.relevance]}))
    return tuple(picked) or bullets


def _courses(
    entry: Education, planned: Sequence[str] | None, role_type: Tag | None, notes: list[str]
) -> tuple[str, ...]:
    """4 to 6 of the entry's courses: the planned ones, topped up or trimmed as needed."""
    known = entry.courses
    picked = [course for course in dict.fromkeys(planned or ()) if course in known]
    if planned and len(picked) < len(set(planned)):
        notes.append(f"{entry.id} lists courses that aren't in its coursework, so they're left out")
    if len(picked) > MAX_COURSES:
        notes.append(f"{entry.id} lists {MAX_COURSES} of the {len(picked)} courses chosen")
        picked = picked[:MAX_COURSES]
    wanted = min(MIN_COURSES, len(known))
    if len(picked) < wanted:
        subset = next((s.courses for s in entry.coursework_subsets if s.role_type == role_type), ())
        for course in (*subset, *known):
            if len(picked) >= wanted:
                break
            if course not in picked:
                picked.append(course)
        if planned:
            notes.append(f"{entry.id} needs {wanted} courses, so the list was topped up")
    return tuple(picked)


def _skills(
    profile: Profile,
    posting: JobPosting,
    planned: Sequence[PlannedSkillLine],
    preset: Sequence[SkillLine],
    confirmed: Collection[str],
    notes: list[str],
) -> tuple[SkillLine, ...]:
    """The planned skills lines, or the role type's preset if the plan has none, keeping only
    catalogue skills, each once, in the posting's spelling."""
    catalogue = {
        spelling.casefold(): skill
        for skill in profile.skills
        for spelling in (skill.name, *skill.aliases)
    }
    gaps = {gap.casefold() for gap in profile.skill_gaps}
    labels = {skill.category for skill in profile.skills} | {
        line.label for p in profile.skill_presets for line in p.lines
    }
    spellings: dict[str, str] = {}
    for term in posting_spellings(posting):
        spellings.setdefault(term.casefold(), term)
    done = {name.casefold() for name in confirmed}
    lines = planned or [PlannedSkillLine(label=line.label, skills=line.skills) for line in preset]
    listed: set[str] = set()
    grouped: dict[str, list[str]] = {}
    for line in lines:
        for name in line.skills:
            if name.casefold() in gaps:
                notes.append(f"{name} is on your gaps list, so it's never listed")
                continue
            skill = catalogue.get(name.casefold())
            if skill is None:
                notes.append(f"{name} isn't in your skills catalogue, so it's left out")
                continue
            if skill.name in listed:
                continue
            if skill.verify_before_shipping and skill.name.casefold() not in done:
                notes.append(
                    f"{skill.name} is marked verify-before-shipping, so it's listed only once you "
                    "confirm it for this resume"
                )
                continue
            listed.add(skill.name)
            label = line.label if line.label in labels else skill.category
            grouped.setdefault(label, []).append(_spelled(skill, spellings))
    return tuple(SkillLine(label=label, skills=tuple(names)) for label, names in grouped.items())


def _spelled(skill: Skill, spellings: dict[str, str]) -> str:
    """The skill as the posting spells it, if it names it; otherwise its preferred spelling."""
    for spelling in (skill.name, *skill.aliases):
        if spelling.casefold() in spellings:
            return spellings[spelling.casefold()]
    return skill.name


# --- Wording ----------------------------------------------------------------------------------


def _reword(
    profile: Profile,
    posting: JobPosting,
    client: LLMClient,
    selection: Selection,
    chosen: dict[str, PlannedBullet],
    facts: Sequence[Fact],
    avoid: Sequence[str],
    notes: list[str],
) -> tuple[list[Phrasing], list[Phrasing]]:
    """Work in the chosen swaps, and write the new phrasings asked for, in resume order."""
    by_id = {fact.id: fact for fact in facts}
    owners = {
        bullet.id: (item.id, bullet)
        for item in (*profile.work, *profile.projects)
        for bullet in item.highlights
    }
    swaps: list[Phrasing] = []
    proposals: list[Phrasing] = []
    shown = [b.id for item in (*selection.work, *selection.projects) for b in item.bullets]
    for bullet_id in shown:
        planned = chosen.get(bullet_id)
        if planned is None or (planned.swap is None and not planned.rephrase):
            continue
        entry_id, bullet = owners[bullet_id]
        sources = [by_id[source] for source in bullet.sources if source in by_id]
        if not sources:
            notes.append(f"{bullet_id} has none of its facts here, so it keeps its own words")
            continue
        if planned.swap is not None:
            if planned.swap > len(bullet.swaps):
                notes.append(f"{bullet_id} has no swap {planned.swap}, so it keeps its own words")
                continue
            swap = bullet.swaps[planned.swap - 1]
            if LINKEDIN_CONTEXT in swap.contexts:
                notes.append(
                    f"swap {planned.swap} of {bullet_id} is for LinkedIn, so it isn't used"
                )
                continue
            report = apply_swap(
                sources,
                profile,
                client,
                bullet_id=bullet_id,
                swap=swap,
                posting=posting,
                avoid=avoid,
            )
            if report.passed:
                swaps.append(Phrasing(bullet_id=bullet_id, original=bullet.text, report=report))
            else:
                notes.append(f"the swap for {bullet_id} didn't pass the checks, so it isn't used")
            continue
        if len(proposals) >= MAX_NEW_PHRASINGS:
            notes.append(
                f"{bullet_id} keeps its own words: new phrasings are limited to "
                f"{MAX_NEW_PHRASINGS} per resume"
            )
            continue
        report = write_bullet(
            sources,
            profile,
            client,
            entry_id=entry_id,
            edit=bullet_id,
            style="posting",
            posting=posting,
            avoid=avoid,
        )
        if report.passed:
            proposals.append(Phrasing(bullet_id=bullet_id, original=bullet.text, report=report))
        else:
            notes.append(f"the new phrasing of {bullet_id} didn't pass the checks")
    return swaps, proposals


def _with_texts(selection: Selection, texts: dict[str, str]) -> Selection:
    """The selection with the bullets in `texts` reworded."""
    if not texts:
        return selection

    def reworded(bullets: tuple[SelectedBullet, ...]) -> tuple[SelectedBullet, ...]:
        return tuple(
            bullet.model_copy(update={"text": texts[bullet.id]}) if bullet.id in texts else bullet
            for bullet in bullets
        )

    work = tuple(
        role.model_copy(update={"bullets": reworded(role.bullets)}) for role in selection.work
    )
    projects = tuple(
        project.model_copy(update={"bullets": reworded(project.bullets)})
        for project in selection.projects
    )
    return selection.model_copy(update={"work": work, "projects": projects})
