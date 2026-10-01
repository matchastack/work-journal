"""Reading the values written in a master resume: dates, degrees, titles, lists and labels.

Pure functions. They return None for a value they can't read, and the importer reports it.
"""

import re
import unicodedata
from typing import Literal

from app.importers.latex import unescape_comment
from app.importers.tags import Segment
from app.schema.common import Note
from app.schema.profile import Strength, Verification

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b|\b(\d{4})-(\d{2})\b")
_ONGOING = re.compile(r"\b(?:present|current|now|ongoing)\b", re.IGNORECASE)
_OFFICIAL = re.compile(
    r"official\s+(?:record|title)[^\"\u201c]*[\"\u201c]([^\"\u201d]+)[\"\u201d]", re.IGNORECASE
)
_VARIANTS = re.compile(r"variants?\s*:\s*(.*)$", re.IGNORECASE)
_NOTE_KINDS: dict[str | None, Literal["note", "warning", "upgrade"]] = {
    "WARNING": "warning",
    "TODO": "warning",
    "VERIFY": "warning",
    "HONESTY": "warning",
    "UPGRADE": "upgrade",
}
_LABEL_TAGS = frozenset({"NOTE", "WARNING", "UPGRADE"})
"""Tags whose label the note's kind already says, so the note keeps only the text after it."""


def parse_dates(text: str) -> tuple[str | None, str | None] | None:
    """`Aug 2020 -- May 2024` -> ("2020-08", "2024-05"); `Aug 2024 -- Present` -> ("2024-08", None).

    Returns (None, None) for an empty date and None for one it can't read. A year without a
    month can't be read, because the importer never invents a month.
    """
    text = text.strip()
    if not text:
        return None, None
    months: list[str] = []
    for match in _MONTH_YEAR.finditer(text):
        if match[1]:
            month = _MONTHS.get(match[1][:3].casefold())
            if month is None:
                return None
            months.append(f"{match[2]}-{month:02d}")
        elif 1 <= int(match[4]) <= 12:
            months.append(f"{match[3]}-{match[4]}")
        else:
            return None
    leftover = _ONGOING.sub("", _MONTH_YEAR.sub("", text))
    if re.sub(r"[\s\u2013\u2014,.-]|\bto\b", "", leftover):
        return None
    ongoing = bool(_ONGOING.search(text))
    if len(months) == 1:
        return months[0], None if ongoing else months[0]
    if len(months) == 2 and not ongoing:
        return months[0], months[1]
    return None


def dates_and_place(first: str, second: str) -> tuple[str, str | None]:
    """Split a heading's right-hand side into dates and a place; either line may hold the dates."""
    for dates, place in ((first, second), (second, first)):
        if dates and parse_dates(dates) is not None:
            return dates, place or None
    return first, second or None


