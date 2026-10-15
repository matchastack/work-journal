"""The writer: facts become a bullet in one of three styles (FR-WRT-1..5).

- resume: the resume/HR style (FR-WRT-1);
- linkedin: the first person, slightly narrative, for a LinkedIn description (FR-WRT-2);
- posting: a resume bullet in a job posting's vocabulary, where it is true (FR-WRT-3).

A standard-tier prompt for the style writes one text from facts, under a role, education entry
or project: a new one, or a new version of a bullet the item has. The profile goes in the cached
context, without its contact details or its benched and planned bullets, so texts written for the
same profile share it. `apply_swap` works an approved swap into a bullet the same way, and
`swap_op` saves a phrasing the owner approves as a swap on its bullet (FR-WRT-5).

Every text goes through the verifier (`app/validate/verifier.py`) before anyone sees it, with the
owner's words to avoid in every style (FR-WRT-4). One that fails is written once more, with the
failed attempt and the verifier's findings, and is flagged if it fails again (FR-FID-7). The
result is the verifier's report, whose text is the bullet. The prompt holds fact text, so nothing
else from the writing is kept or logged.
"""

import json
import re
from collections.abc import Sequence
from typing import Literal

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.changes import AddSwap
from app.schema.common import Model, NonEmptyStr
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Bullet, Profile, Swap
from app.schema.verification import VerifierReport
from app.validate.claims import facts_json
from app.validate.verifier import Verifier, Writer

Style = Literal["resume", "linkedin", "posting"]

PROMPTS: dict[Style, tuple[str, int]] = {
    "resume": ("bullet_writing", 1),
    "linkedin": ("linkedin_writing", 1),
    "posting": ("posting_writing", 1),
}
"""The prompt, and its version, that writes each style."""
SWAP_PROMPT = ("swap_application", 1)

LINKEDIN_CONTEXT = "LinkedIn"
"""Where a swap saved from the LinkedIn style fits. Such a swap never goes on a resume."""

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
    style: Style = "resume",
    posting: JobPosting | None = None,
    avoid: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> VerifierReport:
    """Write `facts` in `style` under `entry_id`, the ID of a role, education entry or project
    in `profile`, and verify it.

    With `edit`, the ID of one of the item's bullets, the text is a new version of that one: in
    the resume style it replaces the bullet, and in the others it can be saved as a swap on it.
    `facts` must then include the facts it was written from, or its claims fail the check. The
    posting style needs the parsed `posting`, and the others take none. `avoid` is the owner's
    list of words to avoid. Raises `ValueError` for no facts or an unknown item or bullet.
    """
    if not facts:
        raise ValueError("a bullet is written from at least one fact")
    if (style == "posting") != (posting is not None):
        raise ValueError("the posting style needs a job posting, and only it takes one")
    current = None
    if edit is not None:
        parent, current = locate_bullet(profile, edit)
        if parent != entry_id:
            raise ValueError(f"bullet {edit!r} is under {parent!r}, not {entry_id!r}")
    verifier = Verifier(
        client=client,
        profile=profile,
        facts=facts,
        kind="linkedin_position" if style == "linkedin" else "resume_bullet",
        entry_id=entry_id,
        replaces=edit,
        posting=posting,
        avoid=avoid,
    )
    variables = {
        "item": _item(profile, entry_id),
        "facts": facts_json(facts),
        "current": current.text if current else "none",
        "posting": _posting_json(posting) if posting else "none",
        "avoid": _listed(avoid),
    }
    prompt = prompt or load_prompt(*PROMPTS[style])
    return verifier.write(_writer(client, prompt, variables, writing_context(profile)))


def apply_swap(
    facts: Sequence[Fact],
    profile: Profile,
    client: LLMClient,
    *,
    bullet_id: str,
    swap: Swap,
    posting: JobPosting | None = None,
    avoid: Sequence[str] = (),
    prompt: Prompt | None = None,
) -> VerifierReport:
    """Work `swap` into the bullet `bullet_id`, and verify the result as a resume bullet that
    replaces it, against `posting` when it's for one.

    `facts` must include the facts the bullet was written from. An approved swap that replaces
    the whole bullet is only checked; any other is worked in by the standard-tier model, which
    changes as little else as it can. Raises `ValueError` for no facts, an unknown bullet, or a
    swap saved for LinkedIn.
    """
    if not facts:
        raise ValueError("a swap is checked against at least one fact")
    if LINKEDIN_CONTEXT in swap.contexts:
        raise ValueError("a swap written for LinkedIn doesn't go on a resume")
    entry_id, bullet = locate_bullet(profile, bullet_id)
    verifier = Verifier(
        client=client,
        profile=profile,
        facts=facts,
        entry_id=entry_id,
        replaces=bullet_id,
        posting=posting,
        avoid=avoid,
    )
    if swap.replaces is None and swap.origin == "approved":
        return verifier.check(swap.text)
    variables = {
        "item": _item(profile, entry_id),
        "bullet": bullet.text,
        "swap": swap.text,
        "replaces": swap.replaces or "not given",
        "facts": facts_json(facts),
        "avoid": _listed(avoid),
    }
    prompt = prompt or load_prompt(*SWAP_PROMPT)
    return verifier.write(_writer(client, prompt, variables, writing_context(profile)))


def swap_op(
    bullet_id: str,
    report: VerifierReport,
    *,
    style: Style,
    posting: JobPosting | None = None,
) -> AddSwap:
    """The change that saves the phrasing in `report`, which the owner approved, as a swap on
    the bullet `bullet_id` (FR-WRT-5). It replaces the whole bullet, in the setting the style
    wrote it for: LinkedIn, or the posting's title and company."""
    contexts: tuple[str, ...] = ()
    if style == "linkedin":
        contexts = (LINKEDIN_CONTEXT,)
    elif posting is not None:
        contexts = (f"{posting.title} at {posting.company}" if posting.company else posting.title,)
    swap = Swap(text=report.text, contexts=contexts, origin="approved")
    return AddSwap(bullet_id=bullet_id, swap=swap, fact_ids=report.fact_ids)


def _writer(client: LLMClient, prompt: Prompt, variables: dict[str, str], context: str) -> Writer:
    """Calls `prompt` with `variables`; for a rewrite, also the last attempt and its problems."""
    attempts: list[str] = []

    def write(feedback: str | None) -> str:
        result = client.call(
            "bullet_writing",
            prompt,
            WrittenBullet,
            variables={
                **variables,
                "attempt": attempts[-1] if feedback and attempts else "none",
                "problems": feedback or "none",
            },
            context=context,
        )
        text = _BULLET_SIGN.sub("", result.output.text)
        attempts.append(text)
        return text

    return write


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


def _posting_json(posting: JobPosting) -> str:
    data = posting.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"url"})
    return json.dumps(data, indent=2, ensure_ascii=False)


def _active(bullets: Sequence[Bullet]) -> list[str]:
    return [bullet.text for bullet in bullets if bullet.status == "active"]


def _listed(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "none"
