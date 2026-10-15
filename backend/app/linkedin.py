"""The LinkedIn pack: the headline, About and position descriptions, ready to paste (FR-LIN).

LinkedIn's Profile Edit API is closed to self-serve apps (C1), so the owner pastes the text in.
The pack is written from the master profile's public content, as the LinkedIn variant selects
it, with the role titles from their approved variants (FR-LIN-4). A standard-tier prompt writes
each section in the first person (FR-WRT-2), within LinkedIn's limits (FR-LIN-1).

**What it's written from.** The profile is the source of truth for every output (G6), so each
section's facts come from it: every bullet the section draws on stands in as a fact, with the
numbers code reads from its text, as the import reads them. Each role, education entry, project,
the skills catalogue and the owner's label add a fact of their own, so the judge can check titles
and tools too. The facts hold only public content, so nothing the owner keeps off the web, such
as a resume-only bullet, reaches the pack. Every section passes the verifier before it's shown:
text that fails is written once more, and flagged if it fails again (FR-FID-7).

**Only what changed.** Each section records a hash of what it was written from: the facts, the
role and title, the prompt version and the owner's style notes. A section whose hash matches the
last pack the owner marked done is carried over without a call; one that differs is written
again, and listed only if its text changed (FR-LIN-2). Marking a pack done stores its sections
as the new snapshot.

**Titles.** A current role whose LinkedIn title differs from the title on the most recent
application is flagged, so the owner can make them match (FR-LIN-4).
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Id, Model, NonEmptyStr
from app.schema.fact import Fact
from app.schema.jobs import Application
from app.schema.profile import Profile
from app.schema.variant import Variant
from app.schema.verification import VerifierReport
from app.selection import SelectedBullet, SelectedRole, Selection, select
from app.validate.claims import facts_json
from app.validate.lint import allowed_terms
from app.validate.quantities import extract_metrics
from app.validate.style import LINKEDIN_LIMITS, LinkedInSection, linkedin_length
from app.validate.verifier import TextKind, Verifier

PROMPT_NAME = "linkedin_pack"
PROMPT_VERSION = 1

LINKEDIN_VARIANT = Variant(id="linkedin", name="LinkedIn", audience="linkedin", max_pages=None)
"""Everything public, with each role's first approved title (FR-LIN-4)."""

_KINDS: dict[LinkedInSection, TextKind] = {
    "headline": "linkedin_headline",
    "about": "linkedin_about",
    "position": "linkedin_position",
}


class WrittenSection(Model):
    text: NonEmptyStr


class PackSection(Model):
    """One section of the pack, as it should read on LinkedIn."""

    key: NonEmptyStr
    """`headline`, `about` or `position:<role ID>`."""
    kind: LinkedInSection
    label: NonEmptyStr
    """What the owner sees, e.g. `Position: Backend Engineer at Northwind Traders`."""
    role_id: Id | None = None
    title: str | None = None
    """For a position, the title to use on LinkedIn."""
    text: NonEmptyStr
    source: NonEmptyStr
    """A hash of what the section was written from."""
    changed: bool
    """True when the text differs from the last pack marked done, or none was."""
    report: VerifierReport | None = None
    """What the verifier found. None for a section carried over from the last pack marked done,
    whose text the owner already used."""
    notes: tuple[str, ...] = ()
    """Things to check, such as a title that differs from the latest application's."""

    @property
    def limit(self) -> int:
        return LINKEDIN_LIMITS[self.kind]

    @property
    def length(self) -> int:
        """The length as LinkedIn counts it."""
        return linkedin_length(self.text)

    @property
    def flagged(self) -> bool:
        """True when the text failed the verifier: the owner must not use it as it is."""
        return self.report is not None and not self.report.passed


class LinkedInPack(Model):
    sections: tuple[PackSection, ...]
    removed: tuple[str, ...] = ()
    """The labels of sections marked done before that the profile no longer has, such as a role
    that is now private: take them off LinkedIn."""
    profile_version: int | None = None
    """The profile version the pack was written from, when it came from the database."""

    @property
    def changed(self) -> tuple[PackSection, ...]:
        return tuple(section for section in self.sections if section.changed)


