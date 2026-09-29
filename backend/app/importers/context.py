"""What the parts of a master-resume import share: the report, the IDs and the role types."""

import re
from dataclasses import dataclass

from app.importers.latex import to_plain, unescape_comment
from app.importers.report import ImportReport
from app.importers.tags import Segment, list_items
from app.importers.values import display_label, label_parts, quote, slug, snippet
from app.schema.fact import Fact
from app.schema.profile import OpenQuestion

_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass
class RoleTypeDraft:
    id: str
    name: str
    parts: set[str]
    """Lowercase label parts such as `backend` and `full stack`, used to match other labels."""


@dataclass(frozen=True)
class Need:
    """A `NEEDS: q_x` comment on a bullet."""

    line: int
    question_id: str
    bullet_id: str
    comment: str


class ImportContext:
    def __init__(self, report: ImportReport) -> None:
        self.report = report
        self.unknown_commands: set[str] = set()
        self.used_ids: set[str] = set()
        self.reserved_ids: set[str] = set()
        """IDs written in the file, which generated IDs must not take."""
        self.role_types: list[RoleTypeDraft] = []
        self._role_type_by_label: dict[str, str] = {}
        self.allowed_terms: list[str] = []
        """Skill names such as `Java 17`, whose numbers aren't metrics."""
        self.questions: dict[str, OpenQuestion] = {}
        self.needs: list[Need] = []
        self.facts: list[Fact] = []

    def plain(self, tex: str) -> str:
        """Plain text of some LaTeX, remembering the commands it had to drop."""
        return to_plain(tex, self.unknown_commands)

    def skip(self, segment: Segment, where: str) -> None:
        text = quote(snippet(unescape_comment(segment.text)))
        self.report.skipped(segment.line, f"comment {where}: {text}")

    # --- IDs ---------------------------------------------------------------------------------

    def claim_id(self, candidate: str | None, line: int, what: str) -> str | None:
        """Use an ID written in the file, unless it's invalid or already taken."""
        if not candidate:
            return None
        if not _ID.fullmatch(candidate):
            self.report.problem(
                line,
                f"{what}: {quote(candidate)} isn't a valid ID "
                "(lowercase letters, digits, _ and -); generated one instead",
            )
            return None
        if candidate in self.used_ids:
            self.report.problem(line, f"{what}: the ID {candidate} is used twice; generated one")
            return None
        self.used_ids.add(candidate)
        return candidate

    def generate_id(self, base: str, line: int, what: str) -> str:
        """A readable ID from a name, e.g. `northwind_traders`, made unique with a number."""
        stem = slug(base)[:56] or "item"
        candidate, number = stem, 2
        while candidate in self.used_ids or candidate in self.reserved_ids:
            candidate, number = f"{stem}_{number}", number + 1
        self.used_ids.add(candidate)
        self.report.generated(line, f"{candidate} for {what}")
        return candidate

    # --- Role types --------------------------------------------------------------------------

    def role_type(self, label: str, line: int) -> str:
        """The role type a label names, created on first sight and matched by shared parts.

        `DATA / ML`, `ML / data` and `DATA / ML PIPELINES` share the part `data`: one type.
        """
        key = label.strip().casefold()
        if key in self._role_type_by_label:
            return self._role_type_by_label[key]
        parts = label_parts(label)
        overlaps = [(len(rt.parts & parts), index) for index, rt in enumerate(self.role_types)]
        best, index = max(overlaps, default=(0, -1))
        if best:
            role_type = self.role_types[index]
            role_type.parts |= parts
            self.report.check(
                line, f"{quote(label.strip())} is role type {role_type.id} ({role_type.name})"
            )
        else:
            taken = {rt.id for rt in self.role_types}
            stem = slug(label.split("/")[0])[:28] or "role_type"
            role_id, number = stem, 2
            while role_id in taken:
                role_id, number = f"{stem}_{number}", number + 1
            role_type = RoleTypeDraft(role_id, display_label(label), parts)
            self.role_types.append(role_type)
            self.report.check(line, f"new role type {role_id} ({role_type.name})")
        self._role_type_by_label[key] = role_type.id
        return role_type.id

    def match_role_types(self, item: str) -> list[str]:
        """Role types named exactly by a list item such as `ML/data` or `pure platform roles`."""
        text = re.sub(r"^(?:any|all|pure|purely)\s+", "", item.casefold().strip())
        text = re.sub(r"\s+(?:roles?|work|jobs?|positions?|employers?|teams?)$", "", text)
        ids: list[str] = []
        for part in text.split("/"):
            for role_type in self.role_types:
                if part.strip() in role_type.parts and role_type.id not in ids:
                    ids.append(role_type.id)
        return ids

    def keep_or_cut(self, segment: Segment, what: str) -> list[str]:
        """The role types a `KEEP for:` or `CUT for:` list names. The rest stays in the note."""
        ids: list[str] = []
        unmatched: list[str] = []
        for item in list_items(unescape_comment(segment.value)):
            found = self.match_role_types(item)
            ids.extend(role_id for role_id in found if role_id not in ids)
            if not found:
                unmatched.append(item)
        verb = "keep for" if segment.tag == "KEEP" else "cut for"
        message = f"{what}: {verb} {', '.join(ids) or '(no role type)'}"
        if unmatched:
            message += f"; not role types, so only in the note: {', '.join(unmatched)}"
        self.report.check(segment.line, message)
        return ids
