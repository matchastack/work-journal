"""A tailoring draft: a resume tailored on the Tailor page, which the owner adjusts before
downloading it (T-045, FR-TLR-3, FR-TLR-7, R2, R3).

The tailoring job keeps what the models chose and wrote (`Tailoring`): the heavy-tier plan and
the verified wording. The owner's choices (`DraftChoices`) then shape the resume without another
model call (`draft_resume`):
- each reworded bullet uses its own words, the swap worked in for the posting, or its new
  phrasing once the owner has approved it (approving saves it as a swap, FR-WRT-5);
- each skill marked verify-before-shipping that the draft lists or names is confirmed or
  dropped (R3). Until then it stays listed, so the owner sees it, and the resume can't be
  downloaded. A dropped skill leaves the skills lines; text that still names it breaks R3, so
  the resume is marked not to send (R2).

The resume rules run on the result before it's fitted to a page, as `wj tailor` runs them. Every
function here is pure: the same draft and choices give the same resume, and so the same render
key, which is how a render is found again instead of compiled.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from app.llm.client import LLMClient
from app.schema.common import Model, Tag
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.selection import Selection
from app.tailoring.tailor import (
    Phrasing,
    TailoringPlan,
    choose,
    plan_resume,
    posting_spellings,
    reword,
    with_texts,
)
from app.validate.lint import LintReport, check_sendable, verify_before_shipping_uses

BulletUse = Literal["own", "swap", "phrasing"]
"""A reworded bullet's words: its own, the swap worked in, or its new phrasing."""
SkillDecision = Literal["confirmed", "dropped"]


class Tailoring(Model):
    """What the tailoring job chose and wrote for a draft. It holds resume text and verifier
    reports, never fact text."""

    plan: TailoringPlan
    """The heavy-tier model's choices, before code checks them: checked again each time."""
    swaps: tuple[Phrasing, ...] = ()
    """Approved swaps worked into bullets, all of which passed the verifier."""
    proposals: tuple[Phrasing, ...] = ()
    """New phrasings that passed the verifier, waiting for the owner's approval."""
    notes: tuple[str, ...] = ()
    """What code left out of the wording, and why."""


class DraftChoices(Model):
    """The owner's choices on a draft."""

    uses: dict[str, BulletUse] = Field(default_factory=dict[str, BulletUse])
    """Each reworded bullet's words, by bullet ID. A bullet not listed keeps its own."""
    approved: tuple[str, ...] = ()
    """The bullets whose new phrasings the owner approved, and saved as swaps."""
    skills: dict[str, SkillDecision] = Field(default_factory=dict[str, SkillDecision])
    """The owner's decision on each skill marked verify-before-shipping, by the skill's name."""


class ChoiceError(ValueError):
    """The choice doesn't fit the draft, such as words a bullet doesn't have."""


@dataclass(frozen=True)
class DraftSkill:
    """A skill marked verify-before-shipping that the draft lists or names."""

    name: str
    decision: SkillDecision | None
    locations: tuple[str, ...]
    """Where the resume lists or names it now: none once it's dropped, unless text names it."""


@dataclass(frozen=True)
class RewordedBullet:
    """A bullet with new words to choose from."""

    id: str
    under: str
    """The role's organisation or the project's name."""
    original: str
    use: BulletUse
    swap: Phrasing | None
    phrasing: Phrasing | None
    approved: bool


@dataclass(frozen=True)
class DraftResume:
    selection: Selection
    """What the resume shows with the owner's choices, before it's fitted to one page."""
    role_type: Tag | None
    notes: tuple[str, ...]
    """What code changed in or left out of the plan, and why."""
    used: tuple[Phrasing, ...]
    """The new words the resume uses."""
    bullets: tuple[RewordedBullet, ...]
    """The reworded bullets the resume shows, in its order."""
    skills: tuple[DraftSkill, ...]
    rules: LintReport
    """The resume rules' findings. An error doesn't stop the PDF, but the resume mustn't be sent
    until it's fixed (R2)."""

    @property
    def undecided(self) -> tuple[str, ...]:
        """The skills to confirm or drop before the resume can be downloaded (R3)."""
        return tuple(skill.name for skill in self.skills if skill.decision is None)


def draft_tailoring(
    profile: Profile,
    posting: JobPosting,
    client: LLMClient,
    *,
    facts: Sequence[Fact] = (),
    avoid: Sequence[str] = (),
) -> Tailoring:
    """Tailor a draft as `tailor` does: the heavy-tier plan, then the wording, each new text
    checked by the verifier. Skills marked verify-before-shipping wait for the owner's decision,
    so which ones are listed is chosen later (`draft_resume`)."""
    plan = plan_resume(profile, posting, client)
    reworded = reword(
        profile, posting, client, choose(profile, posting, plan), facts=facts, avoid=avoid
    )
    return Tailoring(
        plan=plan, swaps=reworded.swaps, proposals=reworded.proposals, notes=reworded.notes
    )


def first_choices(tailoring: Tailoring) -> DraftChoices:
    """A new draft's choices: each swap worked in, and the new phrasings waiting for approval."""
    return DraftChoices(uses={swap.bullet_id: "swap" for swap in tailoring.swaps})


