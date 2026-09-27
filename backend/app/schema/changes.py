"""Change operations: the only way the master profile changes, whether proposed by AI or the owner.

Each operation addresses its target by ID and can cite the facts it came from.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from app.schema.common import Id, ItemStatus, Model, NonEmptyStr
from app.schema.profile import Bullet, Education, Project, Role, Skill, Swap


class OpBase(Model):
    fact_ids: tuple[Id, ...] = ()
    """The facts that justify this change."""
    rationale: str | None = None


class AddBullet(OpBase):
    op: Literal["add_bullet"] = "add_bullet"
    parent_id: Id
    """The role, education entry or project that gets the bullet."""
    bullet: Bullet
    position: int | None = Field(default=None, ge=0)
    """Where to insert it; None appends."""


class EditBullet(OpBase):
    op: Literal["edit_bullet"] = "edit_bullet"
    bullet_id: Id
    text: NonEmptyStr


class SetBulletStatus(OpBase):
    op: Literal["set_bullet_status"] = "set_bullet_status"
    bullet_id: Id
    status: ItemStatus
    reason: str | None = None

    @model_validator(mode="after")
    def benched_needs_reason(self) -> Self:
        if self.status == "benched" and not self.reason:
            raise ValueError("benching a bullet needs a reason")
        return self


class AddSwap(OpBase):
    op: Literal["add_swap"] = "add_swap"
    bullet_id: Id
    swap: Swap


class AddRole(OpBase):
    op: Literal["add_role"] = "add_role"
    role: Role


class AddEducation(OpBase):
    op: Literal["add_education"] = "add_education"
    education: Education


class AddProject(OpBase):
    op: Literal["add_project"] = "add_project"
    project: Project


class UpdateField(OpBase):
    op: Literal["update_field"] = "update_field"
    target_id: Id
    """An item or bullet ID, or `basics`."""
    field: NonEmptyStr
    """The field's JSON name, e.g. `endDate`."""
    value: JsonValue


class AddSkill(OpBase):
    op: Literal["add_skill"] = "add_skill"
    skill: Skill


class ResolveOpenQuestion(OpBase):
    op: Literal["resolve_open_question"] = "resolve_open_question"
    question_id: Id
    status: Literal["answered", "closed"] = "answered"


ChangeOp = Annotated[
    AddBullet
    | EditBullet
    | SetBulletStatus
    | AddSwap
    | AddRole
    | AddEducation
    | AddProject
    | UpdateField
    | AddSkill
    | ResolveOpenQuestion,
    Field(discriminator="op"),
]

change_op_adapter: TypeAdapter[ChangeOp] = TypeAdapter(ChangeOp)
"""Validates and serialises a single change operation."""
