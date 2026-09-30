"""Reading LaTeX resume sources: brace groups, a flat stream of events, and plain text.

This is not a LaTeX parser. It reads the small, regular subset that resume templates such as
Jake's Resume use: commands with brace arguments, `%` comments and plain text.
"""

import bisect
import re
from collections.abc import Mapping
from dataclasses import dataclass


class LatexError(ValueError):
    """The source can't be read, e.g. a brace group is never closed."""


@dataclass(frozen=True)
class Comment:
    line: int
    text: str
    """Everything after the leading `%` signs, with its indentation."""
    trailing: bool = False
    """Code comes before the comment on the same line."""


@dataclass(frozen=True)
class Blank:
    """A line with nothing but whitespace."""

    line: int


@dataclass(frozen=True)
class Command:
    line: int
    name: str
    args: tuple[str, ...]
    """The raw LaTeX of each brace argument that was found."""


@dataclass(frozen=True)
class Text:
    """Plain content outside the known commands, e.g. a phone number in the heading."""

    line: int
    text: str


Event = Comment | Blank | Command | Text

_NAME = re.compile(r"\\([A-Za-z]+\*?|.)", re.DOTALL)
_LAYOUT = frozenset({"vspace", "vspace*", "hspace", "hspace*"})
"""Commands whose argument is a length, not content."""


def read_group(source: str, start: int) -> tuple[str, int]:
    """Read the brace group that opens at `source[start]`.

    Returns the group's content and the index just after its closing brace. Escaped braces don't
    count, and `%` comments inside the group are dropped, as LaTeX does.
    """
    if source[start] != "{":
        raise LatexError(f"expected '{{' at offset {start}")
    depth = 0
    content: list[str] = []
    pos = start
    while pos < len(source):
        char = source[pos]
        if char == "\\":
            content.append(source[pos : pos + 2])
            pos += 2
            continue
        if char == "%":
            end = source.find("\n", pos)
            pos = len(source) if end == -1 else end + 1
            continue
        if char == "{":
            depth += 1
            if depth > 1:
                content.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(content), pos + 1
            content.append(char)
        else:
            content.append(char)
        pos += 1
    raise LatexError(f"the brace group at offset {start} is never closed")


def scan(
    source: str,
    arity: Mapping[str, int],
    *,
    optional_group: frozenset[str] = frozenset(),
    first_line: int = 1,
) -> list[Event]:
    """Split LaTeX source into comments, blank lines, known commands and plain text.

    `arity` gives the number of brace arguments of each command to report; other commands are
    kept as text. Commands in `optional_group` take one more group if it follows immediately,
    as in `\\textbf{Languages}{: Python, SQL}`. Line numbers start at `first_line`.
    """
    line_starts = [0, *(match.end() for match in re.finditer("\n", source))]

    def line_at(pos: int) -> int:
        return first_line + bisect.bisect_right(line_starts, pos) - 1

    events: list[Event] = []
    text: list[str] = []
    text_line = first_line
    code_on_line = False
    content_on_line = False

    def flush_text() -> None:
        raw = "".join(text)
        text.clear()
        if to_plain(raw):
            events.append(Text(text_line, raw.strip()))

    pos = 0
    while pos < len(source):
        char = source[pos]
        if char == "\n":
            flush_text()
            if not content_on_line:
                events.append(Blank(line_at(pos)))
            code_on_line = content_on_line = False
            pos += 1
        elif char == "%":
            flush_text()
            end = source.find("\n", pos)
            end = len(source) if end == -1 else end
            comment = source[pos:end].lstrip("%").rstrip()
            events.append(Comment(line_at(pos), comment, trailing=code_on_line))
            content_on_line = True
            pos = end
        elif char == "\\" and (name := _NAME.match(source, pos)) and name[1] in arity:
            flush_text()
            try:
                args, pos = _read_args(
                    source, name.end(), arity[name[1]], name[1] in optional_group
                )
            except LatexError as error:
                raise LatexError(f"line {line_at(pos)}: {error}") from error
            events.append(Command(line_at(name.start()), name[1], args))
            code_on_line = content_on_line = True
        elif char == "\\" and (name := _NAME.match(source, pos)) and name[1] in _LAYOUT:
            flush_text()
            pos = _skip_group(source, name.end())
            code_on_line = content_on_line = True
        else:
            if not text:
                text_line = line_at(pos)
            step = 2 if char == "\\" else 1
            text.append(source[pos : pos + step])
            if not char.isspace():
                code_on_line = content_on_line = True
            pos += step
    flush_text()
    return events


