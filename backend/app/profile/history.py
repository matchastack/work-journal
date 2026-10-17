"""What each profile version changed, in the owner's terms (FR-PRF-2, FR-PRF-3). Nothing here does
I/O.

`version_changes` compares a version with its parent (`profile_ops.diff`) and describes each
difference: what it's about, such as "Text of a bullet under Engineer, Northwind Traders", the
bullet it's in, and the value before and after, a word at a time for text of more than one word.

`describe_path` reads a profile path such as `work[northwind].highlights[nw_export].text`, as
diffs and resume-rule findings write them, in the same terms. It knows basics, roles, education
entries, projects and their bullets; any other path is shown as it is.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from app import profile_ops
from app.inbox.describe import DiffPart, item_label, word_diff
from app.profile_ops import ITEM_SECTIONS
from app.schema.common import Model
from app.schema.profile import Education, Profile, Project, Role

_SEGMENT = r"([A-Za-z]+)(?:\[([^\]]*)\])?"
_PATH = re.compile(rf"{_SEGMENT}(?:\.{_SEGMENT})*")
_SEGMENTS = re.compile(rf"(?:^|\.){_SEGMENT}")
"""A path is `name` or `name[key]` segments joined by dots. A key may hold dots, as in
`skills[Node.js]`."""

KINDS = {"work": "Role", "education": "Education entry", "projects": "Project"}

FIELD_LABELS = {
    "position": "title",
    "studyType": "degree",
    "area": "area of study",
    "keywords": "tech stack",
    "url": "link",
    "sources": "facts",
    "highlights": "bullets",
    "countryCode": "country code",
}
"""Labels for the fields whose JSON name, split into words, doesn't say what they hold."""

KEYED_LABELS = {"profiles": "{key} profile", "courseworkSubsets": "{key} coursework"}
"""How to name one entry of a list field, by its key."""

FACT_LISTS = frozenset({"sources", "answerFactIds"})
"""Lists of fact IDs, which mean nothing to the owner, so they're counted instead."""


class VersionChange(Model):
    """One difference between a version and its parent."""

    kind: Literal["added", "removed", "changed"]
    path: str
    """Where it is, e.g. `work[northwind].highlights[nw_export].text`."""
    title: str
    """What it's about, e.g. `Text of a bullet under Engineer, Northwind Traders`."""
    context: str | None
    """The bullet's text, when the change is to another of its fields."""
    before: str | None
    after: str | None
    diff: tuple[DiffPart, ...]
    """The text before and after, a word at a time, when both are text of more than one word."""


@dataclass(frozen=True)
class _Place:
    """Where a path points: basics or an item, a bullet under the item, and fields inside."""

    section: str
    item_id: str | None
    bullet_id: str | None
    fields: tuple[tuple[str, str | None], ...]


def version_changes(before: Profile | None, after: Profile) -> tuple[VersionChange, ...]:
    """What changed from `before`, a version's parent, to `after`. The first version, which has
    no parent, changed nothing."""
    if before is None:
        return ()
    return tuple(_change(change, before, after) for change in profile_ops.diff(before, after))


def describe_path(path: str, *profiles: Profile) -> str:
    """`path` in words, e.g. `End date of Engineer, Northwind Traders`, with items labelled as in
    the first of `profiles` that has them."""
    place = _parse(path)
    if place is None:
        return path
    target, joiner = _target(place, profiles)
    if not place.fields:
        return _capitalised(target)
    return _capitalised(f"{_fields(place)} {joiner} {target}")


def _change(change: profile_ops.Change, before: Profile, after: Profile) -> VersionChange:
    place = _parse(change.path)
    old, new = _display(change.before, change.path), _display(change.after, change.path)
    words = isinstance(change.before, str) and isinstance(change.after, str)
    reworded = words and any(" " in str(text) for text in (change.before, change.after))
    return VersionChange(
        kind=change.kind,
        path=change.path,
        title=_title(change, place, (after, before)),
        context=_context(place, (after, before)),
        before=old,
        after=new,
        diff=word_diff(str(change.before), str(change.after)) if reworded else (),
    )


