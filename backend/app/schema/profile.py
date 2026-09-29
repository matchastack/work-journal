"""The master profile: everything worth using on a resume, with its metadata.

Field names follow JSON Resume where it has one (`work`, `highlights`, `studyType`, `keywords`);
the rest are this project's extensions (requirements §5, FR-PRF).
"""

from collections.abc import Iterator
from typing import Literal, Self

from pydantic import model_validator

from app.schema.common import (
    Id,
    ItemStatus,
    Model,
    NonEmptyStr,
    Note,
    Priority,
    Tag,
    Visibility,
    YearMonth,
)

Strength = Literal["high", "medium", "low"]
Verification = Literal["yes", "partial", "no"]


class Swap(Model):
    """An approved alternative phrasing: the same fact in another audience's words."""

    text: NonEmptyStr
    replaces: str | None = None
    """The part of the bullet this replaces. None means the whole bullet, or, for an imported
    swap, that the master resume doesn't say which part the phrase replaces."""
    contexts: tuple[str, ...] = ()
    """Where this phrasing fits, e.g. a role type or the posting it was written for."""
    origin: Literal["imported", "approved"] = "imported"


class StrengthOverride(Model):
    """A bullet's strength for one role type, when it differs from the default."""

    role_type: Tag
    strength: Strength


class Bullet(Model):
    """One highlight line under a role, education entry or project."""

    id: Id
    text: NonEmptyStr
    strength: Strength = "medium"
    strength_overrides: tuple[StrengthOverride, ...] = ()
    verification: Verification = "no"
    verification_note: str | None = None
    swaps: tuple[Swap, ...] = ()
    needs: tuple[Id, ...] = ()
    """IDs of open questions whose answers would make this bullet stronger."""
    notes: tuple[Note, ...] = ()
    status: ItemStatus = "active"
    status_reason: str | None = None
    tags: tuple[Tag, ...] = ()
    priority: Priority = 2
    visibility: Visibility = "public"
    sources: tuple[Id, ...] = ()
    """IDs of the facts this bullet is based on."""

    @model_validator(mode="after")
    def benched_needs_reason(self) -> Self:
        if self.status == "benched" and not self.status_reason:
            raise ValueError(f"benched bullet {self.id!r} needs a status reason")
        return self


class Location(Model):
    city: str | None = None
    region: str | None = None
    country_code: str | None = None


class SocialProfile(Model):
    """An online profile, e.g. GitHub or LinkedIn (JSON Resume `basics.profiles`)."""

    network: NonEmptyStr
    username: str | None = None
    url: str | None = None


class Basics(Model):
    name: NonEmptyStr
    label: str | None = None
    email: str | None = None
    email_visibility: Visibility = "public"
    phone: str | None = None
    phone_visibility: Visibility = "resume_only"
    url: str | None = None
    summary: str | None = None
    location: Location | None = None
    location_visibility: Visibility = "public"
    profiles: tuple[SocialProfile, ...] = ()


