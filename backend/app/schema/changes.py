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

Author = Literal["owner", "ai"]
"""Who made a change: the owner by hand, or the AI with the owner's approval (FR-PRF-2)."""
OpStatus = Literal["proposed", "accepted", "rejected"]
ChangeSetStatus = Literal["proposed", "applied", "rejected"]


class ReviewedOp(Model):
    """One operation in a change set, with the owner's decision on it (FR-REV-2)."""

    op: ChangeOp
    status: OpStatus = "proposed"
    reason: str | None = None
    """Why the owner rejected it. Rejection reasons become style notes (FR-REV-4)."""
    verifier_report: dict[str, JsonValue] | None = None
    """What the verifier found (FR-FID-8)."""


class ChangeSet(Model):
    """Operations proposed together and reviewed together. Its accepted operations are applied
    as one new profile version (FR-REV-3)."""

    author: Author
    ops: tuple[ReviewedOp, ...] = Field(min_length=1)
    summary: str | None = None
    base_version: int | None = Field(default=None, ge=1)
    """The profile version the operations were proposed against."""
    status: ChangeSetStatus = "proposed"
