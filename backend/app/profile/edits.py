"""The owner's edits on the Profile page, as change operations (FR-PRF-11). Nothing here does I/O.

Each form sends every field it edits, all at once, with the version the page showed
(`base_version`). Every field is required, and null clears an optional one, so a field left out
can't be mistaken for one cleared. Blank optional text counts as cleared too.

Only the fields that changed become operations, so a save that changes nothing makes none:
- basics, roles, education entries and projects get an `UpdateField` for each changed field;
- a bullet gets `EditBullet` for its text, `SetBulletStatus` for its status and reason, and
  `UpdateField` for its strength, verification, verification note and tags.

A reason belongs to a benched or planned status: switching to active clears it. Benching needs
one (FR-PRF-4).

The owner's own words aren't generated text, so the verifier doesn't check them. They still get
ASCII characters, as every string in a schema model does.

Items and bullets aren't added or removed here: a bullet is benched rather than deleted
(FR-PRF-4), and new bullets come from journal facts through /refresh, so that each one traces
back to a fact (G4).

The metadata that drives tailoring is edited a list at a time (T-043): a role's tailoring rules
(FR-PRF-5), a bullet's swaps (FR-WRT-5), an education entry's coursework and rules (FR-PRF-7),
and the profile's role types, skills and gaps (FR-PRF-6), skills presets (FR-PRF-9), summaries
(FR-PRF-8) and open questions (FR-PRF-10). The profile's own lists change by an `UpdateField` on
the `profile` target. An edit that names a role type, course or bullet the profile doesn't have
is refused (`InvalidEdit`), with what's wrong in words.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BeforeValidator, Field, JsonValue, StringConstraints, model_validator

from app.inbox.describe import item_label
from app.inbox.routes import BULLET_LIMIT
from app.profile_ops import PROFILE_TARGET, ProfileOpError
from app.schema.changes import ChangeOp, EditBullet, SetBulletStatus, UpdateField
from app.schema.common import Id, ItemStatus, Model, Tag, YearMonth
from app.schema.profile import (
    Bullet,
    Education,
    Location,
    OpenQuestion,
    Profile,
    Project,
    Role,
    Skill,
    SocialProfile,
    Strength,
    Verification,
)

NAME_LIMIT = 200
"""The longest name, title or other short field, in characters."""
URL_LIMIT = 500
TEXT_LIMIT = 2000
"""The longest summary or description."""
REASON_LIMIT = 500
"""The longest status reason or verification note."""
TAG_COUNT = 20
KEYWORD_COUNT = 50
PROFILE_COUNT = 20
LIST_COUNT = 20
"""The most title variants, honesty boundaries, rules, swaps or a swap's contexts."""
COURSE_COUNT = 100
ROLE_TYPE_COUNT = 30
"""The most role types, and so presets, summaries or coursework subsets."""
SKILL_COUNT = 300
GAP_COUNT = 100
LINE_COUNT = 20
"""The most lines in a skills preset."""
LINE_SKILL_COUNT = 50
"""The most skills on one line of a preset."""
BLOCKED_COUNT = 50
"""The most bullets one open question blocks."""