def _read_args(
    source: str, pos: int, count: int, optional_group: bool
) -> tuple[tuple[str, ...], int]:
    args: list[str] = []
    for _ in range(count):
        start = _skip_space(source, pos)
        if start >= len(source) or source[start] != "{":
            break
        arg, pos = read_group(source, start)
        args.append(arg)
    if optional_group and pos < len(source) and source[pos] == "{":
        arg, pos = read_group(source, pos)
        args.append(arg)
    if pos < len(source) and source[pos] == "[":
        pos = _skip_brackets(source, pos)
    return tuple(args), pos


def _skip_space(source: str, pos: int) -> int:
    while pos < len(source) and source[pos].isspace():
        pos += 1
    return pos


def _skip_group(source: str, pos: int) -> int:
    start = _skip_space(source, pos)
    if start < len(source) and source[start] == "{":
        return read_group(source, start)[1]
    return pos


def _skip_brackets(source: str, pos: int) -> int:
    """Skip an optional `[...]` argument, which may contain brace groups."""
    depth = 0
    while pos < len(source):
        char = source[pos]
        if char == "{":
            pos = read_group(source, pos)[1]
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    raise LatexError("an optional argument is never closed")


# --- Plain text ------------------------------------------------------------------------------

_ESCAPES: dict[str, str] = {
    "%": "%", "&": "&", "$": "$", "#": "#", "_": "_", "{": "{", "}": "}",
    "\\": " ", " ": " ", ",": " ", "-": "", "/": "",
}  # fmt: skip
_SYMBOLS: dict[str, str] = {
    "textendash": "\u2013", "textemdash": "\u2014", "ldots": "\u2026", "dots": "\u2026",
    "textbar": "|", "textbullet": "\u2022", "textasciitilde": "~", "textbackslash": "\\",
    "times": "\u00d7", "sim": "~", "approx": "\u2248", "pm": "\u00b1", "cdot": "\u00b7",
    "to": "\u2192", "rightarrow": "\u2192", "geq": "\u2265", "leq": "\u2264",
    "ge": "\u2265", "le": "\u2264",
}  # fmt: skip
_DROPPED_GROUPS: dict[str, int] = {
    "href": 1,
    "raisebox": 1,
    "label": 1,
    **dict.fromkeys(_LAYOUT, 1),
}
"""Commands whose first groups aren't content, e.g. the URL of `\\href{url}{text}`."""
_FORMATTING = frozenset({
    "textbf", "textit", "emph", "underline", "texttt", "textsc", "textrm", "textsf", "textnormal",
    "mbox", "url", "tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large",
    "LARGE", "huge", "Huge", "scshape", "bfseries", "itshape", "centering", "item", "hfill",
    "newline", "linebreak", "noindent",
})  # fmt: skip
"""Commands that only format their content, so plain text keeps the content and drops them."""


def to_plain(tex: str, unknown: set[str] | None = None) -> str:
    """Turn LaTeX markup into plain text: `\\%` -> `%`, `--` -> en dash, `\\textbf{x}` -> `x`.

    The names of commands it doesn't know are added to `unknown`; their arguments are kept.
    """
    out: list[str] = []
    pos = 0
    while pos < len(tex):
        char = tex[pos]
        if char == "\\" and (match := _NAME.match(tex, pos)):
            name = match[1]
            pos = match.end()
            if name in _ESCAPES:
                out.append(_ESCAPES[name])
            elif name in _SYMBOLS:
                out.append(_SYMBOLS[name])
            elif name in _DROPPED_GROUPS:
                for _ in range(_DROPPED_GROUPS[name]):
                    pos = _skip_group(tex, pos)
            elif name not in _FORMATTING and unknown is not None:
                unknown.add(name)
        elif char == "%":
            end = tex.find("\n", pos)
            pos = len(tex) if end == -1 else end + 1
        else:
            if char == "~":
                out.append(" ")
            elif char not in "{}$":
                out.append(char)
            pos += 1
    return _typeset("".join(out))


def unescape_comment(text: str) -> str:
    """Plain text of a comment written in LaTeX style: `\\&` -> `&`, `--` -> en dash."""
    for symbol in "%&$#_":
        text = text.replace("\\" + symbol, symbol)
    return _typeset(text)


def _typeset(text: str) -> str:
    text = text.replace("---", "\u2014").replace("--", "\u2013")
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    return re.sub(r"\s+", " ", text).strip()
