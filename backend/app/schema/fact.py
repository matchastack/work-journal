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


class Fact(Model):
    """Something that happened, extracted from a journal entry or an imported bullet."""

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
    question_id: Id | None = None
    """The open question this fact answers, if any."""
    confidentiality_flags: tuple[str, ...] = ()
    origin: Literal["journal", "import"] = "journal"
    entry_id: str | None = None
    """The journal entry this fact came from. None for imported facts."""
