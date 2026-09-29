"""Variant selection: what one resume, page or LinkedIn text shows from the master profile.

`select(profile, variant)` is a pure function (FR-RES-5). It follows the variant's role type:

- **Roles and projects** are left out when the role type is in their cut-for list, except that
  load-bearing roles always appear (R7). Items kept for the role type rank first.
- **Bullets:** benched and planned bullets and projects never appear (R8). A bullet's strength
  is its override for the role type, if it has one.
- **Tags:** when the variant lists tags, tagged bullets and projects need one of them; untagged
  ones stay.
- **Role type presets:** the coursework subset, skills preset and summary are the ones for the
  role type. A variant without a role type is the full master document: all coursework and
  every skill.
- **Visibility:** fields show when the audience may see them, so resumes show `resume_only`
  fields such as the phone and web pages don't. The variant can also hide contact details.

Fitting the result on a page comes later (T-010), so selection keeps each item's priority and
each bullet's strength for it.
"""

from collections.abc import Sequence

from app.schema.common import Id, Model, NonEmptyStr, Priority, Tag, Visibility, YearMonth
from app.schema.profile import (
    Basics,
    Bullet,
    Education,
    Location,
    Profile,
    Project,
    Role,
    SkillLine,
    SocialProfile,
    Strength,
)
from app.schema.variant import Audience, Section, Variant

_SHOWN: dict[Audience, frozenset[Visibility]] = {
    "resume": frozenset({"public", "resume_only"}),
    "web": frozenset({"public"}),
    "linkedin": frozenset({"public"}),
}


class SelectedBullet(Model):
    id: Id
    text: NonEmptyStr
    strength: Strength
    """The bullet's strength for the variant's role type."""
    priority: Priority


class SelectedRole(Model):
    id: Id
    organisation: NonEmptyStr
    title: NonEmptyStr
    location: str | None = None
    start_date: YearMonth
    end_date: YearMonth | None = None
    bullets: tuple[SelectedBullet, ...] = ()
    priority: Priority = 2
    """1 for load-bearing roles and roles kept for the role type."""
    load_bearing: bool = False
    during_education: bool = False
    sensitive: bool = False


class SelectedEducation(Model):
    id: Id
    institution: NonEmptyStr
    study_type: NonEmptyStr
    area: NonEmptyStr
    honours: str | None = None
    location: str | None = None
    start_date: YearMonth
    end_date: YearMonth | None = None
    courses: tuple[str, ...] = ()
    """The coursework subset for the role type; empty when there's none."""
    bullets: tuple[SelectedBullet, ...] = ()
    rules: tuple[str, ...] = ()


class SelectedProject(Model):
    id: Id
    name: NonEmptyStr
    keywords: tuple[str, ...] = ()
    url: str | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    bullets: tuple[SelectedBullet, ...] = ()
    priority: Priority = 2


class Contact(Model):
    name: NonEmptyStr
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    url: str | None = None
    profiles: tuple[SocialProfile, ...] = ()


class Selection(Model):
    """What one variant shows, in order, before it's fitted to a page and rendered."""

    variant_id: Id
    audience: Audience
    contact: Contact
    summary: str | None = None
    sections: tuple[Section, ...]
    education: tuple[SelectedEducation, ...] = ()
    work: tuple[SelectedRole, ...] = ()
    projects: tuple[SelectedProject, ...] = ()
    skills: tuple[SkillLine, ...] = ()
    max_pages: int | None
    template: NonEmptyStr
    notes: tuple[str, ...] = ()
    """What the variant asked for but the profile doesn't have, e.g. a skills preset."""


MASTER_VARIANT = Variant(id="master", name="Master (everything)", max_pages=None)
"""The only predefined variant: the full master document, with no page limit (OQ-6). One-page
resumes are tailored to each job posting (T-020), so no variant is predefined per role type."""


def select(profile: Profile, variant: Variant) -> Selection:
    """The content `variant` shows from `profile`."""
    notes: list[str] = []
    role_type = variant.role_type
    shown = _SHOWN[variant.audience]

    role_ids = {role.id for role in profile.work}
    notes.extend(
        f"the title choice for {choice.role_id} names no role"
        for choice in variant.titles
        if choice.role_id not in role_ids
    )
    work = tuple(
        _role(role, variant, shown, notes)
        for role in profile.work
        if role.load_bearing or role_type is None or role_type not in role.cut_for
    )
    projects = tuple(
        _project(project, variant, shown)
        for project in profile.projects
        if project.status == "active"
        and (role_type is None or role_type not in project.cut_for)
        and _has_tag(project.tags, variant.include_tags)
    )
    education = tuple(_education(entry, variant, shown, notes) for entry in profile.education)

    summary: str | None = None
    if variant.include_summary:
        summary = next((s.text for s in profile.summaries if s.role_type == role_type), None)
        if summary is None:
            notes.append(f"no summary for {role_type or 'the master variant'}, so none is shown")
    sections: list[Section] = [s for s in variant.section_order if s != "summary" or summary]
    if summary and "summary" not in sections:
        sections.insert(0, "summary")

    return Selection(
        variant_id=variant.id,
        audience=variant.audience,
        contact=_contact(profile.basics, variant, shown),
        summary=summary,
        sections=tuple(sections),
        education=education,
        work=work,
        projects=projects,
        skills=_skills(profile, role_type, notes),
        max_pages=variant.max_pages,
        template=variant.template,
        notes=tuple(notes),
    )


