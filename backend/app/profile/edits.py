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
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Self, cast

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from app.inbox.describe import item_label
from app.inbox.routes import BULLET_LIMIT
from app.schema.changes import ChangeOp, EditBullet, SetBulletStatus, UpdateField
from app.schema.common import ItemStatus, Model, Tag, YearMonth
from app.schema.profile import (
    Bullet,
    Education,
    Location,
    Profile,
    Project,
    Role,
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


@dataclass(frozen=True)
class Edited:
    """The operations an edit makes, and the summary of their change set."""

    ops: tuple[ChangeOp, ...]
    summary: str


class MissingTarget(LookupError):
    """The profile has nothing of that kind with the ID, e.g. no role with an education entry's."""


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
