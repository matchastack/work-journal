"""Facts from a repository's history (FR-GH-6): each part of the digest, read by the standard-tier
model, then checked in code.

The prompt (`repo_extraction`) runs as the `fact_extraction` task, so the routing is unchanged.
The context is the owner's skills and the project, never their roles: a GitHub fact belongs to a
project only. Each fact the model returns cites the items it rests on, and code then:

- drops a fact that cites no item, or an item its part doesn't have;
- checks its numbers and its tools against the cited items' own text alone, the way extraction
  checks a note (`app/extraction.py`): a fact with a number they don't give is dropped, and a
  tool they don't name is removed;
- sets what the model never chooses:
  - the project, from the owner's choice;
  - the date, the month of the latest cited pull request or commit. The about item describes the
    project as it is now, so it dates no fact, and a fact that cites it alone has no date;
  - the ownership cap. That's owned in a repository the owner's account owns that isn't a fork,
    and contributed elsewhere, or when another person co-authored a cited item. It's never led.
    The owner may set the cap when linking, and the model may only lower it.
  - the source: the repository and the cited items, with their links;
- adds the AI coding agent that made or helped make a cited item to the tools (FR-GH-4).

A part the model declines or can't answer in the right shape is recorded and skipped, so the
other parts' facts are kept (FR-GH-7). An outage stops the read, as the next part would fail too.

IDs are new for each part read (`gh_<part>_<n>`), so reading again never overwrites a fact the
owner has edited.
"""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.extraction import ExtractedFact, ExtractedMetric, ExtractionReply, check_facts
from app.github.digest import Digest, Item
from app.llm.client import LLMClient, LLMInvalidOutput, LLMRefusal
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model, NonEmptyStr
from app.schema.fact import Fact, Ownership, RepoItem, RepoSource
from app.schema.profile import Profile

PROMPT_NAME = "repo_extraction"
PROMPT_VERSION = 1
RANK: dict[Ownership, int] = {"assisted": 0, "contributed": 1, "owned": 2, "led": 3}


class RepoFactReply(Model):
    """A fact as the model writes it, with the labels of the items it cites. Code adds the rest."""

    items: tuple[str, ...]
    statement: NonEmptyStr
    kind: Literal["accomplishment", "project"]
    metrics: tuple[ExtractedMetric, ...] = ()
    ownership: Literal["contributed", "assisted"] | None = None
    """Only lower than the cap, which code sets."""
    tools: tuple[str, ...] = ()
    outcome: str | None = None


class RepoExtractionReply(Model):
    facts: tuple[RepoFactReply, ...]


@dataclass(frozen=True)
class RepoTarget:
    """The project a repository's facts belong to: one in the profile, by ID, or a new one the
    owner names, which a refresh may add (FR-GH-2). `ownership` is the cap the owner chose."""

    project_id: str | None = None
    new_project: str | None = None
    ownership: Ownership | None = None

    def __post_init__(self) -> None:
        if (self.project_id is None) == (self.new_project is None):
            raise ValueError("name either a project in the profile or a new project")
        if self.ownership == "led":
            raise ValueError("a repository's history can't show you led the work")

    def project_name(self, profile: Profile) -> str:
        """The project's name; raises `ValueError` when the profile has no such project."""
        if self.new_project is not None:
            return self.new_project
        for project in profile.projects:
            if project.id == self.project_id:
                return project.name
        raise ValueError(f"the profile has no project {self.project_id!r}")


@dataclass(frozen=True)
class DroppedRepoFact:
    statement: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FailedPart:
    index: int
    """The part of the digest, from 0."""
    reason: str
    """Why the model gave no facts for it. It never holds the history's text."""


@dataclass(frozen=True)
class RepoExtraction:
    facts: tuple[Fact, ...] = ()
    dropped: tuple[DroppedRepoFact, ...] = ()
    """Facts left out: they cite nothing, an unknown item, or a number their items don't give."""
    notes: tuple[str, ...] = ()
    """Tools removed from the facts that were kept."""
    failed: tuple[FailedPart, ...] = ()
    """Parts the model declined or couldn't answer, so their facts are missing."""

    def __add__(self, other: "RepoExtraction") -> "RepoExtraction":
        return RepoExtraction(
            self.facts + other.facts,
            self.dropped + other.dropped,
            self.notes + other.notes,
            self.failed + other.failed,
        )


def extract_repo_facts(
    digest: Digest,
    profile: Profile,
    client: LLMClient,
    *,
    target: RepoTarget,
    prompt: Prompt | None = None,
) -> RepoExtraction:
    """The facts in every part of the digest, read one part at a time by the standard-tier
    model, then checked. A part the model declines or can't answer is recorded in `failed`;
    any other `LLMCallError`, such as an outage, stops the read."""
    target.project_name(profile)
    extraction = RepoExtraction()
    for index in range(len(digest.windows)):
        try:
            reply = ask_for_repo_facts(digest, index, profile, client, target=target, prompt=prompt)
        except (LLMRefusal, LLMInvalidOutput) as error:
            extraction += RepoExtraction(failed=(FailedPart(index, str(error)),))
            continue
        part = uuid.uuid4().hex
        extraction += check_repo_facts(reply, digest, index, profile, target=target, part=part)
    return extraction


