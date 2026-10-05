"""Fact extraction: an informal journal note becomes neutral, structured facts (FR-EXT-1).

A standard-tier prompt reads the note, with the profile's roles, projects and skills as cached
context. Code then checks each fact against the note before keeping it:

- Numbers (FR-EXT-2): every number in the fact, in its statement, outcome and metrics, must be one
  the note gives, in a compatible unit and with a qualifier no stronger ("50" isn't backed by
  "~50"). The number checker (T-005) compares them one number at a time, since a note's
  "from ~50 min to 12" and a fact's "from about 50 to 12 minutes" group the same numbers
  differently. A number the note gives without a unit takes the fact's unit. A fact with a
  number the note doesn't back is dropped, and so is one with a computed figure such as a
  percentage change: facts keep the note's own numbers.
- Tools: a tool the note doesn't name is removed from the fact, unless the note names one of its
  aliases in the skills catalogue (R1).
- Links: a role or project ID that isn't in the profile is removed.

A note unrelated to work gives no facts (FR-CAP-8).
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, Self

from pydantic import model_validator

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model, NonEmptyStr, YearMonth
from app.schema.fact import (
    ChangeValue,
    Fact,
    FactKind,
    Metric,
    Ownership,
    Qualifier,
    RangeValue,
    SingleValue,
)
from app.schema.profile import Profile, Skill
from app.validate.numbers import check_numbers
from app.validate.quantities import Quantity, find_quantities, normalize_unit

PROMPT_NAME = "fact_extraction"
PROMPT_VERSION = 1


class ExtractedMetric(Model):
    """A metric as the model writes it. It's flat, unlike `Metric`, so the reply's schema stays
    simple enough for structured outputs."""

    subject: NonEmptyStr
    kind: Literal["single", "change", "range"]
    numbers: tuple[float, ...]
    """`[value]` for single, `[before, after]` for change and `[low, high]` for range."""
    unit: str | None = None
    qualifier: Qualifier = "exact"

    @model_validator(mode="after")
    def numbers_fit_the_kind(self) -> Self:
        expected = 1 if self.kind == "single" else 2
        if len(self.numbers) != expected:
            count = "one number" if expected == 1 else "two numbers"
            raise ValueError(f"a {self.kind} metric needs {count}")
        if self.kind == "range" and self.numbers[0] > self.numbers[1]:
            raise ValueError("a range's low number must not be above its high one")
        return self

    def to_metric(self) -> Metric:
        value: SingleValue | ChangeValue | RangeValue
        match self.kind:
            case "single":
                value = SingleValue(value=self.numbers[0])
            case "change":
                value = ChangeValue(before=self.numbers[0], after=self.numbers[1])
            case "range":
                value = RangeValue(low=self.numbers[0], high=self.numbers[1])
        return Metric(subject=self.subject, value=value, unit=self.unit, qualifier=self.qualifier)


class ExtractedFact(Model):
    """A fact as the model writes it. Code adds the ID, the entry and the checks."""

    statement: NonEmptyStr
    kind: FactKind
    metrics: tuple[ExtractedMetric, ...] = ()
    ownership: Ownership | None = None
    tools: tuple[str, ...] = ()
    outcome: str | None = None
    date: YearMonth | None = None
    role_id: str | None = None
    project_id: str | None = None
    proposed_item: str | None = None


class ExtractionReply(Model):
    facts: tuple[ExtractedFact, ...]


@dataclass(frozen=True)
class DroppedFact:
    statement: str
    reasons: tuple[str, ...]
    """The number checker's errors, e.g. `'300 users' isn't in the cited facts`."""


@dataclass(frozen=True)
class Extraction:
    facts: tuple[Fact, ...]
    dropped: tuple[DroppedFact, ...] = ()
    """Facts left out because the note doesn't give one of their numbers (FR-EXT-2)."""
    notes: tuple[str, ...] = ()
    """Tools and links removed from the facts that were kept."""


def extract_facts(
    note: str,
    profile: Profile,
    client: LLMClient,
    *,
    written: date,
    entry_id: str | None = None,
    prompt: Prompt | None = None,
) -> Extraction:
    """Extract the facts in `note`, written on `written`, with the standard-tier model."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call(
        "fact_extraction",
        prompt,
        ExtractionReply,
        variables={"note": note, "date": written.isoformat()},
        context=profile_context(profile),
    )
    return check_facts(result.output, note, profile, entry_id=entry_id)


def profile_context(profile: Profile) -> str:
    """The roles, projects and skills that facts link to, as JSON for the prompt."""
    data = {
        "roles": [
            {
                "id": role.id,
                "organisation": role.name,
                "title": role.position,
                "startDate": role.start_date,
                "endDate": role.end_date,
            }
            for role in profile.work
        ],
        "projects": [
            {"id": project.id, "name": project.name, "status": project.status}
            for project in profile.projects
        ],
        "skills": [skill.name for skill in profile.skills],
    }
    return "The person's profile:\n" + json.dumps(data, indent=2)


def check_facts(
    reply: ExtractionReply, note: str, profile: Profile, *, entry_id: str | None = None
) -> Extraction:
    """Keep the facts the note backs, as described in the module docstring."""
    skill_names = [name for skill in profile.skills for name in (skill.name, *skill.aliases)]
    note_quantities = find_quantities(note, skill_names)
    roles = {role.id for role in profile.work}
    projects = {project.id for project in profile.projects}
    facts: list[Fact] = []
    dropped: list[DroppedFact] = []
    notes: list[str] = []
    for extracted in reply.facts:
        errors = _unbacked_numbers(extracted, note_quantities, [*skill_names, *extracted.tools])
        if errors:
            dropped.append(DroppedFact(statement=extracted.statement, reasons=errors))
            continue
        fact_id = f"{entry_id or 'fact'}_{len(facts) + 1}"
        tools: list[str] = []
        for tool in extracted.tools:
            if _named(tool, note, profile.skills):
                tools.append(tool)
            else:
                notes.append(f'{fact_id}: removed the tool "{tool}", which the note doesn\'t name')
        role_id = _known(extracted.role_id, roles, "role", fact_id, notes)
        project_id = _known(extracted.project_id, projects, "project", fact_id, notes)
        facts.append(
            Fact(
                id=fact_id,
                statement=extracted.statement,
                kind=extracted.kind,
                metrics=tuple(metric.to_metric() for metric in extracted.metrics),
                ownership=extracted.ownership,
                tools=tuple(tools),
                outcome=extracted.outcome,
                date=extracted.date,
                role_id=role_id,
                project_id=project_id,
                proposed_item=extracted.proposed_item,
                origin="journal",
                entry_id=entry_id,
            )
        )
    return Extraction(facts=tuple(facts), dropped=tuple(dropped), notes=tuple(notes))


def _unbacked_numbers(
    fact: ExtractedFact, note_quantities: Sequence[Quantity], allowed_terms: Sequence[str]
) -> tuple[str, ...]:
    """Why the note doesn't back the fact's numbers; empty when it does."""
    claims: list[tuple[float, str | None, Qualifier]] = []
    for text in (fact.statement, fact.outcome or ""):
        for quantity in find_quantities(text, allowed_terms):
            claims.extend((value, quantity.unit, quantity.qualifier) for value in quantity.values)
    for metric in fact.metrics:
        unit = normalize_unit(metric.unit)
        claims.extend((value, unit, metric.qualifier) for value in metric.numbers)
    errors: list[str] = []
    for value, unit, qualifier in claims:
        given = [
            quantity
            for quantity in note_quantities
            if any(math.isclose(number, value, rel_tol=1e-9) for number in quantity.values)
        ]
        if not given:
            errors.append(f"the note doesn't give {_claim_text(value, unit, qualifier)}")
            continue
        problems: list[str] = []
        for quantity in given:
            # A number given without a unit, in the note or in the fact, takes the other's unit.
            shared = unit or quantity.unit
            evidence = Metric(
                subject="note",
                value=SingleValue(value=value),
                unit=quantity.unit or shared,
                qualifier=quantity.qualifier,
            )
            check = check_numbers(_claim_text(value, shared, qualifier), [evidence])
            found = [finding.message for finding in check.findings if finding.severity == "error"]
            if not found:
                break
            problems += found
        else:
            errors += problems
    return tuple(dict.fromkeys(errors))


_QUALIFIER_WORDS: dict[Qualifier, str] = {
    "exact": "",
    "approximately": "about ",
    "at_least": "at least ",
    "more_than": "more than ",
    "at_most": "up to ",
    "less_than": "less than ",
}


def _claim_text(value: float, unit: str | None, qualifier: Qualifier) -> str:
    """One number written the way the number checker reads it, e.g. `about 50 minute`."""
    number = f"{value:,.6f}".rstrip("0").rstrip(".")
    if unit == "$":
        number = f"${number}"
    elif unit in {"%", "x"}:
        number = f"{number}{unit}"
    elif unit:
        number = f"{number} {unit}"
    return _QUALIFIER_WORDS[qualifier] + number


def _named(tool: str, note: str, skills: Sequence[Skill]) -> bool:
    """Whether the note names `tool`, or an alias of the catalogue skill it is."""
    spellings = {tool}
    for skill in skills:
        names = (skill.name, *skill.aliases)
        if tool.casefold() in {name.casefold() for name in names}:
            spellings.update(names)
    return any(
        re.search(rf"(?<![\w.+#-]){re.escape(spelling)}(?![\w+#]|\.\w)", note, re.IGNORECASE)
        for spelling in spellings
    )


def _known(
    item_id: str | None, known: set[str], kind: str, fact_id: str, notes: list[str]
) -> str | None:
    if item_id is None or item_id in known:
        return item_id
    notes.append(
        f'{fact_id}: removed the link to "{item_id}", which isn\'t a {kind} in the profile'
    )
    return None
