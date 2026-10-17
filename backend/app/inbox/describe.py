"""What a proposed change does, in the owner's terms (FR-REV-1). Nothing here does I/O.

`describe` says what an operation changes against the profile it will apply to: a title, the
text before and after, the bullet or question it's about, and the outputs it affects. Which
outputs a change reaches follows from the rules that select content for them: a bullet that
isn't active shows nowhere (R8), one that isn't public stays off the portfolio page (FR-PRT-4),
the LinkedIn pack describes roles, and swaps are used only by tailored resumes (FR-TLR-3).

`word_diff` compares the text before and after, a word at a time.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, assert_never

from pydantic import ConfigDict, JsonValue

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
from app.schema.common import Model
from app.schema.profile import Bullet, Education, Profile, Project, Role

Output = Literal["master_resume", "tailored_resumes", "portfolio", "linkedin"]
"""The outputs a change can reach: the master resume and tailored resumes (FR-RES, FR-TLR), the
portfolio page (FR-PRT) and the LinkedIn pack (FR-LIN)."""

ALL_OUTPUTS: tuple[Output, ...] = ("master_resume", "tailored_resumes", "portfolio", "linkedin")
RESUMES_AND_PAGE: tuple[Output, ...] = ("master_resume", "tailored_resumes", "portfolio")

Item = Role | Education | Project

STATUS_TITLES = {
    "active": "Use a bullet again",
    "benched": "Bench a bullet",
    "planned": "Mark a bullet as planned",
}


class DiffPart(Model):
    """Words the texts share, or that one of them has. Its spaces count, so the parts rebuild
    each text exactly."""

    model_config = ConfigDict(str_strip_whitespace=False)

    kind: Literal["same", "removed", "added"]
    text: str


@dataclass(frozen=True)
class Description:
    title: str
    """E.g. `Reword a bullet under Engineer, Northwind Traders`."""
    context: str | None
    """What the change is about when it isn't the text itself, e.g. the bullet a swap is for."""
    before: str | None
    """The text the change replaces; None when it adds something."""
    after: str | None
    outputs: tuple[Output, ...]
    editable: bool
    """Whether the owner can edit the text it adds; edited text is verified again."""
    reworded: bool = False
    """Whether the text before and after are two wordings of one text, worth comparing a word at
    a time, rather than two values such as a bullet's status."""


def describe(op: ChangeOp, profile: Profile) -> Description:
    """What `op` does to `profile`. It describes an operation that can't apply, such as one on a
    bullet that's gone, as well as it can; applying it reports the problem."""
    match op:
        case AddBullet():
            parent = _item(profile, op.parent_id)
            bullet = op.bullet
            status = "" if bullet.status == "active" else f"{bullet.status} "
            return Description(
                title=f"Add a {status}bullet under {_label(parent, op.parent_id)}",
                context=None,
                before=None,
                after=bullet.text,
                outputs=_bullet_outputs(bullet, parent),
                editable=True,
            )
        case EditBullet():
            parent, bullet = _bullet(profile, op.bullet_id)
            return Description(
                title=f"Reword a bullet under {_label(parent, op.bullet_id)}",
                context=None,
                before=bullet.text if bullet else None,
                after=op.text,
                outputs=_bullet_outputs(bullet, parent) if bullet else (),
                editable=True,
                reworded=True,
            )
        case SetBulletStatus():
            parent, bullet = _bullet(profile, op.bullet_id)
            after = op.status if op.reason is None else f"{op.status}: {op.reason}"
            before = None
            outputs: tuple[Output, ...] = ()
            if bullet is not None:
                reason = bullet.status_reason
                before = bullet.status if reason is None else f"{bullet.status}: {reason}"
                shown = bullet.model_copy(update={"status": "active"})
                if "active" in (bullet.status, op.status):
                    outputs = _bullet_outputs(shown, parent)
            return Description(
                title=f"{STATUS_TITLES[op.status]} under {_label(parent, op.bullet_id)}",
                context=bullet.text if bullet else None,
                before=before,
                after=after,
                outputs=outputs,
                editable=False,
            )
        case AddSwap():
            parent, bullet = _bullet(profile, op.bullet_id)
            return Description(
                title=f"Add a swap for a bullet under {_label(parent, op.bullet_id)}",
                context=bullet.text if bullet else None,
                before=op.swap.replaces,
                after=op.swap.text,
                outputs=("tailored_resumes",),
                editable=False,
            )
        case AddRole():
            role = op.role
            return Description(
                title="Add a role",
                context=None,
                before=None,
                after=f"{_label(role, role.id)} ({_dates(role.start_date, role.end_date)})",
                outputs=ALL_OUTPUTS,
                editable=False,
            )
        case AddEducation():
            education = op.education
            dates = _dates(education.start_date, education.end_date)
            return Description(
                title="Add education",
                context=None,
                before=None,
                after=f"{_label(education, education.id)} ({dates})",
                outputs=RESUMES_AND_PAGE,
                editable=False,
            )
        case AddProject():
            project = op.project
            after = (
                project.name
                if not project.description
                else (f"{project.name}: {project.description}")
            )
            return Description(
                title="Add a project",
                context=None,
                before=None,
                after=after,
                outputs=RESUMES_AND_PAGE if project.status == "active" else (),
                editable=False,
            )
        case UpdateField():
            return _describe_update(op, profile)
        case AddSkill():
            skill = op.skill
            details = ", ".join(part for part in (skill.category, skill.tier) if part)
            return Description(
                title="Add a skill",
                context=None,
                before=None,
                after=f"{skill.name} ({details})",
                outputs=RESUMES_AND_PAGE,
                editable=False,
            )
        case ResolveOpenQuestion():
            question = next((q for q in profile.open_questions if q.id == op.question_id), None)
            verb = "Answer" if op.status == "answered" else "Close"
            return Description(
                title=f"{verb} an open question",
                context=question.question if question else op.question_id,
                before=question.status if question else None,
                after=op.status,
                outputs=(),
                editable=False,
            )
        case _:
            assert_never(op)


