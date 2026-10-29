"""Facts: neutral, structured records of what happened. The ground truth for meaning and numbers."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.schema.common import Id, Model, NonEmptyStr, YearMonth


class SingleValue(Model):
    """One number, e.g. `93` (% accuracy)."""

    kind: Literal["single"] = "single"
    value: float


class ChangeValue(Model):
    """A change from one number to another, e.g. `50 → 12` (minutes)."""

    kind: Literal["change"] = "change"
    before: float
    after: float


class RangeValue(Model):
    """A range, e.g. `2-3` (hours a week)."""

    kind: Literal["range"] = "range"
    low: float
    high: float

    @model_validator(mode="after")
    def low_not_above_high(self) -> Self:
        if self.low > self.high:
            raise ValueError("range low must not be above high")
        return self


MetricValue = Annotated[SingleValue | ChangeValue | RangeValue, Field(discriminator="kind")]

Qualifier = Literal["exact", "approximately", "at_least", "more_than", "at_most", "less_than"]
"""How precise the number is. "200+" is `at_least`; "about 200" is `approximately`."""


class Metric(Model):
    subject: NonEmptyStr
    """What was measured, e.g. `export job duration`."""
    value: MetricValue
    unit: str | None = None
    """E.g. `minutes`, `%`, `x`, `papers`. None for a plain count."""
    qualifier: Qualifier = "exact"


FactKind = Literal[
    "accomplishment",
    "project",
    "role_change",
    "skill",
    "certification",
    "award",
    "talk",
    "learning",
]

Ownership = Literal["led", "owned", "contributed", "assisted"]
RepoItemKind = Literal["about", "pull", "commit"]


class RepoItem(Model):
    """A pull request, a commit or a repository's description that a GitHub fact rests on."""

    kind: RepoItemKind
    ref: str = ""
    """The pull request's number or the commit's hash; empty for the repository's description."""
    url: NonEmptyStr


class RepoSource(Model):
    """Where a fact from GitHub comes from (FR-GH-6): its repository and the items it cites."""

    repository: NonEmptyStr
    """The repository, as `owner/name`."""
    url: NonEmptyStr
    private: bool
    items: Annotated[tuple[RepoItem, ...], Field(min_length=1)]


class Fact(Model):
    """Something that happened, extracted from a journal entry, an imported bullet or a
    repository's history."""

    id: Id
    statement: NonEmptyStr
    """What happened, in neutral words."""
    kind: FactKind = "accomplishment"
    metrics: tuple[Metric, ...] = ()
    ownership: Ownership | None = None
    tools: tuple[str, ...] = ()
    outcome: str | None = None
    date: YearMonth | None = None
    role_id: Id | None = None
    """The existing role this fact belongs to, if any."""
    project_id: Id | None = None
    """The existing project this fact belongs to, if any."""
    proposed_item: str | None = None
    """The name of a new role or project this fact implies, if it fits no existing one."""
    proposed_role: str | None = None
    """The name of the new role the owner gave this fact's entry (FR-CAP-11), which a refresh may
    add to the profile. Set only when `role_id` isn't."""
    question_id: Id | None = None
    """The open question this fact answers, if any."""
    confidentiality_flags: tuple[str, ...] = ()
    origin: Literal["journal", "import", "github"] = "journal"
    entry_id: str | None = None
    """The journal entry this fact came from. None for imported facts and facts from GitHub."""
    source: RepoSource | None = None
    """For a fact from GitHub, the repository and the pull requests and commits it cites."""

    @model_validator(mode="after")
    def github_facts_have_a_source(self) -> Self:
        if (self.origin == "github") != (self.source is not None):
            raise ValueError("a fact from GitHub has a source, and no other fact does")
        if self.origin == "github" and (self.entry_id or self.role_id or self.proposed_role):
            raise ValueError("a fact from GitHub belongs to a project, never to an entry or a role")
        return self