def _role(
    role: Role, variant: Variant, shown: frozenset[Visibility], notes: list[str]
) -> SelectedRole:
    kept = role.load_bearing or (
        variant.role_type is not None and variant.role_type in role.keep_for
    )
    return SelectedRole(
        id=role.id,
        organisation=role.name,
        title=_title(role, variant, notes),
        location=role.location,
        start_date=role.start_date,
        end_date=role.end_date,
        bullets=_bullets(role.highlights, variant, shown),
        priority=1 if kept else role.priority,
        load_bearing=role.load_bearing,
        during_education=role.during_education,
        sensitive=role.sensitivity == "sensitive",
    )


def _title(role: Role, variant: Variant, notes: list[str]) -> str:
    """The variant's choice if it's approved; otherwise the first approved variant (R9)."""
    default = role.title_variants[0] if role.title_variants else role.position
    chosen = next((choice.title for choice in variant.titles if choice.role_id == role.id), None)
    if chosen is None or chosen == default:
        return default
    if chosen in (role.position, *role.title_variants):
        return chosen
    notes.append(f'"{chosen}" isn\'t an approved title for {role.id}, so it shows "{default}"')
    return default


def _education(
    entry: Education, variant: Variant, shown: frozenset[Visibility], notes: list[str]
) -> SelectedEducation:
    courses = entry.courses
    if variant.role_type is not None:
        subset = next(
            (s for s in entry.coursework_subsets if s.role_type == variant.role_type), None
        )
        courses = subset.courses if subset else ()
        if subset is None and entry.courses:
            notes.append(f"no coursework subset for {variant.role_type} in {entry.id}")
    return SelectedEducation(
        id=entry.id,
        institution=entry.institution,
        study_type=entry.study_type,
        area=entry.area,
        honours=entry.honours,
        location=entry.location,
        start_date=entry.start_date,
        end_date=entry.end_date,
        courses=courses,
        bullets=_bullets(entry.highlights, variant, shown),
        rules=entry.rules,
    )


def _project(project: Project, variant: Variant, shown: frozenset[Visibility]) -> SelectedProject:
    kept = variant.role_type is not None and variant.role_type in project.keep_for
    return SelectedProject(
        id=project.id,
        name=project.name,
        keywords=project.keywords,
        url=project.url,
        start_date=project.start_date,
        end_date=project.end_date,
        bullets=_bullets(project.highlights, variant, shown),
        priority=1 if kept else project.priority,
    )


def _bullets(
    bullets: Sequence[Bullet], variant: Variant, shown: frozenset[Visibility]
) -> tuple[SelectedBullet, ...]:
    return tuple(
        SelectedBullet(
            id=bullet.id,
            text=bullet.text,
            strength=_strength(bullet, variant.role_type),
            priority=bullet.priority,
        )
        for bullet in bullets
        if bullet.status == "active"
        and bullet.visibility in shown
        and _has_tag(bullet.tags, variant.include_tags)
    )


def _strength(bullet: Bullet, role_type: Tag | None) -> Strength:
    for override in bullet.strength_overrides:
        if override.role_type == role_type:
            return override.strength
    return bullet.strength


def _has_tag(tags: Sequence[Tag], wanted: Sequence[Tag]) -> bool:
    """Untagged items always pass; tagged ones need one of the wanted tags, if any are listed."""
    return not wanted or not tags or bool(set(tags) & set(wanted))


def _skills(profile: Profile, role_type: Tag | None, notes: list[str]) -> tuple[SkillLine, ...]:
    """The role type's skills preset, or every skill grouped by category."""
    if role_type is not None:
        preset = next((p for p in profile.skill_presets if p.role_type == role_type), None)
        if preset is not None:
            return preset.lines
        notes.append(f"no skills preset for {role_type}, so every skill is listed")
    by_category: dict[str, list[str]] = {}
    for skill in profile.skills:
        by_category.setdefault(skill.category, []).append(skill.name)
    return tuple(
        SkillLine(label=label, skills=tuple(names)) for label, names in by_category.items()
    )


def _contact(basics: Basics, variant: Variant, shown: frozenset[Visibility]) -> Contact:
    hidden = set(variant.hidden)

    def show(field: str, visibility: Visibility = "public") -> bool:
        return field not in hidden and visibility in shown

    return Contact(
        name=basics.name,
        email=basics.email if show("email", basics.email_visibility) else None,
        phone=basics.phone if show("phone", basics.phone_visibility) else None,
        location=(
            _place(basics.location)
            if basics.location and show("location", basics.location_visibility)
            else None
        ),
        url=basics.url if show("url") else None,
        profiles=basics.profiles if show("profiles") else (),
    )


def _place(location: Location) -> str | None:
    parts = [part for part in (location.city, location.region, location.country_code) if part]
    return ", ".join(parts) or None
