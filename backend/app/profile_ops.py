"""Pure operations on the master profile: apply change operations and compare two profiles.

AI proposals and the owner's manual edits both go through `apply`, so they change the profile in
exactly the same way (FR-PRF-11). Nothing here does I/O.
"""

from collections.abc import Sequence
from typing import Any, assert_never

from pydantic import BaseModel, ValidationError

from app.schema.changes import (
    AddBullet,
    AddEducation,
    AddProject,
    AddRole,
    AddSkill,
    AddSwap,
    ChangeOp,
    EditBullet,
    ResolveOpenQuestion,
    SetBulletStatus,
    UpdateField,
)
from app.schema.profile import Basics, Bullet, Education, Profile, Project, Role

Json = dict[str, Any]

ITEM_SECTIONS: dict[str, type[BaseModel]] = {
    "work": Role,
    "education": Education,
    "projects": Project,
}
"""Profile sections whose entries are addressed by ID, and each entry's model."""

PROTECTED_FIELDS = frozenset({"id", "highlights"})
"""Fields UpdateField may not touch: identity, and bullets (use the bullet operations)."""


class ProfileOpError(ValueError):
    """An operation couldn't be applied. The profile is left unchanged."""


def apply(profile: Profile, ops: Sequence[ChangeOp]) -> Profile:
    """Apply `ops` in order and return the new profile.

    Atomic: if any operation fails, or the result isn't a valid profile, this raises
    `ProfileOpError` and nothing is applied. The input profile is never modified.
    """
    data = profile.model_dump(mode="json")
    for index, op in enumerate(ops, start=1):
        try:
            _apply_one(data, op)
        except ProfileOpError as error:
            raise ProfileOpError(f"operation {index} ({op.op}): {error}") from None
    try:
        return Profile.model_validate(data)
    except ValidationError as error:
        raise ProfileOpError(f"the changes would make the profile invalid: {error}") from error


def _apply_one(data: Json, op: ChangeOp) -> None:
    match op:
        case AddBullet():
            bullets: list[Json] = _find_item(data, op.parent_id)[0]["highlights"]
            position = len(bullets) if op.position is None else op.position
            if position > len(bullets):
                raise ProfileOpError(
                    f"position {position} is past the end of {op.parent_id!r} "
                    f"({len(bullets)} bullets)"
                )
            bullet = op.bullet.model_dump(mode="json")
            bullet["sources"] = _merge_ids(bullet["sources"], op.fact_ids)
            bullets.insert(position, bullet)
        case EditBullet():
            bullet = _find_bullet(data, op.bullet_id)
            bullet["text"] = op.text
            bullet["sources"] = _merge_ids(bullet["sources"], op.fact_ids)
        case SetBulletStatus():
            bullet = _find_bullet(data, op.bullet_id)
            bullet["status"] = op.status
            bullet["statusReason"] = op.reason
        case AddSwap():
            _find_bullet(data, op.bullet_id)["swaps"].append(op.swap.model_dump(mode="json"))
        case AddRole():
            data["work"].append(op.role.model_dump(mode="json"))
        case AddEducation():
            data["education"].append(op.education.model_dump(mode="json"))
        case AddProject():
            data["projects"].append(op.project.model_dump(mode="json"))
        case UpdateField():
            _update_field(data, op)
        case AddSkill():
            data["skills"].append(op.skill.model_dump(mode="json"))
        case ResolveOpenQuestion():
            question = _find_question(data, op.question_id)
            question["status"] = op.status
            question["answerFactIds"] = _merge_ids(question["answerFactIds"], op.fact_ids)
        case _:
            assert_never(op)


def _find_item(data: Json, item_id: str) -> tuple[Json, type[BaseModel]]:
    for section, model in ITEM_SECTIONS.items():
        for item in data[section]:
            if item["id"] == item_id:
                return item, model
    raise ProfileOpError(f"no role, education entry or project has ID {item_id!r}")


def _find_bullet(data: Json, bullet_id: str) -> Json:
    for section in ITEM_SECTIONS:
        for item in data[section]:
            for bullet in item["highlights"]:
                if bullet["id"] == bullet_id:
                    return bullet
    raise ProfileOpError(f"no bullet has ID {bullet_id!r}")


def _find_question(data: Json, question_id: str) -> Json:
    for question in data["openQuestions"]:
        if question["id"] == question_id:
            return question
    raise ProfileOpError(f"no open question has ID {question_id!r}")


def _update_field(data: Json, op: UpdateField) -> None:
    target: Json
    model: type[BaseModel]
    if op.target_id == "basics":
        target, model = data["basics"], Basics
    else:
        try:
            target, model = _find_item(data, op.target_id)
        except ProfileOpError:
            try:
                target, model = _find_bullet(data, op.target_id), Bullet
            except ProfileOpError:
                raise ProfileOpError(f"nothing in the profile has ID {op.target_id!r}") from None
    editable = {field.alias or name for name, field in model.model_fields.items()}
    if op.field not in editable - PROTECTED_FIELDS:
        raise ProfileOpError(f"{op.target_id!r} has no editable field {op.field!r}")
    target[op.field] = op.value


def _merge_ids(existing: Sequence[str], new: Sequence[str]) -> list[str]:
    """Append `new` IDs that aren't already present, keeping order."""
    return list(dict.fromkeys([*existing, *new]))