def draft_resume(
    profile: Profile, posting: JobPosting, tailoring: Tailoring, choices: DraftChoices
) -> DraftResume:
    """The resume the draft makes with the owner's choices, from `profile`, the version it was
    tailored from."""
    vbs = [skill.name for skill in profile.skills if skill.verify_before_shipping]
    dropped = [name for name, decision in choices.skills.items() if decision == "dropped"]
    confirmed = [name for name, decision in choices.skills.items() if decision == "confirmed"]
    chosen = choose(profile, posting, tailoring.plan, confirmed=vbs, dropped=dropped)
    used = _used(tailoring, choices)
    texts = {phrasing.bullet_id: phrasing.text for phrasing in used}
    selection = with_texts(chosen.selection, texts)
    shown = verify_before_shipping_uses(selection, profile)
    candidates = shown
    if dropped:
        everything = choose(profile, posting, tailoring.plan, confirmed=vbs).selection
        candidates = verify_before_shipping_uses(with_texts(everything, texts), profile)
    skills = tuple(
        DraftSkill(name=name, decision=choices.skills.get(name), locations=shown.get(name, ()))
        for name in vbs
        if name in candidates or name in shown
    )
    rules = check_sendable(
        selection, profile, confirmed, posting_spellings=posting_spellings(posting)
    )
    return DraftResume(
        selection=selection,
        role_type=chosen.role_type,
        notes=(*chosen.notes, *tailoring.notes),
        used=used,
        bullets=_reworded(selection, tailoring, choices),
        skills=skills,
        rules=rules,
    )


def use_words(
    choices: DraftChoices, tailoring: Tailoring, bullet_id: str, use: BulletUse
) -> DraftChoices:
    """The choices with `bullet_id` using `use`. Raises `ChoiceError` when the bullet has no
    such words, or its new phrasing isn't approved yet."""
    swap = _find(tailoring.swaps, bullet_id)
    phrasing = _find(tailoring.proposals, bullet_id)
    if swap is None and phrasing is None:
        raise ChoiceError(f"{bullet_id} has no new words to choose from")
    if use == "swap" and swap is None:
        raise ChoiceError(f"no swap was worked into {bullet_id}")
    if use == "phrasing" and (phrasing is None or bullet_id not in choices.approved):
        raise ChoiceError(f"approve the new phrasing of {bullet_id} before you use it")
    return choices.model_copy(update={"uses": {**choices.uses, bullet_id: use}})


def proposal(tailoring: Tailoring, bullet_id: str) -> Phrasing:
    """The new phrasing offered for `bullet_id`. Raises `ChoiceError` when there's none."""
    found = _find(tailoring.proposals, bullet_id)
    if found is None:
        raise ChoiceError(f"there's no new phrasing of {bullet_id} to approve")
    return found


def approve(choices: DraftChoices, bullet_id: str) -> DraftChoices:
    """The choices once the owner approved the new phrasing of `bullet_id`, which is then
    used."""
    approved = choices.approved if bullet_id in choices.approved else (*choices.approved, bullet_id)
    uses = {**choices.uses, bullet_id: "phrasing"}
    return choices.model_copy(update={"approved": approved, "uses": uses})


def decide_skill(
    choices: DraftChoices, profile: Profile, skill: str, decision: SkillDecision | None
) -> DraftChoices:
    """The choices with the owner's decision on `skill`, a skill marked verify-before-shipping
    named as the catalogue or an alias spells it in any case; None takes the decision back.
    Raises `ChoiceError` for any other skill."""
    wanted = skill.casefold()
    found = next(
        (
            entry.name
            for entry in profile.skills
            if entry.verify_before_shipping
            and wanted in {spelling.casefold() for spelling in (entry.name, *entry.aliases)}
        ),
        None,
    )
    if found is None:
        raise ChoiceError(f"{skill} isn't a skill marked verify-before-shipping")
    skills = {name: value for name, value in choices.skills.items() if name != found}
    if decision is not None:
        skills[found] = decision
    return choices.model_copy(update={"skills": skills})


def _find(phrasings: Sequence[Phrasing], bullet_id: str) -> Phrasing | None:
    return next((phrasing for phrasing in phrasings if phrasing.bullet_id == bullet_id), None)


def _used(tailoring: Tailoring, choices: DraftChoices) -> tuple[Phrasing, ...]:
    swaps = [swap for swap in tailoring.swaps if choices.uses.get(swap.bullet_id) == "swap"]
    phrasings = [
        phrasing
        for phrasing in tailoring.proposals
        if choices.uses.get(phrasing.bullet_id) == "phrasing"
        and phrasing.bullet_id in choices.approved
    ]
    return (*swaps, *phrasings)


def _reworded(
    selection: Selection, tailoring: Tailoring, choices: DraftChoices
) -> tuple[RewordedBullet, ...]:
    swaps = {swap.bullet_id: swap for swap in tailoring.swaps}
    phrasings = {phrasing.bullet_id: phrasing for phrasing in tailoring.proposals}
    bullets: list[RewordedBullet] = []
    items = [(role.organisation, role.bullets) for role in selection.work]
    items += [(project.name, project.bullets) for project in selection.projects]
    for under, shown in items:
        for bullet in shown:
            swap, phrasing = swaps.get(bullet.id), phrasings.get(bullet.id)
            new = swap or phrasing
            if new is None:
                continue
            approved = bullet.id in choices.approved
            use = choices.uses.get(bullet.id, "own")
            if use == "phrasing" and not approved:
                use = "own"
            bullets.append(
                RewordedBullet(
                    id=bullet.id,
                    under=under,
                    original=new.original,
                    use=use,
                    swap=swap,
                    phrasing=phrasing,
                    approved=approved,
                )
            )
    return tuple(bullets)