def word_diff(before: str, after: str) -> tuple[DiffPart, ...]:
    """`before` and `after` compared a word at a time: the words they share, and the words
    removed and added between them, in order. Each word goes with the spaces after it, so words
    line up by what they say rather than by the spaces between them."""
    old, new = _words(before), _words(after)
    parts: list[DiffPart] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            _add(parts, "same", old[i1:i2])
            continue
        _add(parts, "removed", old[i1:i2])
        _add(parts, "added", new[j1:j2])
    return tuple(parts)


def _words(text: str) -> list[str]:
    return re.findall(r"^\s+|\S+\s*", text)


def _add(
    parts: list[DiffPart], kind: Literal["same", "removed", "added"], words: Sequence[str]
) -> None:
    if not words:
        return
    text = "".join(words)
    if parts and parts[-1].kind == kind:
        parts[-1] = DiffPart(kind=kind, text=parts[-1].text + text)
    else:
        parts.append(DiffPart(kind=kind, text=text))


def _describe_update(op: UpdateField, profile: Profile) -> Description:
    if op.target_id == "basics":
        current = profile.basics.model_dump(mode="json", by_alias=True).get(op.field)
        target, outputs = "your basics", ALL_OUTPUTS
    elif (item := _item(profile, op.target_id)) is not None:
        current = item.model_dump(mode="json", by_alias=True).get(op.field)
        target = _label(item, op.target_id)
        outputs = ALL_OUTPUTS if isinstance(item, Role) else RESUMES_AND_PAGE
    else:
        parent, bullet = _bullet(profile, op.target_id)
        current = bullet.model_dump(mode="json", by_alias=True).get(op.field) if bullet else None
        target = f"a bullet under {_label(parent, op.target_id)}"
        outputs = _bullet_outputs(bullet, parent) if bullet else ()
    return Description(
        title=f"Change {op.field} of {target}",
        context=None,
        before=_text(current),
        after=_text(op.value),
        outputs=outputs,
        editable=False,
        reworded=isinstance(current, str) and isinstance(op.value, str),
    )


def _item(profile: Profile, item_id: str) -> Item | None:
    items: list[Item] = [*profile.work, *profile.education, *profile.projects]
    return next((item for item in items if item.id == item_id), None)


def _bullet(profile: Profile, bullet_id: str) -> tuple[Item | None, Bullet | None]:
    items: list[Item] = [*profile.work, *profile.education, *profile.projects]
    for item in items:
        for bullet in item.highlights:
            if bullet.id == bullet_id:
                return item, bullet
    return None, None


def item_label(item: Item) -> str:
    """What the owner calls a role, education entry or project, e.g. `Engineer, Northwind
    Traders`, `Bachelor of Science in Computer Science, Springfield State University` or
    `Recipe Box`."""
    match item:
        case Role():
            return f"{item.position}, {item.name}"
        case Education():
            return f"{item.study_type} in {item.area}, {item.institution}"
        case Project():
            return item.name


def _label(item: Item | None, fallback: str) -> str:
    return fallback if item is None else item_label(item)


def _bullet_outputs(bullet: Bullet, parent: Item | None) -> tuple[Output, ...]:
    if bullet.status != "active" or bullet.visibility == "private":
        return ()
    outputs: list[Output] = ["master_resume", "tailored_resumes"]
    if bullet.visibility == "public":
        outputs.append("portfolio")
    if isinstance(parent, Role):
        outputs.append("linkedin")
    return tuple(outputs)


def _dates(start: str | None, end: str | None) -> str:
    return f"{start or '?'} - {end or 'now'}"


def _text(value: JsonValue) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)
