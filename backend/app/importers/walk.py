"""Walking a scanned master resume: which comments belong to which heading or bullet.

Comments above a heading or bullet belong to it, as does a comment at the end of its last line.
Comments after an item's last bullet, such as benched bullets, belong to the item. Anything else
is a loose group, kept with where it was found, for the importer to sort by tag.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.importers.latex import Blank, Command, Comment, Event, Text, to_plain
from app.importers.report import ImportReport
from app.importers.tags import CommentLine, Segment, comment_lines, segments

ARITY: dict[str, int] = {
    "section": 1,
    "resumeSubheading": 4,
    "resumeSubheadingOneLine": 2,
    "resumeProjectHeading": 2,
    "resumeItem": 1,
    "resumeItemListStart": 0,
    "resumeItemListEnd": 0,
    "resumeSubHeadingListStart": 0,
    "resumeSubHeadingListEnd": 0,
    "begin": 1,
    "end": 1,
    "href": 2,
    "textbf": 1,
}
"""The template's commands and their brace arguments. `\\textbf` may take a second group."""
OPTIONAL_GROUP = frozenset({"textbf"})

_SECTIONS: dict[str, str] = {
    "education": "education",
    "work experience": "work",
    "experience": "work",
    "professional experience": "work",
    "projects": "projects",
    "personal projects": "projects",
    "technical skills": "skills",
    "skills": "skills",
}
_HEADINGS = frozenset({"resumeSubheading", "resumeSubheadingOneLine", "resumeProjectHeading"})
_STRUCTURE = frozenset(
    {"resumeItemListStart", "resumeItemListEnd", "resumeSubHeadingListStart", "begin", "end"}
)


@dataclass
class BulletDraft:
    command: Command
    comments: list[Segment]
    """Comments above the bullet, and any comment at the end of its last line."""


@dataclass
class ItemDraft:
    """A role, education entry or project, with its comments and bullets."""

    section: str
    command: Command
    comments: list[Segment]
    """Comments above the heading, and any comment at the end of its last line."""
    bullets: list[BulletDraft] = field(default_factory=list[BulletDraft])
    trailing: list[Segment] = field(default_factory=list[Segment])
    """Comments after the last bullet, e.g. benched bullets."""


@dataclass
class Walk:
    heading: list[Event] = field(default_factory=list[Event])
    """What's between `\\begin{center}` and `\\end{center}`: the name and contact details."""
    items: list[ItemDraft] = field(default_factory=list[ItemDraft])
    skill_lines: list[tuple[Command, list[Segment]]] = field(
        default_factory=list[tuple[Command, list[Segment]]]
    )
    """Each `\\textbf{Label}{: skills}` line of the skills section, with the comments above it."""
    skill_comments: list[Segment] = field(default_factory=list[Segment])
    """Comments after the last skills line, such as the gaps list."""
    loose: list[tuple[str, list[CommentLine | None]]] = field(
        default_factory=list[tuple[str, list[CommentLine | None]]]
    )
    """Comment groups outside any item, with where they were found."""


def walk(events: Sequence[Event], report: ImportReport) -> Walk:
    """Attach each comment group to the heading or bullet below it."""
    result = Walk()
    pending: list[Comment | Blank] = []
    section: str | None = None
    item: ItemDraft | None = None
    target: ItemDraft | BulletDraft | None = None
    heading: list[Event] | None = None

    def take() -> list[CommentLine | None]:
        lines = comment_lines(pending)
        pending.clear()
        return lines

    for event in events:
        if heading is not None:
            if isinstance(event, Command) and event.name == "end" and event.args == ("center",):
                result.heading, heading = heading, None
            else:
                heading.append(event)
        elif isinstance(event, Comment) and event.trailing and target is not None:
            target.comments.extend(segments(comment_lines([event])))
        elif isinstance(event, Comment | Blank):
            pending.append(event)
        elif isinstance(event, Text):
            text = to_plain(event.text)
            report.skipped(event.line, f'text outside the template\'s commands: "{text}"')
        elif event.name == "begin" and event.args == ("center",) and section is None:
            result.loose.append(("before the heading", take()))
            heading = []
        elif event.name == "section":
            title = to_plain(event.args[0]) if event.args else ""
            result.loose.append((f"before the {title} section", take()))
            section = _SECTIONS.get(title.casefold())
            item = target = None
            if section is None:
                report.skipped(event.line, f'the "{title}" section: not one the importer reads')
        elif event.name in _HEADINGS and section in ("education", "work", "projects"):
            item = target = ItemDraft(section, event, segments(take()))
            result.items.append(item)
        elif event.name == "resumeItem" and item is not None:
            target = BulletDraft(event, segments(take()))
            item.bullets.append(target)
        elif event.name == "resumeItemListEnd" and item is not None:
            item.trailing.extend(segments(take()))
            target = item
        elif event.name == "resumeSubHeadingListEnd":
            result.loose.append((f"at the end of the {section} list", take()))
            item = target = None
        elif event.name == "textbf" and section == "skills":
            result.skill_lines.append((event, segments(take())))
            target = None
        elif event.name == "end" and section == "skills" and event.args == ("itemize",):
            result.skill_comments.extend(segments(take()))
        elif event.name not in _STRUCTURE:
            report.skipped(event.line, f"\\{event.name} where the importer doesn't read it")
    result.loose.append(("at the end of the document", take()))
    return result
