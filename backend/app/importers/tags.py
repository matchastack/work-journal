"""Structured comments in the master resume: tags such as `ID:`, `SWAPS:` and `NEEDS:`.

The master resume keeps its metadata in `%` comments next to each item. This module groups those
comment lines into segments, one per tag or paragraph, and parses the values of the tags that
have a fixed shape. The rules follow the file's own layout:

- a tagged line always starts a new segment;
- a line indented further than the segment's first line (a hanging indent) continues it;
- prose (`WARNING:`, `KEEP for:`, an untagged paragraph) also continues onto the next line
  while its sentence is unfinished, and ends at a blank comment line;
- block tags (`BENCHED`, `NOT YET BUILT`, `SKILLS PRESETS`, `OPEN QUESTIONS`) take their whole
  block, including blank comment lines.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.importers.latex import Blank, Comment

_TAGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, re.compile(pattern))
    for tag, pattern in (
        ("ID", r"ID\s*:\s*"),
        ("SWAPS", r"SWAPS?\s*:\s*"),
        ("NEEDS", r"NEEDS\s*:\s*"),
        ("WARNING", r"WARNING\s*:\s*|WARNING\b\s*|(?=\*{3})"),
        ("UPGRADE", r"UPGRADE AVAILABLE\s*:?\s*"),
        ("HONESTY", r"HONESTY BOUNDAR(?:Y|IES)\s*:?\s*"),
        ("NOTE", r"NOTE\s*:\s*"),
        ("TITLE", r"TITLES?\s*:\s*"),
        ("LOAD_BEARING", r"LOAD[- ]BEARING\b\s*"),
        ("SENSITIVE", r"SENSITIVE\b\s*"),
        ("KEEP", r"(?i:KEEP|INCLUDE|STRONGEST)\s+(?i:for)\s*:\s*"),
        ("CUT", r"(?i:CUT|OMIT)\s+(?i:for)\s*:\s*"),
        ("RULE", r"RULES?\s*:\s*"),
        ("VERIFY", r"VERIFY BEFORE SHIPPING\s*:?\s*"),
        ("QUESTIONS", r"OPEN QUESTIONS\b\s*"),
        ("OPEN_QUESTION", r"OPEN QUESTION\s*:\s*"),
        ("TODO", r"TODO\b\s*:?\s*"),
        ("BENCHED", r"BENCHED\b\s*"),
        ("PLANNED", r"(?:PLANNED|NOT YET BUILT)\b\s*"),
        ("SUBSETS", r"SUBSETS USED BEFORE\s*:?\s*"),
        ("TIERS", r"(?i:proficiency tiers)\b\s*"),
        ("GAPS", r"NOT ON THE RESUME\b\s*"),
        ("PRESETS", r"SKILLS PRESETS\b\s*"),
    )
)
_HANGING_ONLY = frozenset({"ID", "LOAD_BEARING", "SENSITIVE", "TODO", "SUBSETS", "TIERS", "GAPS"})
_BLOCKS = frozenset({"PLANNED", "PRESETS", "QUESTIONS"})
_RULE_LINE = re.compile(r"[-=]{3,}")
_BENCHED_ENTRY = re.compile(r"ID\s*:\s*[A-Za-z0-9_-]+\s*(?:--|\u2013|\u2014|\")")


@dataclass(frozen=True)
class CommentLine:
    line: int
    indent: int
    text: str
    """The comment without its `%` signs and indentation."""


@dataclass(frozen=True)
class Segment:
    tag: str | None
    """The canonical tag, e.g. `SWAPS`; None for an untagged paragraph."""
    lines: tuple[CommentLine, ...]

    @property
    def line(self) -> int:
        return self.lines[0].line

    @property
    def text(self) -> str:
        """All lines joined with spaces, still in LaTeX style."""
        return " ".join(line.text for line in self.lines)

    @property
    def value(self) -> str:
        """The text after the tag, e.g. the question IDs after `NEEDS:`."""
        return " ".join(self.value_lines)

    @property
    def value_lines(self) -> list[str]:
        """The value line by line: the first line without its tag, then the other lines."""
        first = self.lines[0].text
        return [first[tag_end(first) :], *(line.text for line in self.lines[1:])]


def tag_of(text: str) -> str | None:
    for tag, pattern in _TAGS:
        if pattern.match(text):
            return tag
    return None


def tag_end(text: str) -> int:
    """Where the value starts: after the tag and its colon."""
    for _, pattern in _TAGS:
        if match := pattern.match(text):
            return match.end()
    return 0


def comment_lines(events: Sequence[Comment | Blank]) -> list[CommentLine | None]:
    """The comment lines of a group; None marks a break (a blank line, `%` alone or a rule)."""
    lines: list[CommentLine | None] = []
    for event in events:
        if isinstance(event, Blank):
            lines.append(None)
            continue
        stripped = event.text.strip()
        if not stripped or _RULE_LINE.match(stripped):
            lines.append(None)
        else:
            indent = len(event.text) - len(event.text.lstrip())
            lines.append(CommentLine(event.line, indent, stripped))
    return lines


def segments(lines: Sequence[CommentLine | None]) -> list[Segment]:
    """Group comment lines into segments, one per tag or paragraph."""
    result: list[Segment] = []
    index = 0
    while index < len(lines):
        first = lines[index]
        if first is None:
            index += 1
            continue
        tag = tag_of(first.text)
        end = _segment_end(lines, index, tag)
        members = tuple(line for line in lines[index:end] if line is not None)
        result.append(Segment(tag, members))
        index = end
    return result


def _segment_end(lines: Sequence[CommentLine | None], start: int, tag: str | None) -> int:
    first = lines[start]
    assert first is not None
    end = start + 1
    if tag in _BLOCKS:
        while end < len(lines) and not _starts_other_block(lines[end], tag):
            end += 1
        return end
    if tag == "BENCHED":
        return _benched_end(lines, end)
    quotes = _QuoteState(first.text[tag_end(first.text) :]) if tag == "SWAPS" else None
    previous = first
    while end < len(lines):
        line = lines[end]
        if line is None:
            break
        tagged = tag_of(line.text) is not None
        hanging = line.indent > first.indent
        if quotes is not None:
            follows = quotes.open or (hanging and not tagged)
        elif tag in _HANGING_ONLY:
            follows = hanging and not tagged
        else:
            follows = not tagged and (hanging or not _ends_sentence(previous.text))
        if not follows:
            break
        if quotes is not None:
            quotes.feed(line.text)
        previous = line
        end += 1
    return end


def _starts_other_block(line: CommentLine | None, tag: str) -> bool:
    if line is None:
        return False
    other = tag_of(line.text)
    return other != tag and (other in _BLOCKS or other == "BENCHED")


def _benched_end(lines: Sequence[CommentLine | None], end: int) -> int:
    """Benched entries (`ID: x -- "text"` plus hanging lines), with breaks allowed between."""
    entry_indent: int | None = None
    last = end
    while end < len(lines):
        line = lines[end]
        end += 1
        if line is None:
            continue
        if _BENCHED_ENTRY.match(line.text):
            entry_indent = line.indent
        elif entry_indent is None or line.indent <= entry_indent:
            break
        last = end
    return last


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith((".", "!", "?"))


class _QuoteState:
    """Tracks open double quotes and parentheses across the lines of a `SWAPS` segment."""

    def __init__(self, text: str) -> None:
        self.in_quote = False
        self.depth = 0
        self.feed(text)

    @property
    def open(self) -> bool:
        return self.in_quote or self.depth > 0

    def feed(self, text: str) -> None:
        for char in text:
            if char == '"':
                self.in_quote = not self.in_quote
            elif not self.in_quote and char == "(":
                self.depth += 1
            elif not self.in_quote and char == ")":
                self.depth = max(0, self.depth - 1)


# --- Tag values ------------------------------------------------------------------------------


def id_fields(segment: Segment) -> dict[str, str]:
    """`ID: nw_x | STRENGTH: high | VERIFIED: yes` -> {"ID": "nw_x", "STRENGTH": "high", ...}."""
    fields: dict[str, str] = {}
    for part in re.split(r"\s+\|\s+", segment.text):
        key, _, value = part.partition(":")
        fields[key.strip().upper()] = value.strip()
    return fields


@dataclass(frozen=True)
class SwapEntry:
    raw: str
    verb: str | None
    """How to apply the phrase, e.g. `drop` or `append`; None for a plain alternative."""
    phrase: str
    context: str


_SWAP_START = re.compile(r'"|(?:drop|append|prepend|lead with|use)\s+"', re.IGNORECASE)
_SWAP_ENTRY = re.compile(
    r'^(?:(?P<verb>[A-Za-z][A-Za-z ]*?)\s+)?"(?P<phrase>[^"]*)"\s*(?P<rest>.*)$', re.DOTALL
)


def swap_entries(segment: Segment) -> tuple[list[SwapEntry], list[str]]:
    """The entries of a `SWAPS` segment, and the raw text of any entry that can't be parsed.

    Entries are `"phrase" (context)` or an instruction such as `drop "phrase" for X roles`.
    A new entry starts on a line that opens with a quote or an instruction, outside any quote or
    parenthesis left open by the previous line.
    """
    raw_entries: list[list[str]] = []
    state = _QuoteState("")
    for text in segment.value_lines:
        if not raw_entries or (not state.open and _SWAP_START.match(text)):
            raw_entries.append([text])
        else:
            raw_entries[-1].append(text)
        state.feed(text)
    entries: list[SwapEntry] = []
    unparsed: list[str] = []
    for parts in raw_entries:
        raw = " ".join(parts).strip()
        match = _SWAP_ENTRY.match(raw)
        if match is None:
            unparsed.append(raw)
            continue
        verb = match["verb"].lower() if match["verb"] else None
        entries.append(SwapEntry(raw, verb, match["phrase"], _context(match["rest"])))
    return entries, unparsed


def _context(rest: str) -> str:
    rest = rest.strip()
    if rest.startswith("(") and _closing_paren(rest) == len(rest) - 1:
        return rest[1:-1].strip()
    return re.sub(r"^(?:--|\u2013|\u2014)\s*", "", rest)


def _closing_paren(text: str) -> int:
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


_NEEDS = re.compile(r"NEEDS\s*:\s*(q_[a-z0-9_]+(?:\s*(?:,|and|&)\s*q_[a-z0-9_]+)*)")


def needed_questions(text: str) -> list[str]:
    """Question IDs after `NEEDS:` anywhere in the text, e.g. `Weak. NEEDS: q_x.`."""
    ids: list[str] = []
    for match in _NEEDS.finditer(text):
        ids.extend(re.findall(r"q_[a-z0-9_]+", match[1]))
    return list(dict.fromkeys(ids))


@dataclass(frozen=True)
class BenchedEntry:
    line: int
    id: str
    text: str
    reason: str | None


_BENCHED_FIELDS = re.compile(
    r"ID\s*:\s*(?P<id>[A-Za-z0-9_-]+)\s*(?:--|\u2013|\u2014)?\s*"
    r'(?:"(?P<quoted>[^"]*)"|(?P<plain>.*?))\s*(?:REASON\s*:\s*(?P<reason>.*))?$',
    re.DOTALL,
)


def benched_entries(segment: Segment) -> list[BenchedEntry]:
    """`ID: x -- "text"` entries with an optional `REASON:`, from a `BENCHED` segment."""
    grouped: list[list[CommentLine]] = []
    for line in segment.lines[1:]:
        if _BENCHED_ENTRY.match(line.text) or not grouped:
            grouped.append([line])
        else:
            grouped[-1].append(line)
    entries: list[BenchedEntry] = []
    for group in grouped:
        match = _BENCHED_FIELDS.match(" ".join(line.text for line in group))
        if match is None:
            continue
        text = match["quoted"] if match["quoted"] is not None else match["plain"]
        entries.append(BenchedEntry(group[0].line, match["id"], text.strip(), match["reason"]))
    return entries


def hanging_entries(lines: Sequence[CommentLine]) -> list[tuple[CommentLine, str]]:
    """Entries at the shallowest indent, each joined with its deeper continuation lines."""
    if not lines:
        return []
    indent = min(line.indent for line in lines)
    entries: list[tuple[CommentLine, list[str]]] = []
    for line in lines:
        if line.indent <= indent or not entries:
            entries.append((line, [line.text]))
        else:
            entries[-1][1].append(line.text)
    return [(first, " ".join(texts)) for first, texts in entries]


def list_items(value: str) -> list[str]:
    """`ML/data, search and ranking roles -- because ...` -> the listed items."""
    value = re.split(r"\s+(?:--|\u2013|\u2014)\s+", value, maxsplit=1)[0].strip().rstrip(".;")
    items = re.split(r",\s*|\s+and\s+|\s+or\s+", value)
    cleaned = (re.sub(r"^(?:and|or)\s+", "", item.strip()) for item in items)
    return [item for item in cleaned if item]
