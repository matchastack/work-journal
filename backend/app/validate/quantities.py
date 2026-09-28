"""Finding quantities in text: numbers with their unit, qualifier and shape.

Used by the number checker (`app.validate.numbers`) and to turn existing bullets into fact
metrics on import. Handles thousands separators, decimals, `%`, `x`, currency, time units, plain
counts, number words ("two"), vague magnitudes ("hundreds of"), ranges, before/after changes,
qualifiers ("about", "over", "200+") and LaTeX escapes (`33\\%`).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.schema.fact import ChangeValue, Metric, Qualifier, RangeValue, SingleValue

QuantityKind = Literal["single", "range", "change", "pair"]
"""A `pair` is a bare "A to B", which may be a range or a before/after change."""

Interval = tuple[float, float]

# --- Units -----------------------------------------------------------------------------------

_UNIT_ALIASES: dict[str, str] = {
    "%": "%", "percent": "%", "pct": "%",
    "pp": "pp",
    "x": "x", "\u00d7": "x", "times": "x", "fold": "x",
    "ms": "millisecond", "millisecond": "millisecond", "milliseconds": "millisecond",
    "s": "second", "sec": "second", "secs": "second", "second": "second", "seconds": "second",
    "min": "minute", "mins": "minute", "minute": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
    "day": "day", "days": "day",
    "week": "week", "weeks": "week",
    "month": "month", "months": "month",
    "yr": "year", "yrs": "year", "year": "year", "years": "year",
    "$": "$", "usd": "usd", "us$": "usd", "sgd": "sgd", "s$": "sgd",
    "eur": "eur", "€": "eur", "gbp": "gbp", "£": "gbp",
}  # fmt: skip

_UNIT_FAMILIES: dict[str, str] = {
    "%": "percent", "pp": "points", "x": "multiplier",
    "millisecond": "time", "second": "time", "minute": "time", "hour": "time",
    "day": "time", "week": "time", "month": "time", "year": "time",
    "$": "currency", "usd": "currency", "sgd": "currency", "eur": "currency", "gbp": "currency",
}  # fmt: skip

_NOT_UNITS = frozenset(
    {"to", "and", "or", "of", "the", "a", "an", "in", "on", "for", "per", "by", "with", "from",
     "at", "than", "more", "less", "over", "under", "into", "across"}
)  # fmt: skip


def normalize_unit(unit: str | None) -> str | None:
    """Canonical form of a unit: `minutes` -> `minute`, `percent` -> `%`, `papers` -> `paper`."""
    if unit is None:
        return None
    text = unit.strip().lower()
    if not text:
        return None
    if text.startswith("percentage point"):
        return "pp"
    if text.startswith("per cent"):
        return "%"
    first = re.split(r"[\s/]+", text)[0].lstrip("-")
    if first in _UNIT_ALIASES:
        return _UNIT_ALIASES[first]
    if len(first) > 3 and first.endswith("s") and not first.endswith("ss"):
        return first[:-1]
    return first


def units_compatible(text_unit: str | None, fact_unit: str | None) -> bool:
    """Known units (time, %, x, currency) must match; other nouns are left to the claim check."""
    if text_unit == fact_unit:
        return True
    if {text_unit, fact_unit} <= {"$", "usd", "sgd"}:
        return True
    return (
        _UNIT_FAMILIES.get(text_unit or "") is None and _UNIT_FAMILIES.get(fact_unit or "") is None
    )


# --- Finding quantities ------------------------------------------------------------------------

_WORD_NUMBERS: dict[str, int] = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip

_MAGNITUDES: dict[str, Interval] = {
    "dozens": (24, 143),
    "hundreds": (200, 999),
    "thousands": (2_000, 999_999),
    "millions": (2_000_000, 999_999_999),
}

_TOKEN = re.compile(
    r"(?<![\w.,])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"|\b(?P<word>{'|'.join(_WORD_NUMBERS)})\b"
    rf"|\b(?P<mag>{'|'.join(_MAGNITUDES)})\b",
    re.IGNORECASE,
)
_CURRENCY_BEFORE = re.compile(r"(US\$|S\$|\$|€|£)\s?$", re.IGNORECASE)
_QUALIFIER_BEFORE = re.compile(
    r"(?:\b(about|around|approximately|approx\.|roughly|nearly|almost|over|above|more than"
    r"|at least|up to|at most|under|below|less than|fewer than)|(~))\s*(?:US\$|S\$|\$|€|£)?\s*$",
    re.IGNORECASE,
)
_QUALIFIER_WORDS: dict[str, Qualifier] = {
    "about": "approximately", "around": "approximately", "approximately": "approximately",
    "approx.": "approximately", "roughly": "approximately", "nearly": "approximately",
    "almost": "approximately", "~": "approximately",
    "over": "more_than", "above": "more_than", "more than": "more_than",
    "at least": "at_least", "up to": "at_most", "at most": "at_most",
    "under": "less_than", "below": "less_than", "less than": "less_than", "fewer than": "less_than",
}  # fmt: skip
_SCALE_AFTER = re.compile(r"\s?(k|thousand|million|billion|bn|m|b)\b", re.IGNORECASE)
_SCALES = {"k": 1e3, "thousand": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}
_SYMBOL_UNIT_AFTER = re.compile(
    r"(%|\u00d7|x\b|\s?(?:percent|per cent|percentage points?|pp|times|-?fold)\b)", re.IGNORECASE
)
_WORD_UNIT_AFTER = re.compile(r"[\s-]+(?:of\s+)?([A-Za-z]+)")
_OR_MORE_AFTER = re.compile(r"\s+or (more|less|fewer)\b", re.IGNORECASE)
_CONNECTOR = re.compile(r"\s*(\u2013|\u2014|-|to|and)\s*(?:US\$|S\$|\$|€|£)?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Quantity:
    """A number (or range, or before/after pair) found in text."""

    text: str
    kind: QuantityKind
    values: tuple[float, ...]
    unit: str | None
    qualifier: Qualifier
    decimals: int = 0
    """Decimal places as written, used to round derived figures."""


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    low: float
    high: float
    decimals: int
    magnitude: bool


def normalize_text(text: str) -> str:
    """Undo LaTeX escapes that affect numbers: `\\%` -> `%`, `--` -> en dash."""
    return text.replace("\\%", "%").replace("\\$", "$").replace("--", "\u2013")


def find_quantities(text: str, allowed_terms: Sequence[str] = ()) -> list[Quantity]:
    """Find every quantity in `text`.

    Skipped: numbers inside `allowed_terms` (e.g. `Java 17`), numbers glued to a name (`GPT-4`,
    `HTTP/2`, `OAuth2`), version strings (`3.12.1`) and standalone years (1900-2100).
    """
    text = normalize_text(text)
    zones = _term_zones(text, allowed_terms)
    tokens = [token for token in _tokens(text) if not _skipped(text, token, zones)]
    quantities: list[Quantity] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        pair_kind = _pair_kind(text, token, following) if following else None
        if following is not None and pair_kind is not None:
            quantities.append(_build(text, token, following, pair_kind))
            index += 2
        else:
            quantities.append(_build(text, token, None, "range" if token.magnitude else "single"))
            index += 1
    return quantities


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN.finditer(text):
        if match["num"]:
            raw = match["num"]
            value = float(raw.replace(",", ""))
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            tokens.append(_Token(match.start(), match.end(), value, value, decimals, False))
        elif match["word"]:
            value = float(_WORD_NUMBERS[match["word"].lower()])
            tokens.append(_Token(match.start(), match.end(), value, value, 0, False))
        else:
            low, high = _MAGNITUDES[match["mag"].lower()]
            tokens.append(_Token(match.start(), match.end(), low, high, 0, True))
    return tokens


def _term_zones(text: str, allowed_terms: Sequence[str]) -> list[Interval]:
    zones: list[Interval] = []
    lowered = text.lower()
    for term in allowed_terms:
        needle = term.strip().lower()
        if not needle:
            continue
        start = lowered.find(needle)
        while start != -1:
            zones.append((start, start + len(needle)))
            start = lowered.find(needle, start + 1)
    return zones


def _skipped(text: str, token: _Token, zones: list[Interval]) -> bool:
    if any(start <= token.start and token.end <= end for start, end in zones):
        return True
    if re.search(r"[A-Za-z][-/]$", text[: token.start]):
        return True
    if re.match(r"\.\d", text[token.end :]):
        return True
    raw = text[token.start : token.end]
    if raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100:
        after = text[token.end :]
        return not (_CURRENCY_BEFORE.search(text[: token.start]) or _SYMBOL_UNIT_AFTER.match(after))
    return False


def _suffix_end(text: str, end: int) -> int:
    """Skip a scale, `+` and symbol unit right after a number, e.g. `50k`, `200+`, `8%`."""
    if scale := _SCALE_AFTER.match(text, end):
        end = scale.end()
    if text.startswith("+", end):
        end += 1
    if unit := _SYMBOL_UNIT_AFTER.match(text, end):
        end = unit.end()
    return end


def _pair_kind(text: str, token: _Token, following: _Token) -> QuantityKind | None:
    if token.magnitude or following.magnitude:
        return None
    connector = _CONNECTOR.fullmatch(text, _suffix_end(text, token.end), following.start)
    if connector is None:
        return None
    word = connector[1].lower()
    before = text[max(0, token.start - 16) : token.start].lower()
    if word == "and":
        return "range" if re.search(r"\bbetween\s+\S*$", before) else None
    if word == "to":
        return "change" if re.search(r"\bfrom\s+\S*$", before) else "pair"
    return "range"


def _build(text: str, first: _Token, second: _Token | None, kind: QuantityKind) -> Quantity:
    last = second or first
    before = text[: first.start]
    currency = _CURRENCY_BEFORE.search(before)
    currency_unit = _UNIT_ALIASES[currency[1].lower()] if currency else None

    qualifier: Qualifier = "exact"
    start = first.start
    if qualifier_match := _QUALIFIER_BEFORE.search(before):
        word = (qualifier_match[1] or qualifier_match[2]).lower()
        qualifier = _QUALIFIER_WORDS[word]
        start = qualifier_match.start()
    elif currency:
        start = currency.start()

    values, end, unit = _values_and_symbol_unit(text, first, second, currency_unit is not None)
    unit = unit or currency_unit
    if text.startswith("+", last.end) or text.startswith("+", _scale_end(text, last.end)):
        qualifier = "at_least"
    if or_more := _OR_MORE_AFTER.match(text, end):
        qualifier = "at_least" if or_more[1].lower() == "more" else "at_most"
        end = or_more.end()
    if unit is None:
        unit, end = _word_unit(text, end)

    if first.magnitude:
        values = (first.low, first.high)
    return Quantity(
        text=text[start:end].strip(),
        kind=kind,
        values=values,
        unit=unit,
        qualifier=qualifier,
        decimals=max(first.decimals, last.decimals),
    )


def _scale_end(text: str, end: int) -> int:
    scale = _SCALE_AFTER.match(text, end)
    return scale.end() if scale else end


def _values_and_symbol_unit(
    text: str, first: _Token, second: _Token | None, has_currency: bool
) -> tuple[tuple[float, ...], int, str | None]:
    """The values, where the numeric part ends, and a symbol unit such as `%` or `x`."""
    tokens = [first] if second is None else [first, second]
    values: list[float] = []
    end = first.end
    unit: str | None = None
    for token in tokens:
        value, end = token.low, token.end
        if scale := _SCALE_AFTER.match(text, end):
            word = scale[1].lower()
            if word in {"k", "thousand", "million", "billion"} or has_currency:
                value *= _SCALES[word]
                end = scale.end()
        if text.startswith("+", end):
            end += 1
        if symbol := _SYMBOL_UNIT_AFTER.match(text, end):
            unit = normalize_unit(symbol[1].strip())
            end = symbol.end()
        values.append(value)
    return tuple(values), end, unit


def _word_unit(text: str, end: int) -> tuple[str | None, int]:
    """A unit word after a number, e.g. `papers` or `of users`; connectives don't count."""
    word_unit = _WORD_UNIT_AFTER.match(text, end)
    if word_unit and word_unit[1].lower() not in _NOT_UNITS:
        return normalize_unit(word_unit[1]), word_unit.end()
    return None, end


# --- Extraction for import -------------------------------------------------------------------


def extract_metrics(text: str, allowed_terms: Sequence[str] = ()) -> tuple[Metric, ...]:
    """Turn the quantities in an existing bullet into metrics, e.g. for imported facts.

    A bare "A to B" becomes a before/after change; a dash or "between" range becomes a range.
    """
    metrics: list[Metric] = []
    for quantity in find_quantities(text, allowed_terms):
        value: SingleValue | ChangeValue | RangeValue
        if quantity.kind == "single":
            value = SingleValue(value=quantity.values[0])
        elif quantity.kind == "range":
            value = RangeValue(low=quantity.values[0], high=quantity.values[-1])
        else:
            value = ChangeValue(before=quantity.values[0], after=quantity.values[1])
        metrics.append(
            Metric(
                subject=quantity.text, value=value, unit=quantity.unit, qualifier=quantity.qualifier
            )
        )
    return tuple(metrics)