class Role(Model):
    """A job (JSON Resume `work` entry)."""

    id: Id
    name: NonEmptyStr
    """The organisation."""
    position: NonEmptyStr
    """The official title."""
    title_variants: tuple[str, ...] = ()
    """Approved titles to use instead of the official one, e.g. to match a posting."""
    location: str | None = None
    url: str | None = None
    start_date: YearMonth
    end_date: YearMonth | None = None
    """None means the role is current."""
    summary: str | None = None
    highlights: tuple[Bullet, ...] = ()
    load_bearing: bool = False
    """Must always appear, e.g. the current job."""
    during_education: bool = False
    """Held while studying, so cutting it leaves no gap."""
    keep_for: tuple[Tag, ...] = ()
    cut_for: tuple[Tag, ...] = ()
    sensitivity: Literal["normal", "sensitive"] = "normal"
    """Sensitive roles describe architecture and outcomes only (resume rule R5)."""
    honesty_boundaries: tuple[str, ...] = ()
    """Things that must never be claimed for this role."""
    priority: Priority = 2
    notes: tuple[Note, ...] = ()

    @model_validator(mode="after")
    def dates_in_order(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(f"role {self.id!r} ends before it starts")
        return self


class CourseworkSubset(Model):
    """The courses to list for one role type."""

    role_type: Tag
    courses: tuple[str, ...]


class Education(Model):
    """A degree or qualification (JSON Resume `education` entry)."""

    id: Id
    institution: NonEmptyStr
    study_type: NonEmptyStr
    area: NonEmptyStr
    honours: str | None = None
    location: str | None = None
    url: str | None = None
    start_date: YearMonth
    end_date: YearMonth | None = None
    courses: tuple[str, ...] = ()
    """The full coursework list."""
    coursework_subsets: tuple[CourseworkSubset, ...] = ()
    highlights: tuple[Bullet, ...] = ()
    rules: tuple[str, ...] = ()
    """Rules for rendering, e.g. which details must never be printed."""

    @model_validator(mode="after")
    def check_dates_and_subsets(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(f"education {self.id!r} ends before it starts")
        known = set(self.courses)
        for subset in self.coursework_subsets:
            unknown = [course for course in subset.courses if course not in known]
            if unknown:
                raise ValueError(
                    f"coursework subset {subset.role_type!r} lists courses not in the "
                    f"full list: {unknown}"
                )
        return self


class Project(Model):
    """A personal or academic project (JSON Resume `projects` entry)."""

    id: Id
    name: NonEmptyStr
    description: str | None = None
    keywords: tuple[str, ...] = ()
    """The tech stack."""
    url: str | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    highlights: tuple[Bullet, ...] = ()
    status: ItemStatus = "active"
    status_reason: str | None = None
    keep_for: tuple[Tag, ...] = ()
    cut_for: tuple[Tag, ...] = ()
    tags: tuple[Tag, ...] = ()
    priority: Priority = 2
    notes: tuple[Note, ...] = ()

    @model_validator(mode="after")
    def check_status_and_dates(self) -> Self:
        if self.status == "benched" and not self.status_reason:
            raise ValueError(f"benched project {self.id!r} needs a status reason")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError(f"project {self.id!r} ends before it starts")
        return self


class Skill(Model):
    name: NonEmptyStr
    """The preferred spelling, e.g. `Node.js`."""
    category: NonEmptyStr
    tier: Literal["strong", "working", "academic"] | None = None
    verify_before_shipping: bool = False
    """Needs explicit confirmation before it goes on a resume (resume rule R3)."""
    aliases: tuple[str, ...] = ()
    """Other spellings to recognise and correct, e.g. `NodeJS`."""
    notes: tuple[Note, ...] = ()


class SkillLine(Model):
    """One line of a skills section, e.g. `Languages: Python, SQL`."""

    label: NonEmptyStr
    skills: tuple[str, ...]


class SkillPreset(Model):
    """The skills section to use for one role type."""

    role_type: Tag
    lines: tuple[SkillLine, ...]


class Summary(Model):
    """A summary paragraph for one role type. Off by default."""

    role_type: Tag
    text: NonEmptyStr


class OpenQuestion(Model):
    """A missing detail that blocks a stronger bullet."""

    id: Id
    question: NonEmptyStr
    status: Literal["open", "answered", "closed"] = "open"
    answer_fact_ids: tuple[Id, ...] = ()


class RoleType(Model):
    """A kind of job the owner applies for, e.g. backend or ML."""

    id: Tag
    name: NonEmptyStr


class Profile(Model):
    """The master profile: the superset every resume, page and LinkedIn text is drawn from."""

    basics: Basics
    role_types: tuple[RoleType, ...] = ()
    work: tuple[Role, ...] = ()
    education: tuple[Education, ...] = ()
    projects: tuple[Project, ...] = ()
    skills: tuple[Skill, ...] = ()
    skill_gaps: tuple[str, ...] = ()
    """Skills that must never be claimed."""
    skill_presets: tuple[SkillPreset, ...] = ()
    summaries: tuple[Summary, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()

    def all_bullets(self) -> Iterator[Bullet]:
        """Every bullet in the profile, in document order."""
        for role in self.work:
            yield from role.highlights
        for education in self.education:
            yield from education.highlights
        for project in self.projects:
            yield from project.highlights

    def bullets_blocked_by(self, question_id: str) -> tuple[Bullet, ...]:
        """The bullets that an open question would make stronger."""
        return tuple(bullet for bullet in self.all_bullets() if question_id in bullet.needs)

    @model_validator(mode="after")
    def check_integrity(self) -> Self:
        ids = [
            *(role.id for role in self.work),
            *(education.id for education in self.education),
            *(project.id for project in self.projects),
            *(bullet.id for bullet in self.all_bullets()),
            *(question.id for question in self.open_questions),
        ]
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if duplicates:
            raise ValueError(f"IDs must be unique across the profile: {duplicates}")

        skill_names = [skill.name.casefold() for skill in self.skills]
        known_skills = set(skill_names)
        if len(skill_names) != len(known_skills):
            raise ValueError("skill names must be unique")
        claimed_gaps = sorted(gap for gap in self.skill_gaps if gap.casefold() in known_skills)
        if claimed_gaps:
            raise ValueError(f"skills can't be both in the catalogue and gaps: {claimed_gaps}")

        role_type_ids = {role_type.id for role_type in self.role_types}
        referenced = [
            *(preset.role_type for preset in self.skill_presets),
            *(summary.role_type for summary in self.summaries),
            *(
                subset.role_type
                for education in self.education
                for subset in education.coursework_subsets
            ),
        ]
        unknown = sorted({role_type for role_type in referenced if role_type not in role_type_ids})
        if unknown:
            raise ValueError(f"undefined role types: {unknown}")
        return self