def _title(change: profile_ops.Change, place: _Place | None, profiles: tuple[Profile, ...]) -> str:
    whole = place is not None and not place.fields and change.kind != "changed"
    if whole and place is not None and place.item_id is not None:
        label = _item_label(place.section, place.item_id, profiles)
        if place.bullet_id is not None:
            where = "under" if change.kind == "added" else "from"
            return f"Bullet {change.kind} {where} {label}"
        return f"{KINDS[place.section]} {change.kind}: {label}"
    title = describe_path(change.path, *profiles)
    if change.kind == "changed":
        return title
    return f"{change.kind.capitalize()}: {title}"


def _context(place: _Place | None, profiles: tuple[Profile, ...]) -> str | None:
    """The text of the bullet a change is in, unless the change is to the text itself, or to the
    whole bullet."""
    if place is None or place.bullet_id is None or not place.fields:
        return None
    if place.fields[0] == ("text", None):
        return None
    for profile in profiles:
        for bullet in profile.all_bullets():
            if bullet.id == place.bullet_id:
                return bullet.text
    return None


def _parse(path: str) -> _Place | None:
    if _PATH.fullmatch(path) is None:
        return None
    segments = [(match[1], match[2]) for match in _SEGMENTS.finditer(path)]
    (section, key), rest = segments[0], segments[1:]
    if section == "basics" and key is None:
        return _Place(section, None, None, tuple(rest))
    if section not in ITEM_SECTIONS or key is None:
        return None
    bullet = None
    if rest and rest[0][0] == "highlights" and rest[0][1] is not None:
        bullet, rest = rest[0][1], rest[1:]
    return _Place(section, key, bullet, tuple(rest))


def _target(place: _Place, profiles: tuple[Profile, ...]) -> tuple[str, str]:
    """What a place is in, and the word that joins a field to it."""
    if place.item_id is None:
        return "your basics", "in"
    label = _item_label(place.section, place.item_id, profiles)
    return (label if place.bullet_id is None else f"a bullet under {label}"), "of"


def _fields(place: _Place) -> str:
    """The fields a place points into, innermost first, e.g. `city of location`."""
    return " of ".join(_field(name, key, place) for name, key in reversed(place.fields))


def _field(name: str, key: str | None, place: _Place) -> str:
    if place.section == "work" and place.bullet_id is None and name == "name":
        label = "organisation"
    else:
        label = FIELD_LABELS.get(name) or re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower()
    if key is None:
        return label
    return KEYED_LABELS[name].format(key=key) if name in KEYED_LABELS else f"{label} ({key})"


def _item_label(section: str, item_id: str, profiles: tuple[Profile, ...]) -> str:
    for profile in profiles:
        items: tuple[Role | Education | Project, ...] = getattr(profile, section)
        for item in items:
            if item.id == item_id:
                return item_label(item)
    return item_id


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _display(value: Any, path: str) -> str | None:
    """A value as the owner reads it."""
    if value is None:
        return None
    if isinstance(value, list) and path.rsplit(".", 1)[-1] in FACT_LISTS:
        count = len(cast(list[Any], value))
        return f"{count} {'fact' if count == 1 else 'facts'}"
    return _text(value)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float | str):
        return str(value)
    if isinstance(value, list):
        items = cast(list[Any], value)
        if not items:
            return "none"
        separator = ", " if all(isinstance(item, str) for item in items) else "; "
        return separator.join(_text(item) for item in items)
    if isinstance(value, dict):
        return _entry(cast(dict[str, Any], value))
    return json.dumps(value)


def _entry(value: dict[str, Any]) -> str:
    """A list entry or nested object by what names it: a bullet's text, a role's title and
    organisation, an education entry's label, a profile's network, or a name or question."""
    if isinstance(value.get("text"), str):
        return value["text"]
    if isinstance(value.get("position"), str) and isinstance(value.get("name"), str):
        return f"{value['position']}, {value['name']}"
    if all(isinstance(value.get(key), str) for key in ("studyType", "area", "institution")):
        return f"{value['studyType']} in {value['area']}, {value['institution']}"
    if isinstance(value.get("network"), str):
        link = value.get("url") or value.get("username")
        return value["network"] if not link else f"{value['network']}: {link}"
    for key in ("name", "question"):
        if isinstance(value.get(key), str):
            return value[key]
    if set(value) <= {"city", "region", "countryCode"}:
        return ", ".join(str(part) for part in value.values() if part) or "none"
    return json.dumps(value)