def ask_for_repo_facts(
    digest: Digest,
    index: int,
    profile: Profile,
    client: LLMClient,
    *,
    target: RepoTarget,
    prompt: Prompt | None = None,
) -> RepoExtractionReply:
    """The facts the model finds in part `index` of the digest, before `check_repo_facts`
    checks them. The evaluation (`app/evals/`) scores this reply."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call(
        "fact_extraction",
        prompt,
        RepoExtractionReply,
        variables={"history": digest.render_window(index)},
        context=repo_context(profile, target),
    )
    return result.output


def repo_context(profile: Profile, target: RepoTarget) -> str:
    """The skills and the project, as JSON for the prompt. Roles stay out (FR-GH-2)."""
    data = {
        "project": target.project_name(profile),
        "skills": [skill.name for skill in profile.skills],
    }
    return "The person's skills, and the project these facts belong to:\n" + json.dumps(
        data, indent=2
    )


def check_repo_facts(
    reply: RepoExtractionReply,
    digest: Digest,
    index: int,
    profile: Profile,
    *,
    target: RepoTarget,
    part: str,
) -> RepoExtraction:
    """Keep the facts their cited items back, as the module docstring describes. `part` makes
    the facts' IDs new for this read."""
    items = {item.label: item for item in digest.windows[index]}
    facts: list[Fact] = []
    dropped: list[DroppedRepoFact] = []
    notes: list[str] = []
    for replied in reply.facts:
        labels = tuple(dict.fromkeys(replied.items))
        if not labels:
            dropped.append(DroppedRepoFact(replied.statement, ("it cites no item",)))
            continue
        if unknown := [label for label in labels if label not in items]:
            reason = f"it cites {', '.join(unknown)}, which this part doesn't have"
            dropped.append(DroppedRepoFact(replied.statement, (reason,)))
            continue
        cited = [items[label] for label in labels]
        checked = _checked(replied, cited, profile)
        if isinstance(checked, DroppedRepoFact):
            dropped.append(checked)
            continue
        fact_id = f"gh_{part}_{len(facts) + 1}"
        notes += [
            f'{fact_id}: removed the tool "{tool}", which the items it cites don\'t name'
            for tool in replied.tools
            if tool not in checked.tools
        ]
        facts.append(_github_fact(checked, fact_id, cited, digest, replied, target))
    return RepoExtraction(tuple(facts), tuple(dropped), tuple(notes))


def ownership_cap(digest: Digest, cited: Sequence[Item], target: RepoTarget) -> Ownership:
    """The most the facts may claim of the owner's part (FR-GH-6)."""
    cap: Ownership = target.ownership or ("owned" if digest.owned else "contributed")
    if any(item.coauthored for item in cited):
        cap = _lower(cap, "contributed")
    return cap


def _checked(
    replied: RepoFactReply, cited: Sequence[Item], profile: Profile
) -> Fact | DroppedRepoFact:
    """The fact as extraction would keep it from a note made of the cited items: its numbers
    checked, and tools they don't name removed."""
    evidence = "\n\n".join(item.evidence for item in cited)
    extracted = ExtractedFact(
        statement=replied.statement,
        kind=replied.kind,
        metrics=replied.metrics,
        tools=replied.tools,
        outcome=replied.outcome,
    )
    checked = check_facts(ExtractionReply(facts=(extracted,)), evidence, profile)
    if checked.dropped:
        reasons = tuple(
            reason.replace("the note doesn't give", "the items it cites don't give")
            for reason in checked.dropped[0].reasons
        )
        return DroppedRepoFact(replied.statement, reasons)
    return checked.facts[0]


def _github_fact(
    checked: Fact,
    fact_id: str,
    cited: Sequence[Item],
    digest: Digest,
    replied: RepoFactReply,
    target: RepoTarget,
) -> Fact:
    cap = ownership_cap(digest, cited, target)
    tools = list(checked.tools)
    for agent in dict.fromkeys(item.agent for item in cited if item.agent is not None):
        if agent.casefold() not in {tool.casefold() for tool in tools}:
            tools.append(agent)
    work = [item.at for item in cited if item.kind != "about"]
    source = RepoSource(
        repository=digest.repository,
        url=digest.url,
        private=digest.private,
        items=tuple(RepoItem(kind=item.kind, ref=item.ref, url=item.url) for item in cited),
    )
    return Fact(
        id=fact_id,
        statement=checked.statement,
        kind=checked.kind,
        metrics=checked.metrics,
        ownership=cap if replied.ownership is None else _lower(cap, replied.ownership),
        tools=tuple(tools),
        outcome=checked.outcome,
        date=f"{max(work):%Y-%m}" if work else None,
        project_id=target.project_id,
        proposed_item=target.new_project,
        origin="github",
        source=source,
    )


def _lower(first: Ownership, second: Ownership) -> Ownership:
    return first if RANK[first] <= RANK[second] else second
