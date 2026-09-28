"""Versioned prompt files (NFR-MAINT-2).

Each prompt lives in `app/llm/prompts/<name>/v<N>/` as `system.md` (the stable instructions,
which are cached) and `user.md` (a template with `$placeholders`). A released version is never
edited: changes go in a new version, after the evaluation suite has run (CLAUDE.md).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"
_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    system: str
    user: str
    """A template: `$name` or `${name}` is replaced by the variable of that name."""

    @property
    def id(self) -> str:
        """What each call records, e.g. `fact_extraction/v2`."""
        return f"{self.name}/v{self.version}"

    def render(self, variables: Mapping[str, str]) -> str:
        """The user message. A placeholder without a variable raises `KeyError`."""
        return Template(self.user).substitute(variables)


def load_prompt(name: str, version: int, root: Path = PROMPTS_DIR) -> Prompt:
    """Load one version of a prompt. Callers name the version, so changing it is deliberate."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"not a prompt name: {name!r}")
    folder = root / name / f"v{version}"
    if not folder.is_dir():
        raise FileNotFoundError(f"no prompt {name!r} version {version} in {root}")
    return Prompt(
        name=name,
        version=version,
        system=(folder / "system.md").read_text(encoding="utf-8").strip(),
        user=(folder / "user.md").read_text(encoding="utf-8").strip(),
    )