def _none_if_blank(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


def _blanks_to_none(value: object) -> object:
    """A mapping with its blank strings as None."""
    if not isinstance(value, Mapping):
        return value
    items = cast("Mapping[object, object]", value).items()
    return {key: _none_if_blank(item) for key, item in items}


def _location(value: object) -> object:
    """A location whose fields are all blank is no location."""
    if not isinstance(value, Mapping):
        return value
    items = cast("Mapping[object, object]", value).items()
    fields = {key: _none_if_blank(item) for key, item in items}
    return None if all(item is None for item in fields.values()) else fields


Name = Annotated[str, StringConstraints(min_length=1, max_length=NAME_LIMIT)]
ShortText = Annotated[
    Annotated[str, StringConstraints(max_length=NAME_LIMIT)] | None,
    BeforeValidator(_none_if_blank),
]
Url = Annotated[
    Annotated[str, StringConstraints(max_length=URL_LIMIT)] | None,
    BeforeValidator(_none_if_blank),
]
LongText = Annotated[
    Annotated[str, StringConstraints(max_length=TEXT_LIMIT)] | None,
    BeforeValidator(_none_if_blank),
]
Reason = Annotated[
    Annotated[str, StringConstraints(max_length=REASON_LIMIT)] | None,
    BeforeValidator(_none_if_blank),
]
Month = Annotated[YearMonth | None, BeforeValidator(_none_if_blank)]
Tags = Annotated[tuple[Tag, ...], Field(max_length=TAG_COUNT)]
Line = Annotated[str, StringConstraints(min_length=1, max_length=REASON_LIMIT)]
"""One entry of a list such as honesty boundaries, which may be a sentence."""
Names = Annotated[tuple[Name, ...], Field(max_length=LIST_COUNT)]
Lines = Annotated[tuple[Line, ...], Field(max_length=LIST_COUNT)]


def _check_dates(start: str | None, end: str | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("the end date is before the start date")


def _check_status(status: ItemStatus, reason: str | None) -> None:
    if status == "benched" and reason is None:
        raise ValueError("benching needs a reason")


class Edit(Model):
    """What every edit sends."""

    base_version: int = Field(ge=1)
    """The version the page showed. The edit is refused when the profile has moved on since."""


class BasicsEdit(Edit):
    name: Name
    label: ShortText
    email: ShortText
    phone: ShortText
    url: Url
    summary: LongText
    location: Annotated[Location | None, BeforeValidator(_location)]
    profiles: Annotated[
        tuple[Annotated[SocialProfile, BeforeValidator(_blanks_to_none)], ...],
        Field(max_length=PROFILE_COUNT),
    ]


class RoleEdit(Edit):
    name: Name
    """The organisation."""
    position: Name
    """The official title."""
    location: ShortText
    url: Url
    start_date: YearMonth
    end_date: Month
    """None while the role is current."""
    summary: LongText

    @model_validator(mode="after")
    def dates_in_order(self) -> Self:
        _check_dates(self.start_date, self.end_date)
        return self


class EducationEdit(Edit):
    institution: Name
    study_type: Name
    area: Name
    honours: ShortText
    location: ShortText
    url: Url
    start_date: YearMonth
    end_date: Month

    @model_validator(mode="after")
    def dates_in_order(self) -> Self:
        _check_dates(self.start_date, self.end_date)
        return self


class ProjectEdit(Edit):
    name: Name
    description: LongText
    keywords: Annotated[tuple[Name, ...], Field(max_length=KEYWORD_COUNT)]
    """The tech stack."""
    url: Url
    start_date: Month
    end_date: Month
    status: ItemStatus
    status_reason: Reason
    tags: Tags

    @model_validator(mode="after")
    def check_status_and_dates(self) -> Self:
        _check_status(self.status, self.status_reason)
        _check_dates(self.start_date, self.end_date)
        return self


class BulletEdit(Edit):
    text: Annotated[str, StringConstraints(min_length=1, max_length=BULLET_LIMIT)]
    status: ItemStatus
    status_reason: Reason
    """Why it's benched (required then), or a note on a planned one."""
    strength: Strength
    verification: Verification
    verification_note: Reason
    tags: Tags

    @model_validator(mode="after")
    def benched_needs_reason(self) -> Self:
        _check_status(self.status, self.status_reason)
        return self


class RoleRulesEdit(Edit):
    """A role's tailoring rules (FR-PRF-5)."""

    title_variants: Names
    """Approved titles to use instead of the official one."""
    load_bearing: bool
    during_education: bool
    keep_for: Tags
    """Role types it's always kept for."""
    cut_for: Tags
    """Role types it's cut for first."""
    sensitivity: Literal["normal", "sensitive"]
    honesty_boundaries: Lines
    """Things never to claim for the role."""

    @model_validator(mode="after")
    def kept_or_cut(self) -> Self:
        both = sorted(set(self.keep_for) & set(self.cut_for))
        if both:
            raise ValueError(f"a role type can't be both kept and cut: {', '.join(both)}")
        return self


class SwapEdit(Model):
    text: Annotated[str, StringConstraints(min_length=1, max_length=BULLET_LIMIT)]
    replaces: Reason
    """The part of the bullet it replaces; null for the whole bullet."""
    contexts: Names
    """Where it fits, e.g. a role type or the posting it was written for."""
    origin: Literal["imported", "approved"]
    """`imported` from the master resume, or `approved` by you; a new swap is approved."""


class SwapsEdit(Edit):
    """A bullet's swaps, the approved alternative phrasings (FR-WRT-5)."""

    swaps: Annotated[tuple[SwapEdit, ...], Field(max_length=LIST_COUNT)]


class CourseworkSubsetEdit(Model):
    role_type: Tag
    courses: Annotated[tuple[Name, ...], Field(min_length=1, max_length=COURSE_COUNT)]


class CourseworkEdit(Edit):
    """An education entry's coursework and rules (FR-PRF-7)."""

    courses: Annotated[tuple[Name, ...], Field(max_length=COURSE_COUNT)]
    """The full coursework list."""
    coursework_subsets: Annotated[
        tuple[CourseworkSubsetEdit, ...], Field(max_length=ROLE_TYPE_COUNT)
    ]
    """The courses to list for a role type, each from the full list."""
    rules: Lines
    """Rules such as "never print GPA"."""


class RoleTypeEdit(Model):
    id: Tag
    name: Name


class RoleTypesEdit(Edit):
    """The kinds of job you apply for, which presets, summaries and rules are kept by."""

    role_types: Annotated[tuple[RoleTypeEdit, ...], Field(max_length=ROLE_TYPE_COUNT)]


class SkillEdit(Model):
    name: Name
    """The preferred spelling."""
    previous_name: Name | None
    """Its name before this edit, so its notes stay with it; null for a new skill."""
    category: Name
    tier: Literal["strong", "working", "academic"] | None
    verify_before_shipping: bool
    aliases: Names
    """Other spellings to recognise and correct."""


class SkillsEdit(Edit):
    """Your skills and the gaps list (FR-PRF-6)."""

    skills: Annotated[tuple[SkillEdit, ...], Field(max_length=SKILL_COUNT)]
    skill_gaps: Annotated[tuple[Name, ...], Field(max_length=GAP_COUNT)]
    """Skills never to claim."""


class SkillLineEdit(Model):
    label: Name
    skills: Annotated[tuple[Name, ...], Field(min_length=1, max_length=LINE_SKILL_COUNT)]


class SkillPresetEdit(Model):
    role_type: Tag
    lines: Annotated[tuple[SkillLineEdit, ...], Field(min_length=1, max_length=LINE_COUNT)]


class SkillPresetsEdit(Edit):
    """The skills section for each role type (FR-PRF-9)."""

    skill_presets: Annotated[tuple[SkillPresetEdit, ...], Field(max_length=ROLE_TYPE_COUNT)]


class SummaryEdit(Model):
    role_type: Tag
    text: Annotated[str, StringConstraints(min_length=1, max_length=TEXT_LIMIT)]


class SummariesEdit(Edit):
    """A summary paragraph for each role type (FR-PRF-8), used only when a resume asks for one."""

    summaries: Annotated[tuple[SummaryEdit, ...], Field(max_length=ROLE_TYPE_COUNT)]


class OpenQuestionEdit(Edit):
    """An open question: what it asks, its status, and the bullets it blocks (FR-PRF-10)."""

    question: Line
    status: Literal["open", "answered", "closed"]
    bullet_ids: Annotated[tuple[Id, ...], Field(max_length=BLOCKED_COUNT)]
    """The bullets its answer would make stronger."""


@dataclass(frozen=True)
class Edited:
    """The operations an edit makes, and the summary of their change set."""

    ops: tuple[ChangeOp, ...]
    summary: str


class MissingTarget(LookupError):
    """The profile has nothing of that kind with the ID, e.g. no role with an education entry's."""


class InvalidEdit(ProfileOpError):
    """The edit doesn't fit the profile, e.g. it names a role type the profile doesn't have."""


def basics_changes(profile: Profile, edit: BasicsEdit) -> Edited:
    return Edited(_updates("basics", profile.basics, edit), "Edited your basics")


def role_changes(profile: Profile, role_id: str, edit: RoleEdit) -> Edited:
    role = _find(profile.work, role_id, "role")
    label = item_label(role.model_copy(update={"name": edit.name, "position": edit.position}))
    return Edited(_updates(role_id, role, edit), f"Edited the role {label}")


def education_changes(profile: Profile, education_id: str, edit: EducationEdit) -> Edited:
    education = _find(profile.education, education_id, "education entry")
    edited = education.model_copy(
        update={
            "institution": edit.institution,
            "study_type": edit.study_type,
            "area": edit.area,
        }
    )
    ops = _updates(education_id, education, edit)
    return Edited(ops, f"Edited the education entry {item_label(edited)}")


def project_changes(profile: Profile, project_id: str, edit: ProjectEdit) -> Edited:
    project = _find(profile.projects, project_id, "project")
    reason = _reason(edit.status, edit.status_reason, project.status, project.status_reason)
    ops = _updates(project_id, project, edit, statusReason=reason)
    return Edited(ops, f"Edited the project {edit.name}")


def bullet_changes(profile: Profile, bullet_id: str, edit: BulletEdit) -> Edited:
    parent, bullet = _find_bullet(profile, bullet_id)
    ops: list[ChangeOp] = []
    if edit.text != bullet.text:
        ops.append(EditBullet(bullet_id=bullet_id, text=edit.text))
    reason = _reason(edit.status, edit.status_reason, bullet.status, bullet.status_reason)
    if (edit.status, reason) != (bullet.status, bullet.status_reason):
        ops.append(SetBulletStatus(bullet_id=bullet_id, status=edit.status, reason=reason))
    current = bullet.model_dump(mode="json")
    fields = edit.model_dump(
        mode="json", include={"strength", "verification", "verification_note", "tags"}
    )
    ops += [
        UpdateField(target_id=bullet_id, field=field, value=value)
        for field, value in fields.items()
        if current[field] != value
    ]
    return Edited(tuple(ops), f"Edited a bullet under {item_label(parent)}")


def role_rules_changes(profile: Profile, role_id: str, edit: RoleRulesEdit) -> Edited:
    role = _find(profile.work, role_id, "role")
    _check_role_types(
        profile, [*edit.keep_for, *edit.cut_for], known=[*role.keep_for, *role.cut_for]
    )
    ops = _updates(role_id, role, edit)
    return Edited(ops, f"Edited the tailoring rules of the role {item_label(role)}")


def swaps_changes(profile: Profile, bullet_id: str, edit: SwapsEdit) -> Edited:
    parent, bullet = _find_bullet(profile, bullet_id)
    swaps = [swap.model_dump(mode="json") for swap in edit.swaps]
    current = [swap.model_dump(mode="json") for swap in bullet.swaps]
    ops = _replace(bullet_id, "swaps", current, swaps)
    return Edited(ops, f"Edited the swaps of a bullet under {item_label(parent)}")


def coursework_changes(profile: Profile, education_id: str, edit: CourseworkEdit) -> Edited:
    education = _find(profile.education, education_id, "education entry")
    _check_unique("coursework for", [subset.role_type for subset in edit.coursework_subsets])
    _check_unique("the course", edit.courses)
    _check_role_types(profile, [subset.role_type for subset in edit.coursework_subsets])
    courses = set(edit.courses)
    for subset in edit.coursework_subsets:
        missing = [course for course in subset.courses if course not in courses]
        if missing:
            raise InvalidEdit(
                f"the {subset.role_type} coursework lists {', '.join(missing)}, which "
                "the full coursework list doesn't have"
            )
    ops = _updates(education_id, education, edit)
    return Edited(ops, f"Edited the coursework and rules of {item_label(education)}")


def role_types_changes(profile: Profile, edit: RoleTypesEdit) -> Edited:
    ids = [role_type.id for role_type in edit.role_types]
    _check_unique("the role type", ids)
    for removed in sorted({role_type.id for role_type in profile.role_types} - set(ids)):
        uses = _role_type_uses(profile, removed)
        if uses:
            raise InvalidEdit(
                f"the role type {removed} is still used by {', '.join(uses)}: change those first"
            )
    value = [role_type.model_dump(mode="json") for role_type in edit.role_types]
    return Edited(_profile_update(profile, "roleTypes", value), "Edited your role types")


def skills_changes(profile: Profile, edit: SkillsEdit) -> Edited:
    _check_unique("the skill", [skill.name for skill in edit.skills], fold=True)
    _check_unique("the gap", edit.skill_gaps, fold=True)
    names = {skill.name.casefold() for skill in edit.skills}
    claimed = [gap for gap in edit.skill_gaps if gap.casefold() in names]
    if claimed:
        raise InvalidEdit(f"{', '.join(claimed)} can't be both a skill and a gap")
    notes = {skill.name.casefold(): skill.notes for skill in profile.skills}
    skills = [
        Skill(
            name=skill.name,
            category=skill.category,
            tier=skill.tier,
            verify_before_shipping=skill.verify_before_shipping,
            aliases=skill.aliases,
            notes=notes.get(skill.previous_name.casefold(), ()) if skill.previous_name else (),
        ).model_dump(mode="json")
        for skill in edit.skills
    ]
    ops = (
        *_profile_update(profile, "skills", skills),
        *_profile_update(profile, "skillGaps", list(edit.skill_gaps)),
    )
    return Edited(ops, "Edited your skills")


def skill_presets_changes(profile: Profile, edit: SkillPresetsEdit) -> Edited:
    role_types = [preset.role_type for preset in edit.skill_presets]
    _check_unique("a skills preset for", role_types)
    _check_role_types(profile, role_types)
    value = [preset.model_dump(mode="json") for preset in edit.skill_presets]
    return Edited(_profile_update(profile, "skillPresets", value), "Edited your skills presets")


def summaries_changes(profile: Profile, edit: SummariesEdit) -> Edited:
    role_types = [summary.role_type for summary in edit.summaries]
    _check_unique("a summary for", role_types)
    _check_role_types(profile, role_types)
    value = [summary.model_dump(mode="json") for summary in edit.summaries]
    return Edited(_profile_update(profile, "summaries", value), "Edited your summaries")


def new_question_changes(profile: Profile, edit: OpenQuestionEdit) -> Edited:
    """Add an open question, with an ID of its own, to the bullets it blocks."""
    taken = (
        {question.id for question in profile.open_questions}
        | {item.id for item in (*profile.work, *profile.education, *profile.projects)}
        | {bullet.id for bullet in profile.all_bullets()}
    )
    number = next(n for n in range(1, len(taken) + 2) if f"q_{n}" not in taken)
    question = OpenQuestion(id=f"q_{number}", question=edit.question, status=edit.status)
    questions = [*profile.open_questions, question]
    ops = (
        *_profile_update(profile, "openQuestions", [q.model_dump(mode="json") for q in questions]),
        *_needs_updates(profile, question.id, edit.bullet_ids),
    )
    return Edited(ops, "Added an open question")


def question_changes(profile: Profile, question_id: str, edit: OpenQuestionEdit) -> Edited:
    """Change an open question's wording, status or the bullets it blocks. The facts that
    answered it stay with it."""
    current = next((q for q in profile.open_questions if q.id == question_id), None)
    if current is None:
        raise MissingTarget(f"there's no open question {question_id} in your profile")
    edited = current.model_copy(update={"question": edit.question, "status": edit.status})
    questions = [edited if q.id == question_id else q for q in profile.open_questions]
    ops = (
        *_profile_update(profile, "openQuestions", [q.model_dump(mode="json") for q in questions]),
        *_needs_updates(profile, question_id, edit.bullet_ids),
    )
    if edit.status == current.status:
        summary = "Edited an open question"
    else:
        summary = {
            "open": "Reopened an open question",
            "answered": "Answered an open question",
            "closed": "Closed an open question",
        }[edit.status]
    return Edited(ops, summary)


def _profile_update(profile: Profile, field: str, value: list[Any]) -> tuple[ChangeOp, ...]:
    current: list[Any] = profile.model_dump(mode="json")[field]
    return _replace(PROFILE_TARGET, field, current, value)


def _replace(
    target_id: str, field: str, current: list[Any], value: list[Any]
) -> tuple[ChangeOp, ...]:
    """An `UpdateField` setting a list to `value`, unless it's that already."""
    return () if current == value else (UpdateField(target_id=target_id, field=field, value=value),)


def _needs_updates(
    profile: Profile, question_id: str, bullet_ids: Sequence[str]
) -> tuple[ChangeOp, ...]:
    """The changes that make exactly `bullet_ids` the bullets the question blocks."""
    wanted = set(bullet_ids)
    known = {bullet.id for bullet in profile.all_bullets()}
    unknown = sorted(wanted - known)
    if unknown:
        raise InvalidEdit(f"there's no bullet {', '.join(unknown)} in your profile")
    ops: list[ChangeOp] = []
    for bullet in profile.all_bullets():
        needs: list[JsonValue]
        blocked = question_id in bullet.needs
        if bullet.id in wanted and not blocked:
            needs = [*bullet.needs, question_id]
        elif bullet.id not in wanted and blocked:
            needs = [need for need in bullet.needs if need != question_id]
        else:
            continue
        ops.append(UpdateField(target_id=bullet.id, field="needs", value=needs))
    return tuple(ops)


def _check_unique(what: str, values: Iterable[str], *, fold: bool = False) -> None:
    seen: set[str] = set()
    for value in values:
        key = value.casefold() if fold else value
        if key in seen:
            raise InvalidEdit(f"{what} {value} is listed twice")
        seen.add(key)


def _check_role_types(
    profile: Profile, role_types: Iterable[str], *, known: Iterable[str] = ()
) -> None:
    """Refuse role types the profile doesn't define, other than those in `known`, which it
    already uses."""
    defined = {role_type.id for role_type in profile.role_types} | set(known)
    unknown = sorted({role_type for role_type in role_types if role_type not in defined})
    if unknown:
        raise InvalidEdit(
            f"{', '.join(unknown)} isn't one of your role types: add it under Role types first"
        )


def _role_type_uses(profile: Profile, role_type: str) -> list[str]:
    """What uses a role type, in words, e.g. `the backend summary`."""
    uses: list[str] = []
    if any(preset.role_type == role_type for preset in profile.skill_presets):
        uses.append(f"the {role_type} skills preset")
    if any(summary.role_type == role_type for summary in profile.summaries):
        uses.append(f"the {role_type} summary")
    for education in profile.education:
        if any(subset.role_type == role_type for subset in education.coursework_subsets):
            uses.append(f"the {role_type} coursework of {item_label(education)}")
    items: list[Role | Project] = [*profile.work, *profile.projects]
    for item in items:
        if role_type in item.keep_for or role_type in item.cut_for:
            uses.append(f"the tailoring rules of {item_label(item)}")
    if any(
        override.role_type == role_type
        for bullet in profile.all_bullets()
        for override in bullet.strength_overrides
    ):
        uses.append("a bullet's strength for it")
    return uses


def _updates(
    target_id: str, current: Model, edit: Edit, **fields: str | None
) -> tuple[ChangeOp, ...]:
    """An `UpdateField` for each field of `edit` that differs from `current`. `fields` replace
    edited values by their camelCase names."""
    before = current.model_dump(mode="json")
    after = edit.model_dump(mode="json", exclude={"base_version"}) | fields
    return tuple(
        UpdateField(target_id=target_id, field=field, value=value)
        for field, value in after.items()
        if before[field] != value
    )


def _reason(
    status: ItemStatus, reason: str | None, current_status: ItemStatus, current_reason: str | None
) -> str | None:
    """The reason to keep with `status`. An active item keeps none of its own: switching to
    active clears it, and an item already active keeps whatever it has, which the form doesn't
    show."""
    if status != "active":
        return reason
    return current_reason if current_status == "active" else None


def _find[T: Role | Education | Project](items: Sequence[T], item_id: str, kind: str) -> T:
    for item in items:
        if item.id == item_id:
            return item
    raise MissingTarget(f"there's no {kind} {item_id} in your profile")


def _find_bullet(profile: Profile, bullet_id: str) -> tuple[Role | Education | Project, Bullet]:
    items: list[Role | Education | Project] = [*profile.work, *profile.education, *profile.projects]
    for item in items:
        for bullet in item.highlights:
            if bullet.id == bullet_id:
                return item, bullet
    raise MissingTarget(f"there's no bullet {bullet_id} in your profile")
