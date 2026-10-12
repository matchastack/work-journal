"""Reviewing a change set: each operation in words, and what a rejection takes with it (FR-REV).

The owner accepts or rejects each operation (FR-REV-2). An operation that needs a rejected one,
such as a bullet under a new role the owner rejected, can't be applied, so it's rejected too.
The accepted operations are then applied together as one new profile (FR-REV-3).
"""

from collections.abc import Sequence

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
from app.schema.profile import Profile
from app.schema.verification import VerifierReport


def describe(op: ChangeOp, profile: Profile) -> list[str]:
    """What `op` does to `profile`, in a few lines of plain text."""
    match op:
        case AddBullet():
            return [f"New bullet under {_item(profile, op.parent_id)}:", f"  {op.bullet.text}"]
        case EditBullet():
            current = _bullet_text(profile, op.bullet_id)
            lines = [f"New version of bullet {op.bullet_id}:"]
            if current is not None:
                lines.append(f"  - {current}")
            return [*lines, f"  + {op.text}"]
        case SetBulletStatus():
            reason = f" ({op.reason})" if op.reason else ""
            return [f"Make bullet {op.bullet_id} {op.status}{reason}"]
        case AddSwap():
            return [f"New phrasing for bullet {op.bullet_id}:", f"  {op.swap.text}"]
        case AddRole():
            role = op.role
            return [
                f"New role {role.id}: {role.position} at {role.name}, "
                f"{role.start_date} to {role.end_date or 'now'}"
            ]
        case AddEducation():
            entry = op.education
            return [f"New education entry {entry.id}: {entry.study_type}, {entry.institution}"]
        case AddProject():
            project = op.project
            line = f"New project {project.id}: {project.name}"
            if project.keywords:
                line += f" ({', '.join(project.keywords)})"
            return [line]
        case UpdateField():
            return [f"Set {op.field} of {op.target_id} to {op.value}"]
        case AddSkill():
            skill = op.skill
            check = ", marked verify-before-shipping" if skill.verify_before_shipping else ""
            return [f"New skill {skill.name} in {skill.category}{check}"]
        case ResolveOpenQuestion():
            question = next((q for q in profile.open_questions if q.id == op.question_id), None)
            asked = f": {question.question}" if question else ""
            return [f"Mark open question {op.question_id} {op.status}{asked}"]


def needs(op: ChangeOp, ops: Sequence[ChangeOp]) -> int | None:
    """The index in `ops` of an operation that `op` can't be applied without, if there's one: the
    new role or project a bullet goes under, or that a change updates."""
    match op:
        case AddBullet():
            target = op.parent_id
        case UpdateField():
            target = op.target_id
        case _:
            return None
    for index, other in enumerate(ops):
        if (isinstance(other, AddRole) and other.role.id == target) or (
            isinstance(other, AddProject) and other.project.id == target
        ):
            return index
    return None


def report_lines(report: VerifierReport) -> list[str]:
    """What the verifier found: the findings, any figures code derived, then the verdict."""
    lines = [
        f"{finding.severity:<7}  {finding.check:<7}  {finding.message}"
        for finding in report.findings
    ]
    lines += [
        f"Derived {derivation.quantity} from the facts: {derivation.formula}"
        for derivation in report.derivations
    ]
    if not report.passed:
        lines.append("Flagged: it didn't pass the checks, so don't use it as it is.")
    elif report.attempts > 1:
        lines.append("Passed the checks when written the second time.")
    else:
        lines.append("Passed the checks.")
    return lines


def _item(profile: Profile, item_id: str) -> str:
    for role in profile.work:
        if role.id == item_id:
            return f"{item_id} ({role.position} at {role.name})"
    for entry in profile.education:
        if entry.id == item_id:
            return f"{item_id} ({entry.institution})"
    for project in profile.projects:
        if project.id == item_id:
            return f"{item_id} ({project.name})"
    return item_id


def _bullet_text(profile: Profile, bullet_id: str) -> str | None:
    return next((b.text for b in profile.all_bullets() if b.id == bullet_id), None)