class DoneSection(Model):
    key: NonEmptyStr
    label: NonEmptyStr
    text: NonEmptyStr
    source: NonEmptyStr


class LinkedInDone(Model):
    """The sections the owner last marked done: what their LinkedIn profile says (FR-LIN-3)."""

    sections: tuple[DoneSection, ...] = ()
    marked_at: datetime | None = None


class _Plan(Model):
    key: NonEmptyStr
    kind: LinkedInSection
    label: NonEmptyStr
    role_id: Id | None = None
    title: str | None = None
    item: str
    """What the request says about the role, for a position."""
    facts: tuple[Fact, ...]


def write_pack(
    profile: Profile,
    client: LLMClient,
    *,
    done: LinkedInDone | None = None,
    latest_application: Application | None = None,
    avoid: Sequence[str] = (),
    preferences: Sequence[str] = (),
    everything: bool = False,
    profile_version: int | None = None,
    prompt: Prompt | None = None,
) -> LinkedInPack:
    """Write the LinkedIn pack for `profile`.

    Sections whose sources match `done`, the last pack marked done, are carried over without a
    call, unless `everything` is true. `avoid` and `preferences` are the owner's style notes
    (FR-WRT-4).
    """
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    selection = select(profile, LINKEDIN_VARIANT)
    plans = plan_sections(profile, selection)
    earlier = {section.key: section for section in (done.sections if done else ())}
    context = pack_context(profile, selection)
    title_notes = _title_notes(selection, latest_application)
    sections: list[PackSection] = []
    for plan in plans:
        source = _source(plan, prompt, avoid, preferences)
        previous = earlier.get(plan.key)
        notes = title_notes.get(plan.role_id, ()) if plan.role_id else ()
        report: VerifierReport | None = None
        if previous is not None and previous.source == source and not everything:
            text, changed = previous.text, False
        else:
            report = _write(plan, profile, client, prompt, context, avoid, preferences)
            text, changed = report.text, previous is None or previous.text != report.text
        section = PackSection(
            key=plan.key,
            kind=plan.kind,
            label=plan.label,
            role_id=plan.role_id,
            title=plan.title,
            text=text,
            source=source,
            changed=changed,
            report=report,
            notes=notes,
        )
        sections.append(section)
    keys = {plan.key for plan in plans}
    return LinkedInPack(
        sections=tuple(sections),
        removed=tuple(section.label for section in earlier.values() if section.key not in keys),
        profile_version=profile_version,
    )


def mark_done(
    pack: LinkedInPack, previous: LinkedInDone | None, *, now: datetime
) -> tuple[LinkedInDone, tuple[PackSection, ...]]:
    """The snapshot after the owner pastes `pack` into LinkedIn, and the flagged sections left
    out of it. A flagged section keeps its earlier snapshot, if it has one, so it's written
    again next time."""
    earlier = {section.key: section for section in (previous.sections if previous else ())}
    sections: list[DoneSection] = []
    flagged: list[PackSection] = []
    for section in pack.sections:
        if section.flagged:
            flagged.append(section)
            if section.key in earlier:
                sections.append(earlier[section.key])
            continue
        sections.append(
            DoneSection(
                key=section.key, label=section.label, text=section.text, source=section.source
            )
        )
    return LinkedInDone(sections=tuple(sections), marked_at=now), tuple(flagged)


