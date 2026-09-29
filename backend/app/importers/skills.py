"""The skills section: the skills, their proficiency tiers and notes, the gaps and the presets."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.importers.context import ImportContext
from app.importers.latex import Command, unescape_comment
from app.importers.report import ImportReport
from app.importers.tags import CommentLine, Segment, hanging_entries
from app.importers.values import note_of, quote, split_list
from app.schema.common import Note
from app.schema.profile import Skill, SkillLine

Tier = Literal["strong", "working", "academic"]
_TIERS: dict[str, Tier] = {"strong": "strong", "working": "working", "academic": "academic"}


@dataclass
class _SkillDraft:
    name: str
    category: str
    tier: Tier | None = None
    verify: bool = False
    notes: list[Note] = field(default_factory=list[Note])


def read_skills(
    lines: Sequence[tuple[Command, list[Segment]]],
    comments: Sequence[Segment],
    ctx: ImportContext,
) -> tuple[list[Skill], list[str]]:
    """The skills from `\\textbf{Label}{: skills}` lines, and the gaps list.

    Comments above the lines and in `comments` set proficiency tiers (`Proficiency tiers:`),
    flag skills (`VERIFY BEFORE SHIPPING:`), list gaps (`NOT ON THE RESUME`) or become notes on
    the skills they name.
    """
    skills: dict[str, _SkillDraft] = {}
    found: list[Segment] = []
    for command, above in lines:
        found.extend(above)
        if len(command.args) < 2:
            ctx.report.skipped(command.line, "a skills line without its `{: skills}` group")
            continue
        category = ctx.plain(command.args[0]).rstrip(":").strip()
        for name in split_list(ctx.plain(command.args[1]).lstrip(":")):
            if name.casefold() in skills:
                ctx.report.problem(command.line, f"{name} is listed twice; kept the first")
            else:
                skills[name.casefold()] = _SkillDraft(name, category)

    gaps: list[str] = []
    for segment in [*found, *comments]:
        if segment.tag == "TIERS":
            _tiers(segment, skills, ctx.report)
        elif segment.tag == "GAPS":
            _gaps(segment, gaps)
        elif segment.tag in (None, "NOTE", "WARNING", "UPGRADE", "VERIFY"):
            mentioned = _mentioned(unescape_comment(segment.text), skills.values())
            for skill in mentioned:
                skill.verify = skill.verify or segment.tag == "VERIFY"
                skill.notes.append(note_of(segment))
            if not mentioned:
                ctx.skip(segment, "in the skills section, naming no listed skill")
        else:
            ctx.skip(segment, "in the skills section")
    for gap in gaps:
        if skills.pop(gap.casefold(), None):
            ctx.report.problem(None, f"{gap} is both a skill and a gap; kept it as a gap only")

    ctx.allowed_terms = [skill.name for skill in skills.values()]
    models = [
        Skill(
            name=skill.name,
            category=skill.category,
            tier=skill.tier,
            verify_before_shipping=skill.verify,
            notes=tuple(skill.notes),
        )
        for skill in skills.values()
    ]
    return models, gaps


def preset_rows(
    found: Sequence[Segment], report: ImportReport
) -> list[tuple[int, str, list[SkillLine]]]:
    """Skills presets from a `SKILLS PRESETS` block: a role-type label, then `Label: skills` lines.

    Returns (line, role-type label, skill lines); the importer resolves the role types.
    """
    rows: list[tuple[int, str, list[SkillLine]]] = []
    for segment in found:
        body = segment.lines[1:]
        if segment.tag != "PRESETS" or not body:
            continue
        label_indent = min(line.indent for line in body)
        groups: list[tuple[CommentLine, list[CommentLine]]] = []
        for line in body:
            if line.indent == label_indent:
                groups.append((line, []))
            elif groups:
                groups[-1][1].append(line)
        for label_line, item_lines in groups:
            skill_lines: list[SkillLine] = []
            for first, entry in hanging_entries(item_lines):
                label, colon, skills = unescape_comment(entry).partition(":")
                if colon and label.strip():
                    skill_lines.append(
                        SkillLine(label=label.strip(), skills=tuple(split_list(skills)))
                    )
                else:
                    report.skipped(first.line, f"skills preset line {quote(entry)}")
            label = unescape_comment(label_line.text).rstrip(":").strip()
            rows.append((label_line.line, label, skill_lines))
    return rows


def _tiers(segment: Segment, skills: dict[str, _SkillDraft], report: ImportReport) -> None:
    for first, entry in hanging_entries(segment.lines[1:]):
        label, colon, names = unescape_comment(entry).partition(":")
        tier = _TIERS.get(label.strip().casefold())
        if not colon or tier is None:
            report.skipped(first.line, f"proficiency line {quote(entry)}")
            continue
        for name in split_list(names):
            if skill := skills.get(name.casefold()):
                skill.tier = tier
            else:
                report.problem(first.line, f"the {tier} tier lists {name}, which isn't a skill")


def _gaps(segment: Segment, gaps: list[str]) -> None:
    """`Mobile: no Swift / SwiftUI, no Flutter` -> Swift, SwiftUI, Flutter."""
    for _, entry in hanging_entries(segment.lines[1:]):
        _, colon, items = entry.partition(":")
        for item in split_list(unescape_comment(items if colon else entry)):
            item = re.sub(r"^no\s+", "", item, flags=re.IGNORECASE)
            parts = [part.strip() for part in item.split("/")]
            names = parts if all(len(part.split()) <= 2 for part in parts) else [item]
            known = {gap.casefold() for gap in gaps}
            gaps.extend(name for name in names if name and name.casefold() not in known)


def _mentioned(text: str, skills: Iterable[_SkillDraft]) -> list[_SkillDraft]:
    """The skills a comment names as whole terms: `C` isn't named by `C++` or `CSS`."""
    return [
        skill
        for skill in skills
        if re.search(rf"(?<![\w+#/.-]){re.escape(skill.name)}(?![\w+#/-])", text)
    ]
