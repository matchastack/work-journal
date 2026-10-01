"""ASCII characters in the text the app stores and outputs.

Imports and LLMs produce typographic characters that have plain ASCII versions, such as the en
dash beside the hyphen. The rules below replace them in code, which is more reliable than asking
a prompt to avoid them. Every string in a schema model goes through them (`app/schema/common.py`);
to keep another character out, add a rule here.

Characters without an ASCII version that keeps their meaning stay as they are: letters with
accents (names must be spelled right) and currency signs such as the euro.
"""

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import cast

RULES: tuple[tuple[str, str], ...] = (
    # An em dash joins clauses, so it becomes a hyphen with spaces, as in "shipped - fast".
    # (Two hyphens would print as an en dash in LaTeX.)
    (r"[ \t]*[\u2014\u2015][ \t]*", " - "),
    # Hyphens, the en dash (ranges such as "2-3 hours") and the minus sign.
    (r"[\u2010\u2011\u2012\u2013\u2212]", "-"),
    # Curly quotes and primes.
    (r"[\u2018\u2019\u201a\u201b\u2032]", "'"),
    (r"[\u201c\u201d\u201e\u201f\u2033]", '"'),
    ("\u2026", "..."),
    # Other spaces become a plain space; invisible characters go.
    (r"[\u00a0\u2002-\u200a\u202f]", " "),
    (r"[\u00ad\u200b\u2060\ufeff]", ""),
    # Symbols. "~" keeps the meaning of "about", which the number checker reads.
    ("\u00d7", "x"),
    ("\u2248", "~"),
    ("\u2264", "<="),
    ("\u2265", ">="),
    ("\u00b1", "+/-"),
    ("\u2192", "->"),
    ("\u2190", "<-"),
    ("\u2022", "*"),
    ("\u00b7", "|"),
    ("\u00a9", "(c)"),
    ("\u00ae", "(R)"),
    ("\u2122", "(TM)"),
)
"""(regular expression, replacement) pairs, applied in order."""

_COMPILED = tuple((re.compile(pattern), replacement) for pattern, replacement in RULES)


def plain(text: str) -> str:
    """`text` with every rule applied: `2\N{EN DASH}3 hours\N{HORIZONTAL ELLIPSIS}` becomes
    `2-3 hours...`."""
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
