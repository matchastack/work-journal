"""The resume writer: facts become a bullet in the resume/HR style (FR-WRT-1, FR-WRT-4).

A standard-tier prompt writes one bullet from facts, under a role, education entry or project:
a new bullet, or a new version of one the item has. The profile goes in the cached context,
without its contact details or its benched and planned bullets, so bullets written for the same
profile share it. The request gives the facts, the item and, for an edit, the bullet it replaces.

Every bullet goes through the verifier (`app/validate/verifier.py`) before anyone sees it. One
that fails is written once more, with the failed attempt and the verifier's findings, and is
flagged if it fails again (FR-FID-7). The result is the verifier's report, whose text is the
bullet. The prompt holds fact text, so nothing else from the writing is kept or logged.
"""

import json
import re
from collections.abc import Sequence

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model, NonEmptyStr
from app.schema.fact import Fact
from app.schema.profile import Bullet, Profile
from app.schema.verification import VerifierReport
from app.validate.claims import facts_json
from app.validate.verifier import Verifier

PROMPT_NAME = "bullet_writing"
PROMPT_VERSION = 1

_BULLET_SIGN = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
"""A list marker the model may put before the bullet, such as `- ` or `1. `."""


class WrittenBullet(Model):
    text: NonEmptyStr


def write_bullet(
    facts: Sequence[Fact],
    profile: Profile,
    client: LLMClient,
    *,
    entry_id: str,
    edit: str | None = None,
    avoid: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> VerifierReport:
    """Write `facts` as a bullet under `entry_id`, the ID of a role, education entry or project
    in `profile`, and verify it.

    With `edit`, the ID of one of the item's bullets, the bullet is a new version of that one;
    `facts` must then include the facts it was written from, or its claims fail the check.
    `avoid` is the owner's list of words to avoid. Raises `ValueError` for no facts or an unknown
    item or bullet.
    """
    if not facts:
        raise ValueError("a bullet is written from at least one fact")
    current = None
    if edit is not None:
        parent, current = locate_bullet(profile, edit)
        if parent != entry_id:
            raise ValueError(f"bullet {edit!r} is under {parent!r}, not {entry_id!r}")
    verifier = Verifier(
        client=client,
        profile=profile,
        facts=facts,
        entry_id=entry_id,
        replaces=edit,
        avoid=avoid,
    )
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    context = writing_context(profile)
    attempts: list[str] = []

    def write(feedback: str | None) -> str:
        variables = {
            "item": _item(profile, entry_id),
            "facts": facts_json(facts),
            "current": current.text if current else "none",
            "avoid": _listed(avoid),
            "attempt": attempts[-1] if feedback and attempts else "none",
            "problems": feedback or "none",
        }
        result = client.call(
            "bullet_writing", prompt, WrittenBullet, variables=variables, context=context
        )
        text = _BULLET_SIGN.sub("", result.output.text)
        attempts.append(text)
        return text

    return verifier.write(write)


def writing_context(profile: Profile) -> str:
    """The profile as the writer sees it, as JSON: each item with its active bullets, the skills
    catalogue and the gaps list. Contact details and benched or planned bullets are left out."""
    data = {
        "roles": [
            {
                "id": role.id,
                "organisation": role.name,
                "title": role.position,
                "startDate": role.start_date,
                "endDate": role.end_date,
                **({"sensitive": True} if role.sensitivity == "sensitive" else {}),
                "honestyBoundaries": list(role.honesty_boundaries),
                "bullets": _active(role.highlights),
            }
            for role in profile.work
        ],
        "education": [
            {
                "id": entry.id,
                "institution": entry.institution,
                "studyType": entry.study_type,
                "area": entry.area,
                "bullets": _active(entry.highlights),
            }
            for entry in profile.education
        ],
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "keywords": list(project.keywords),
                "status": project.status,
                "bullets": _active(project.highlights),
            }
            for project in profile.projects
        ],
        "skills": [skill.name for skill in profile.skills],
        "gaps": list(profile.skill_gaps),
    }
    return "The person's profile:\n" + json.dumps(data, indent=2, ensure_ascii=False)


def locate_bullet(profile: Profile, bullet_id: str) -> tuple[str, Bullet]:
    """The bullet `bullet_id`, and the ID of the role, education entry or project it's under."""
    for item in (*profile.work, *profile.education, *profile.projects):
        for bullet in item.highlights:
            if bullet.id == bullet_id:
                return item.id, bullet
    raise ValueError(f"the profile has no bullet {bullet_id!r}")


def _item(profile: Profile, entry_id: str) -> str:
    if any(role.id == entry_id for role in profile.work):
        return f'role "{entry_id}"'
    if any(entry.id == entry_id for entry in profile.education):
        return f'education entry "{entry_id}"'
    return f'project "{entry_id}"'


def _active(bullets: Sequence[Bullet]) -> list[str]:
    return [bullet.text for bullet in bullets if bullet.status == "active"]


def _listed(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "none"
