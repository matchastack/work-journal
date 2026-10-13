"""Evaluation datasets: the cases `wj eval` runs, as one TOML file per suite.

- `extraction.toml`: journal notes and the facts each one gives (entries to expected facts).
- `writing.toml`: facts and what a bullet written from them must and mustn't say (facts to
  bullets).
- `adversarial.toml`: known-bad text the verifier must catch, and faithful text it must pass.

Notes and outputs that try to trick the extractor or the writer, such as instructions in a note,
are cases in their own suites, with `never` patterns.

The fictional dataset is committed in `backend/evals/`, whose README describes the format. The
owner's own cases go in the same files in the git-ignored `local/evals/`. Paths in a file, such as
its profile, are relative to the file. Loading checks every ID a case names, so a run doesn't
stop halfway on a typo.
"""

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from app.schema.fact import Fact, Ownership
from app.schema.profile import Profile
from app.validate.verifier import TextKind
from app.writing import locate_bullet

Suite = Literal["extraction", "writing", "adversarial"]
SUITES: tuple[Suite, ...] = ("extraction", "writing", "adversarial")

FICTIONAL_DIR = Path(__file__).resolve().parents[2] / "evals"
"""The committed dataset about the fictional person (`backend/evals/`)."""

_FACTS = TypeAdapter(tuple[Fact, ...])
_MAX_ERRORS = 10


class DatasetError(Exception):
    """A dataset file that can't be used. The message says where, never quoting a case's text."""


def _compiles(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"not a regular expression: {error}") from None
    return pattern


Pattern = Annotated[str, AfterValidator(_compiles)]
"""A regular expression, searched for anywhere in the text, ignoring case."""

CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^|`\n]+$")]
"""A case's name in the report, such as `inflated ownership`."""


class _Entry(BaseModel):
    """Dataset files are hand-written, so unknown keys are errors. Text is kept as written: a
    note goes to the model exactly as the owner would send it."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExpectedFact(_Entry):
    """A fact the note gives. A fact the model writes recovers it when it matches every part."""

    terms: tuple[Pattern, ...] = ()
    """Patterns the fact's statement, outcome or tools must match, such as `export`."""
    numbers: tuple[float, ...] = ()
    """Numbers the fact's metrics must hold."""
    role: str | None = None
    """The role the fact must link to."""
    project: str | None = None
    """The project the fact must link to."""
    ownership: Ownership | None = None
    """The most ownership the note supports. More is an inflated claim."""


class ExtractionCase(_Entry):
    id: CaseId
    note: str = Field(min_length=1)
    written: date | None = None
    """The day the note was written. Default: the file's `written`."""
    role: str | None = None
    """The role the owner chose for the entry (FR-CAP-11), if any."""
    expected: tuple[ExpectedFact, ...] = ()
    """The facts the note gives. With none, any fact is spurious."""
    never: tuple[Pattern, ...] = ()
    """Patterns no fact may match, such as ownership the note doesn't give."""


class WritingCase(_Entry):
    id: CaseId
    facts: tuple[str, ...] = Field(min_length=1)
    """The IDs of the facts to write from."""
    under: str | None = None
    """The role, education entry or project the bullet goes under. Default: the one the bullet
    to edit, or else the facts, belong to."""
    edit: str | None = None
    """A bullet to write a new version of."""
    avoid: tuple[str, ...] = ()
    """The owner's words to avoid."""
    keeps: tuple[Pattern, ...] = ()
    """Patterns the bullet must match, such as its facts' numbers."""
    never: tuple[Pattern, ...] = ()
    """Patterns the bullet mustn't match, such as an inflated verb."""


class AdversarialCase(_Entry):
    id: CaseId
    text: str = Field(min_length=1)
    """The text the verifier checks."""
    facts: tuple[str, ...] = Field(min_length=1)
    """The IDs of the facts the text cites."""
    under: str | None = None
    """The role, education entry or project the text is about. Default: the facts' one."""
    kind: TextKind = "resume_bullet"
    posting_terms: tuple[str, ...] = ()
    """The terms of the posting the text is tailored to, if any."""
    catch: tuple[str, ...] = ()
    """For known-bad text, the verifier codes that catch it, any one of which will do. Empty for
    faithful text, which must pass."""


class _File(_Entry):
    profile: Path
    """The profile JSON, relative to this file."""


class ExtractionFile(_File):
    written: date
    cases: tuple[ExtractionCase, ...]


class _FileWithFacts(_File):
    fact_files: tuple[Path, ...] = ()
    """JSON lists of facts, relative to this file, such as the import's `facts.json`."""
    facts: tuple[Fact, ...] = ()
    """More facts, written in this file."""


class WritingFile(_FileWithFacts):
    cases: tuple[WritingCase, ...]


class AdversarialFile(_FileWithFacts):
    cases: tuple[AdversarialCase, ...]


_FILES: dict[Suite, type[ExtractionFile | WritingFile | AdversarialFile]] = {
    "extraction": ExtractionFile,
    "writing": WritingFile,
    "adversarial": AdversarialFile,
}

Case = ExtractionCase | WritingCase | AdversarialCase


@dataclass(frozen=True)
class Dataset:
    """One suite's cases, with the profile and facts they use."""

    suite: Suite
    path: Path
    profile: Profile
    facts: Mapping[str, Fact]
    cases: tuple[Case, ...]


def load_dataset(folder: Path, suite: Suite) -> Dataset | None:
    """The suite's cases in `folder`, or None when the folder has no file for the suite."""
    path = folder / f"{suite}.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise DatasetError(f"{path} isn't valid TOML: {error}") from None
    try:
        file = _FILES[suite].model_validate(data)
    except ValidationError as error:
        raise DatasetError(f"{path} doesn't match the format:\n{_problems(error)}") from None
    profile = _profile(path.parent / file.profile, path)
    facts: dict[str, Fact] = {}
    cases: tuple[Case, ...]
    if isinstance(file, ExtractionFile):
        cases = tuple(
            case if case.written else case.model_copy(update={"written": file.written})
            for case in file.cases
        )
    else:
        facts = _facts(path, file)
        cases = file.cases
    dataset = Dataset(suite=suite, path=path, profile=profile, facts=facts, cases=cases)
    _check(dataset)
    return dataset


def writing_item(case: WritingCase | AdversarialCase, dataset: Dataset) -> str | None:
    """The ID of the item the case's text goes under: the one it names, or the bullet to edit's,
    or else the one all its facts belong to. None when there's no single one."""
    if case.under is not None:
        return case.under
    if isinstance(case, WritingCase) and case.edit is not None:
        return locate_bullet(dataset.profile, case.edit)[0]
    items = {
        dataset.facts[fact_id].role_id or dataset.facts[fact_id].project_id
        for fact_id in case.facts
    }
    if len(items) == 1:
        return items.pop()
    return None


def _profile(path: Path, file: Path) -> Profile:
    if not path.is_file():
        raise DatasetError(f"{file}: the profile {path} doesn't exist")
    try:
        return Profile.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise DatasetError(f"{file}: the profile {path} isn't valid:\n{_problems(error)}") from None


def _facts(path: Path, file: _FileWithFacts) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}
    loaded: list[Fact] = []
    for name in file.fact_files:
        source = path.parent / name
        if not source.is_file():
            raise DatasetError(f"{path}: the fact file {source} doesn't exist")
        try:
            loaded.extend(_FACTS.validate_json(source.read_text(encoding="utf-8")))
        except ValidationError as error:
            problems = _problems(error)
            raise DatasetError(f"{path}: {source} isn't a list of facts:\n{problems}") from None
    for fact in (*loaded, *file.facts):
        if fact.id in facts:
            raise DatasetError(f"{path}: two facts have the ID {fact.id!r}")
        facts[fact.id] = fact
    return facts


def _check(dataset: Dataset) -> None:
    """Every case ID is unique, and every ID a case names is in the profile or the facts."""
    profile = dataset.profile
    roles = {role.id for role in profile.work}
    projects = {project.id for project in profile.projects}
    items = roles | projects | {entry.id for entry in profile.education}
    seen: set[str] = set()
    for case in dataset.cases:
        where = f"{dataset.path}, case {case.id!r}"
        if case.id in seen:
            raise DatasetError(f"{where}: another case has this ID")
        seen.add(case.id)
        problems: list[str] = []
        if isinstance(case, ExtractionCase):
            problems += _unknown("role", [case.role], roles)
            for expected in case.expected:
                problems += _unknown("role", [expected.role], roles)
                problems += _unknown("project", [expected.project], projects)
        else:
            problems += _unknown("fact", case.facts, set(dataset.facts))
            problems += _unknown("item", [case.under], items)
            if isinstance(case, WritingCase) and case.edit is not None:
                try:
                    locate_bullet(profile, case.edit)
                except ValueError as error:
                    problems.append(str(error))
            if not problems and isinstance(case, WritingCase) and not writing_item(case, dataset):
                problems.append("the facts don't all belong to one item; say which with `under`")
        if problems:
            raise DatasetError(f"{where}: {'; '.join(problems)}")


def _unknown(kind: str, ids: Sequence[str | None], known: set[str]) -> list[str]:
    return [f"there's no {kind} {item!r}" for item in ids if item is not None and item not in known]


def _problems(error: ValidationError) -> str:
    """Each failed field and rule, without the values, which can be journal text."""
    problems = error.errors(include_url=False, include_input=False, include_context=False)
    lines = [
        f"- {'.'.join(str(part) for part in problem['loc']) or '(the whole file)'}: "
        f"{problem['msg']}"
        for problem in problems[:_MAX_ERRORS]
    ]
    if len(problems) > _MAX_ERRORS:
        lines.append(f"- and {len(problems) - _MAX_ERRORS} more")
    return "\n".join(lines)
