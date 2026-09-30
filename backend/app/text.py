"""Plain characters in the text the app stores.

Imports and LLMs produce typographic characters that look like plain ones, such as the en dash
beside the hyphen. The rules below replace them in code, which is more reliable than asking a
prompt to avoid them. Every string in a schema model goes through them (`app/schema/common.py`);
to keep another character out, add a rule here.
"""

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import cast

RULES: tuple[tuple[str, str], ...] = (("\N{EN DASH}", "-"),)
"""(regular expression, replacement) pairs, applied in order."""

_COMPILED = tuple((re.compile(pattern), replacement) for pattern, replacement in RULES)


def plain(text: str) -> str:
    """`text` with every rule applied: `2\N{EN DASH}3 hours` becomes `2-3 hours`."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


def plain_strings(value: object) -> object:
    """`value` with `plain` applied to every string in it, inside dicts, lists, tuples and sets.

    Dict keys are left alone: they name fields.
    """
    if isinstance(value, str):
        return plain(value)
    if isinstance(value, Mapping):
        items = cast("Mapping[object, object]", value).items()
        return {key: plain_strings(item) for key, item in items}
    if isinstance(value, list | tuple):
        converted = [plain_strings(item) for item in cast("Sequence[object]", value)]
        return tuple(converted) if isinstance(value, tuple) else converted
    if isinstance(value, set | frozenset):
        return {plain_strings(item) for item in cast("AbstractSet[object]", value)}
    return value