def split_list(text: str) -> list[str]:
    """Split a comma-separated list, leaving commas inside parentheses alone."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def degree_parts(degree: str) -> tuple[str, str, str | None] | None:
    """`BSc (Hons) in Physics, Cum Laude` -> ("BSc (Hons)", "Physics", "Cum Laude")."""
    study_type, found, rest = degree.partition(" in ")
    if not found:
        return None
    area, _, honours = rest.partition(", ")
    return study_type.strip(), area.strip(), honours.strip() or None


def title_parts(segment: Segment) -> tuple[str | None, list[str], Note]:
    """The official title, the approved title variants and the `TITLE:` comment as a note.

    Reads `the official title is "Analyst"` and an `Approved variants:` list whose entries are
    separated by `|` and may continue on indented lines.
    """
    official = _OFFICIAL.search(segment.text)
    official_title = unescape_comment(official[1]) if official else None
    lines = segment.lines
    index = next((i for i, line in enumerate(lines) if _VARIANTS.search(line.text)), None)
    if index is None:
        return official_title, [], note_of(segment)
    marker = _VARIANTS.search(lines[index].text)
    assert marker is not None
    end = index + 1
    while end < len(lines) and lines[end].indent > lines[0].indent:
        end += 1
    pieces = [marker[1], *(line.text for line in lines[index + 1 : end])]
    variants = [unescape_comment(piece) for piece in " | ".join(pieces).split("|") if piece.strip()]
    listed = f"{lines[index].text[: marker.start(1)].rstrip()} {' | '.join(variants)}"
    if lines[end:] and not listed.endswith((".", ";")):
        listed += "."
    before = [line.text for line in lines[:index]]
    after = [line.text for line in lines[end:]]
    return (
        official_title,
        variants,
        Note(text=unescape_comment(" ".join([*before, listed, *after]))),
    )


def parse_strength(value: str) -> tuple[Strength, str] | None:
    """`low (HIGH for data roles)` -> ("low", "HIGH for data roles")."""
    match = re.fullmatch(r"(high|medium|low)\b\s*(.*)", value.strip(), re.IGNORECASE)
    if match is None:
        return None
    rest = match[2].strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    return _strength(match[1]), rest


def strength_exception(text: str) -> tuple[Strength, str] | None:
    """`HIGH for data roles` -> ("high", "data roles")."""
    match = re.fullmatch(r"(high|medium|low)\s+for\s+(.+)", text.strip(), re.IGNORECASE)
    return (_strength(match[1]), match[2]) if match else None


def parse_verification(value: str) -> tuple[Verification, str | None] | None:
    """`partial (scale unknown)` -> ("partial", "scale unknown").

    A mixed answer such as `numbers yes, cause no` is partial, with the answer as its note.
    """
    match = re.fullmatch(r"(yes|partial|no)\b[\s,;:-]*(.*)", value.strip(), re.IGNORECASE)
    if match:
        note = match[2].strip()
        if note.startswith("(") and note.endswith(")"):
            note = note[1:-1].strip()
        word = match[1].casefold()
        verification: Verification = "yes" if word == "yes" else "no" if word == "no" else "partial"
        return verification, unescape_comment(note) or None
    if re.search(r"\byes\b", value, re.IGNORECASE) and re.search(r"\bno\b", value, re.IGNORECASE):
        return "partial", unescape_comment(value)
    return None


def drop_phrase(text: str, phrase: str) -> str | None:
    """The bullet without `phrase`, or None if the phrase isn't in it."""
    if not phrase or phrase not in text:
        return None
    dropped = re.sub(r"\s{2,}", " ", text.replace(phrase, "", 1))
    return re.sub(r"\s+([,.;:])", r"\1", dropped).strip()


def append_phrase(text: str, phrase: str) -> str:
    """The bullet with `phrase` added before its final full stop."""
    base = text.rstrip()
    period = "." if base.endswith(".") else ""
    joiner = "" if phrase[:1] in ",;:" else " "
    return f"{base.rstrip('.')}{joiner}{phrase}{period}"


def note_of(segment: Segment) -> Note:
    """A comment as a note; `WARNING:` and `UPGRADE AVAILABLE:` set the note's kind."""
    text = segment.value if segment.tag in _LABEL_TAGS and segment.value.strip() else segment.text
    return Note(kind=_NOTE_KINDS.get(segment.tag, "note"), text=unescape_comment(text))


def rule_text(segment: Segment) -> str:
    return unescape_comment(segment.value if segment.tag == "RULE" else segment.text)


def label_parts(label: str) -> set[str]:
    """`DATA / ML PIPELINES` -> {"data", "ml pipelines"}."""
    return {part.strip().casefold() for part in label.split("/") if part.strip()}


def display_label(label: str) -> str:
    """`DATA / ML PIPELINES` -> `Data / ML Pipelines`; short acronyms such as `ML` stay."""
    words = re.split(r"(\s+|/)", label.strip().rstrip(":"))
    return "".join(w.capitalize() if len(w) > 3 and w.isupper() else w for w in words)


def slug(text: str) -> str:
    """`R&D Labs, Inc.` -> `r_d_labs_inc`: a readable ID from a name."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.casefold()).strip("_")


def host(url: str) -> str:
    match = re.match(r"(?:https?://)?(?:www\.)?([^/]+)", url.strip())
    return match[1].casefold() if match else ""


def snippet(text: str, width: int = 70) -> str:
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def quote(text: str) -> str:
    return f'"{text}"'


def _strength(word: str) -> Strength:
    lowered = word.casefold()
    return "high" if lowered == "high" else "low" if lowered == "low" else "medium"