def plan_sections(profile: Profile, selection: Selection) -> list[_Plan]:
    """The sections to write, each with its facts: the headline, About, then one position for
    each role with public bullets, in the profile's order. The headline draws on the current
    roles, or the latest role when none is current."""
    terms = allowed_terms(profile)
    roles = {role.id: _role_facts(role, terms) for role in selection.work}
    current = [role for role in selection.work if role.end_date is None]
    headline_roles = current or selection.work[:1]
    profile_facts = _profile_facts(profile)
    skills = _skills_fact(profile)
    education = [
        Fact(
            id=f"education-{entry.id}"[:64],
            kind="learning",
            statement=(
                f"Studied for a {entry.study_type} in {entry.area} at {entry.institution}, "
                f"{_span(entry.start_date, entry.end_date)}."
                + (f" Honours: {entry.honours}." if entry.honours else "")
            ),
            date=entry.end_date,
        )
        for entry in selection.education
    ]
    education_bullets = [
        _bullet_fact(bullet, terms) for entry in selection.education for bullet in entry.bullets
    ]
    projects = [
        fact
        for project in selection.projects
        for fact in (
            Fact(
                id=f"project-{project.id}"[:64],
                kind="project",
                statement=f"Built the project {project.name}"
                + (f" with {', '.join(project.keywords)}." if project.keywords else "."),
                tools=project.keywords,
                project_id=project.id,
            ),
            *(_bullet_fact(bullet, terms, project_id=project.id) for bullet in project.bullets),
        )
    ]
    headline = [
        *profile_facts,
        *(fact for role in headline_roles for fact in roles[role.id]),
        *([skills] if skills else []),
    ]
    about = [
        *profile_facts,
        *(fact for facts in roles.values() for fact in facts),
        *projects,
        *education,
        *education_bullets,
        *([skills] if skills else []),
    ]
    plans = [
        _Plan(key="headline", kind="headline", label="Headline", item="", facts=tuple(headline)),
        _Plan(key="about", kind="about", label="About", item="", facts=tuple(about)),
    ]
    plans += [
        _Plan(
            key=f"position:{role.id}",
            kind="position",
            label=f"Position: {role.title} at {role.organisation}",
            role_id=role.id,
            title=role.title,
            item=_role_item(role),
            facts=tuple(roles[role.id]),
        )
        for role in selection.work
        if role.bullets
    ]
    return plans


def pack_context(profile: Profile, selection: Selection) -> str:
    """The public profile as the writer sees it, as JSON: the LinkedIn selection's roles,
    education and projects with their bullets, the skills that may be claimed, and the gaps."""
    boundaries = {role.id: list(role.honesty_boundaries) for role in profile.work}
    data = {
        "roles": [
            {
                "id": role.id,
                "organisation": role.organisation,
                "title": role.title,
                "startDate": role.start_date,
                "endDate": role.end_date,
                **({"sensitive": True} if role.sensitive else {}),
                "honestyBoundaries": boundaries.get(role.id, []),
                "bullets": _texts(role.bullets),
            }
            for role in selection.work
        ],
        "education": [
            {
                "institution": entry.institution,
                "studyType": entry.study_type,
                "area": entry.area,
                "bullets": _texts(entry.bullets),
            }
            for entry in selection.education
        ],
        "projects": [
            {
                "name": project.name,
                "keywords": list(project.keywords),
                "bullets": _texts(project.bullets),
            }
            for project in selection.projects
        ],
        "skills": [
            {"name": skill.name, **({"tier": skill.tier} if skill.tier else {})}
            for skill in profile.skills
            if not skill.verify_before_shipping
        ],
        "gaps": list(profile.skill_gaps),
    }
    return "The person's public profile:\n" + json.dumps(data, indent=2, ensure_ascii=False)


def _write(
    plan: _Plan,
    profile: Profile,
    client: LLMClient,
    prompt: Prompt,
    context: str,
    avoid: Sequence[str],
    preferences: Sequence[str],
) -> VerifierReport:
    verifier = Verifier(
        client=client,
        profile=profile,
        facts=plan.facts,
        kind=_KINDS[plan.kind],
        entry_id=plan.role_id,
        avoid=avoid,
    )
    attempts: list[str] = []

    def write(feedback: str | None) -> str:
        variables = {
            "section": plan.kind,
            "limit": f"{LINKEDIN_LIMITS[plan.kind]:,}",
            "item": f"\n\n{plan.item}" if plan.item else "",
            "facts": facts_json(plan.facts),
            "preferences": _listed(preferences),
            "avoid": _listed(avoid),
            "attempt": attempts[-1] if feedback and attempts else "none",
            "problems": feedback or "none",
        }
        result = client.call(
            "linkedin_pack", prompt, WrittenSection, variables=variables, context=context
        )
        attempts.append(result.output.text)
        return result.output.text

    return verifier.write(write)


