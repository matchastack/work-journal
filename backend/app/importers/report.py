"""The import report: what was imported, what to check, and what couldn't be mapped (FR-IMP-3)."""

from dataclasses import dataclass, field
from typing import Literal

ReportKind = Literal["problem", "check", "generated", "skipped"]

_HEADINGS: dict[ReportKind, str] = {
    "problem": "Problems (fix them in the .tex file and import again)",
    "check": "Check these mappings",
    "generated": "Generated IDs",
    "skipped": "Not imported",
}


@dataclass(frozen=True)
class ReportItem:
    kind: ReportKind
    line: int | None
    """The line in the source file, if the item has one."""
    message: str


@dataclass
class ImportReport:
    summary: str = ""
    items: list[ReportItem] = field(default_factory=list[ReportItem])

    def problem(self, line: int | None, message: str) -> None:
        """Something that couldn't be imported as written and needs fixing in the source."""
        self.items.append(ReportItem("problem", line, message))

    def check(self, line: int | None, message: str) -> None:
        """A mapping the owner should confirm, e.g. which role type a label was matched to."""
        self.items.append(ReportItem("check", line, message))

    def generated(self, line: int | None, message: str) -> None:
        self.items.append(ReportItem("generated", line, message))

    def skipped(self, line: int | None, message: str) -> None:
        """Something left out of the profile, such as a comment with nowhere to go."""
        self.items.append(ReportItem("skipped", line, message))

    def of(self, kind: ReportKind) -> list[ReportItem]:
        return sorted(
            (item for item in self.items if item.kind == kind), key=lambda item: item.line or 0
        )

    def render(self) -> str:
        parts = [self.summary]
        for kind, heading in _HEADINGS.items():
            if items := self.of(kind):
                parts.append(f"\n{heading}:")
                parts.extend(
                    f"  line {item.line}: {item.message}" if item.line else f"  {item.message}"
                    for item in items
                )
        return "\n".join(parts) + "\n"
