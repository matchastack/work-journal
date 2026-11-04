"""Confidential terms: the owner's words and names that must never appear in public outputs
(FR-SET-5, NFR-PRIV-4).

The portfolio page and its resumes (`app/portfolio/confidential.py`), the LinkedIn pack
(`app/linkedin.py`) and the verifier (`app/validate/lint.py`, rules `confidential` and R5) all
match terms here, the same way:

- **Whole words, in any case.** `Atlas` matches `ATLAS`, `atlas.` and `Atlas's`, but not
  `Atlassian` or `Atlas2`: a term matches only where no letter or digit comes right before or
  after it. Punctuation, such as a hyphen or an underscore, separates words, so `atlas` is found
  in `atlas-db` and `atlas_db`.
- **Phrases.** The words of a term match with any spaces, line breaks or hyphens between them,
  so `Project Atlas` matches `project atlas`, `Project-Atlas` and a line that breaks after
  `Project`.
- **Plain characters.** Text and terms go through `app/text.py` and Unicode compatibility
  normalisation first, so typographic characters, such as a non-breaking space or the `fi`
  ligature a PDF may hold, match their plain versions.

A match names the term as the owner wrote it, and where it was found. Neither terms nor the text
around them are ever logged.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from functools import lru_cache

from app.schema.common import Model, NonEmptyStr
from app.text import plain

_ALNUM = r"[^\W_]"
"""A letter or a digit, in any script."""
_SEPARATORS = re.compile(r"[\s\-]+")


class TermMatch(Model):
    """A confidential term found in an output."""

    term: NonEmptyStr
    """The term, as the owner wrote it in Settings."""
    where: NonEmptyStr
    """Where it was found, such as `Experience: Engineer` or `Headline`."""


def normal(text: str) -> str:
    """`text` as terms are matched against it: plain characters, and compatibility forms
    (ligatures, full-width letters) replaced."""
    return plain(unicodedata.normalize("NFKC", text))


def find_terms(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    """The terms that appear in `text`, each once, in the order of `terms`."""
    if not terms or not text:
        return ()
    either, each = _patterns(tuple(terms))
    if either is None:
        return ()
    checked = normal(text)
    if either.search(checked) is None:
        return ()
    return tuple(term for term, pattern in each if pattern.search(checked))


def screen(texts: Iterable[tuple[str, str]], terms: Sequence[str]) -> tuple[TermMatch, ...]:
    """The terms found in `texts`, pairs of where and what: one match for each term and place,
    in the order the places come."""
    if not terms:
        return ()
    found: dict[tuple[str, str], TermMatch] = {}
    for where, text in texts:
        for term in find_terms(text, terms):
            found.setdefault((term.casefold(), where), TermMatch(term=term, where=where))
    return tuple(found.values())


def describe(matches: Sequence[TermMatch]) -> str:
    """The matches for the owner, such as `"Atlas" (Experience: Engineer; About)`. Terms come
    in the order first found."""
    places: dict[str, list[str]] = {}
    for match in matches:
        places.setdefault(match.term, []).append(match.where)
    return ", ".join(f'"{term}" ({"; ".join(where)})' for term, where in places.items())


def quoted(terms: Iterable[str]) -> str:
    """Terms for a message, such as `"Atlas" and "Project Falcon"`."""
    names = [f'"{term}"' for term in dict.fromkeys(terms)]
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def term_pattern(term: str) -> re.Pattern[str] | None:
    """The regular expression that finds `term` as whole words, in any case; None for a term
    with no words, such as a lone hyphen."""
    words = [word for word in _SEPARATORS.split(normal(term)) if word]
    if not words:
        return None
    body = r"[\s\-]+".join(re.escape(word) for word in words)
    return re.compile(rf"(?<!{_ALNUM})(?:{body})(?!{_ALNUM})", re.IGNORECASE)


@lru_cache(maxsize=32)
def _patterns(
    terms: tuple[str, ...],
) -> tuple[re.Pattern[str] | None, tuple[tuple[str, re.Pattern[str]], ...]]:
    """One pattern that finds any of `terms`, to rule out most text at once, and one for each
    term. Kept for the last few term lists, as they change rarely."""
    each = tuple((term, pattern) for term in terms if (pattern := term_pattern(term)) is not None)
    if not each:
        return None, ()
    either = "|".join(f"(?:{pattern.pattern})" for _, pattern in each)
    return re.compile(either, re.IGNORECASE), each