def _title_notes(
    selection: Selection, latest: Application | None
) -> dict[str | None, tuple[str, ...]]:
    """A note on each current role whose LinkedIn title isn't the latest application's."""
    if latest is None:
        return {}
    notes: dict[str | None, tuple[str, ...]] = {}
    for role in selection.work:
        if role.end_date is None and role.title.casefold() != latest.title_variant.casefold():
            notes[role.id] = (
                f'LinkedIn shows "{role.title}" for {role.organisation}, but your latest '
                f"application ({latest.company}, {latest.applied_on.isoformat()}) used "
                f'"{latest.title_variant}". Make them match, or check that both are right.',
            )
    return notes


def _source(plan: _Plan, prompt: Prompt, avoid: Sequence[str], preferences: Sequence[str]) -> str:
    data = {
        "prompt": prompt.id,
        "kind": plan.kind,
        "title": plan.title,
        "item": plan.item,
        "facts": [fact.model_dump(mode="json") for fact in plan.facts],
        "avoid": list(avoid),
        "preferences": list(preferences),
    }
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _role_facts(role: SelectedRole, terms: Sequence[str]) -> list[Fact]:
    where = f" in {role.location}" if role.location else ""
    if role.end_date is None:
        statement = f"Works as {role.title} at {role.organisation}{where}, "
    else:
        statement = f"Worked as {role.title} at {role.organisation}{where}, "
    statement += f"{_span(role.start_date, role.end_date)}."
    role_fact = Fact(
        id=f"role-{role.id}"[:64],
        kind="role_change",
        statement=statement,
        date=role.start_date,
        role_id=role.id,
    )
    return [role_fact, *(_bullet_fact(bullet, terms, role_id=role.id) for bullet in role.bullets)]


def _bullet_fact(
    bullet: SelectedBullet,
    terms: Sequence[str],
    *,
    role_id: str | None = None,
    project_id: str | None = None,
) -> Fact:
    """A bullet as a fact: its text, with the numbers code reads from it."""
    return Fact(
        id=bullet.id,
        statement=bullet.text,
        metrics=extract_metrics(bullet.text, terms),
        role_id=role_id,
        project_id=project_id,
    )


def _profile_facts(profile: Profile) -> list[Fact]:
    """The owner's own label and summary for their work."""
    facts: list[Fact] = []
    if profile.basics.label:
        facts.append(
            Fact(id="label", kind="role_change", statement=f"Works as a {profile.basics.label}.")
        )
    if profile.basics.summary:
        facts.append(Fact(id="summary", kind="role_change", statement=profile.basics.summary))
    return facts


def _skills_fact(profile: Profile) -> Fact | None:
    """The skills that may be claimed. Skills that need confirming first are left out (R3), and
    academic ones are said to be studied."""
    usable = [skill for skill in profile.skills if not skill.verify_before_shipping]
    used = [skill.name for skill in usable if skill.tier != "academic"]
    studied = [skill.name for skill in usable if skill.tier == "academic"]
    if not usable:
        return None
    parts: list[str] = []
    if used:
        parts.append(f"Works with {', '.join(used)}.")
    if studied:
        parts.append(f"Studied, without using them at work: {', '.join(studied)}.")
    return Fact(id="skills", kind="skill", statement=" ".join(parts), tools=tuple(used))


def _role_item(role: SelectedRole) -> str:
    when = (
        f"a current role, since {_month(role.start_date)}"
        if role.end_date is None
        else f"a past role, from {_month(role.start_date)} to {_month(role.end_date)}"
    )
    sensitive = " The role is sensitive." if role.sensitive else ""
    return f"The role: {role.title} at {role.organisation}, {when}.{sensitive}"


def _span(start: str, end: str | None) -> str:
    if end is None:
        return f"since {_month(start)}"
    return f"from {_month(start)} to {_month(end)}"


def _month(year_month: str) -> str:
    year, month = year_month.split("-")
    return date(int(year), int(month), 1).strftime("%B %Y")


def _texts(bullets: Sequence[SelectedBullet]) -> list[str]:
    return [bullet.text for bullet in bullets]


def _listed(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "none"
