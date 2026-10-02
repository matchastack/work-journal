"""Style checker: the wording rules for resume bullets and LinkedIn text (FR-WRT-1, 2 and 4).

Deterministic, no LLM. A resume bullet must:
- start with an action verb in the past tense, as a bullet describes work done, so this holds
  for the current role too;
- use no first-person pronouns;
- fit in 2 lines at the template's width, estimated from the font's character widths.

LinkedIn text must fit its section's character limit, and may use the first person. Every style
avoids the owner's words to avoid.

A clear breach is an error. Where the check can't be sure, such as a first word the verb list
doesn't know, the finding is a warning. Whether a bullet reads as "what + how + measurable
result" takes judgement, so the writers' prompts and the claim verifier handle that.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.schema.common import Model
from app.validate.verbs import verb_form

StyleCode = Literal["not_action_verb", "wrong_tense", "first_person", "too_long", "avoided_word"]
LinkedInSection = Literal["headline", "about", "position"]

LINKEDIN_LIMITS: dict[LinkedInSection, int] = {"headline": 220, "about": 2600, "position": 2000}
"""LinkedIn's character limits (FR-LIN-1) for the headline, About and a position's description."""


class StyleFinding(Model):
    severity: Literal["error", "warning"]
    code: StyleCode
    message: str


class StyleCheck(Model):
    findings: tuple[StyleFinding, ...] = ()

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not any(finding.severity == "error" for finding in self.findings)


@dataclass(frozen=True)
class BulletLayout:
    """How the template sets a bullet: the line width and font size in points, and max lines."""

    line_width_pt: float
    font_size_pt: float
    max_lines: int = 2


TEMPLATE_LAYOUT = BulletLayout(line_width_pt=513.1, font_size_pt=10)
"""The resume template: a 7.5 in text width, less list indents of 0.15 in and 0.25 in, with
bullets in small type (10 pt) in an 11 pt document."""

_LOWERCASE = (500, 556, 444, 556, 444, 306, 500, 556, 278, 306, 528, 278, 833, 556, 500, 556, 528,
              392, 394, 389, 556, 528, 722, 528, 528, 444)  # fmt: skip
_UPPERCASE = (750, 708, 722, 764, 681, 653, 785, 750, 361, 514, 778, 625, 917, 750, 778, 681, 778,
              736, 556, 722, 750, 750, 1028, 750, 750, 611)  # fmt: skip
_WIDTHS = {
    **dict(zip("abcdefghijklmnopqrstuvwxyz", _LOWERCASE, strict=True)),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", _UPPERCASE, strict=True)),
    **dict.fromkeys('0123456789$/*~"\u2013\u201c\u201d', 500),
    **dict.fromkeys(".,:;!'[]\u2019", 278),
    **dict.fromkeys("%#@", 833),
    **dict.fromkeys("+=&<>\u00d7", 778),
    " ": 333,
    "-": 333,
    "(": 389,
    ")": 389,
    "?": 472,
    "\u2014": 1000,
}
"""Character widths in Computer Modern Roman, the template's font, in thousandths of an em."""
_DEFAULT_WIDTH = 500
"""Other characters count as half an em."""

_FIRST_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")
_NOT_VERBS = frozenset(
    """
    a an the this that these those each every all some any many most several various multiple
    both i we you he she they it me us my our your his her its their as at by for from in into
    of on over to with within without across after before during under via through while when
    and but or also responsible
    """.split()  # noqa: SIM905 - a word list reads better as text
)
"""First words that are clearly not verbs: articles, pronouns, prepositions and the like."""

_WORD = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)?")
_FIRST_PERSON = frozenset(
    ["i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves", "i'm", "i've",
     "i'd", "i'll", "we're", "we've", "we'd", "we'll"]
)  # fmt: skip
_JOINERS = frozenset("/&-")
"""Characters that make a pronoun-like word part of a term: "I/O", "R&I", "us-east-1"."""


def check_resume_bullet(
    text: str,
    *,
    avoid: Sequence[str] = (),
    layout: BulletLayout = TEMPLATE_LAYOUT,
) -> StyleCheck:
    """Check a work or project bullet, given as plain text the way the profile stores it.

    Education lines such as honours don't open with a verb, so they aren't checked here. `avoid`
    is the owner's list of words to avoid.
    """
    findings = [
        *_opening(text),
        *_first_person(text),
        *_length(text, layout),
        *_avoided(text, avoid),
    ]
    return StyleCheck(findings=tuple(findings))


def check_linkedin(text: str, section: LinkedInSection, *, avoid: Sequence[str] = ()) -> StyleCheck:
    """Check LinkedIn text for one section. The first person is fine on LinkedIn."""
    findings: list[StyleFinding] = []
    length = linkedin_length(text)
    limit = LINKEDIN_LIMITS[section]
    if length > limit:
        where = {"headline": "the headline", "about": "About", "position": "a position"}[section]
        message = f"Has {length:,} characters; LinkedIn allows {limit:,} in {where}."
        findings.append(_error("too_long", message))
    findings += _avoided(text, avoid)
    return StyleCheck(findings=tuple(findings))


def linkedin_length(text: str) -> int:
    """The length as web forms count it, in UTF-16 units: an emoji counts as 2 characters."""
    return len(text.encode("utf-16-le")) // 2


def estimate_lines(text: str, layout: BulletLayout = TEMPLATE_LAYOUT) -> int:
    """How many lines `text` takes at the layout's width.

    Lines break between words. LaTeX can also hyphenate, so the estimate errs toward more lines.
    """
    space = _width(" ", layout)
    lines, used = 1, 0.0
    for word in text.split():
        width = _width(word, layout)
        if used and used + space + width > layout.line_width_pt:
            lines, used = lines + 1, width
        else:
            used += (space if used else 0.0) + width
    return lines


def _width(text: str, layout: BulletLayout) -> float:
    return sum(_WIDTHS.get(char, _DEFAULT_WIDTH) for char in text) * layout.font_size_pt / 1000


def _opening(text: str) -> list[StyleFinding]:
    """The first word must be an action verb in the past tense."""
    start = text.lstrip()
    match = _FIRST_WORD.match(start)
    if match is None:
        first = start.split(maxsplit=1)[0] if start else ""
        message = f'Starts with "{first}" rather than an action verb.' if first else "Is empty."
        return [_error("not_action_verb", message)]
    word = match[0]
    form = verb_form(word)
    if form is None:
        return _unknown_opening(word)
    if form == "gerund":
        return [_warning("wrong_tense", f'Starts with "{word}"; {_PAST_TENSE}.')]
    if form in ("either", "past"):
        return []
    return [_error("wrong_tense", f'"{word}" is in the {form} tense; {_PAST_TENSE}.')]


def _unknown_opening(word: str) -> list[StyleFinding]:
    """A first word the verb list doesn't know, judged by its shape."""
    lowered = word.casefold()
    if lowered in _NOT_VERBS or lowered.endswith("ly"):
        return [_error("not_action_verb", f'Starts with "{word}" rather than an action verb.')]
    if lowered.endswith("ed"):  # most likely a past-tense verb
        return []
    if lowered.endswith("ing"):
        return [_warning("wrong_tense", f'Starts with "{word}"; {_PAST_TENSE}.')]
    return [_warning("not_action_verb", f'"{word}" may not be an action verb.')]


_PAST_TENSE = "resume bullets use the past tense, as they describe work done"


def _first_person(text: str) -> list[StyleFinding]:
    pronouns: list[str] = []
    for match in _WORD.finditer(text):
        token = match[0].replace("\u2019", "'")
        if token.casefold() not in _FIRST_PERSON:
            continue
        if token[0] == "i" or (len(token) > 1 and token.isupper()):
            continue  # a lowercase "i" is rarely the pronoun, and "US" is an acronym
        before = text[match.start() - 1 : match.start()]
        after = text[match.end() : match.end() + 1]
        if before in _JOINERS or after in _JOINERS:
            continue
        pronouns.append(match[0])
    if not pronouns:
        return []
    listed = ", ".join(f'"{pronoun}"' for pronoun in dict.fromkeys(pronouns))
    return [_error("first_person", f"Uses first-person pronouns ({listed}).")]


def _length(text: str, layout: BulletLayout) -> list[StyleFinding]:
    lines = estimate_lines(text, layout)
    if lines <= layout.max_lines:
        return []
    message = f"Takes about {lines} lines at the template's width; the limit is {layout.max_lines}."
    return [_error("too_long", message)]


def _avoided(text: str, avoid: Sequence[str]) -> list[StyleFinding]:
    """Words and phrases from the owner's list. Words also match their -s, -ed and -ing forms."""
    findings: list[StyleFinding] = []
    normalized = text.replace("\u2019", "'")
    for entry in avoid:
        words = entry.replace("\u2019", "'").casefold().split()
        if not words:
            continue
        if len(words) == 1:
            forms = sorted(_inflections(words[0]), key=len, reverse=True)
            body = "|".join(re.escape(form) for form in forms)
        else:
            body = r"\s+".join(re.escape(word) for word in words)
        match = re.search(rf"(?<![\w'-])(?:{body})(?![\w'-])", normalized, re.IGNORECASE)
        if match:
            message = f'Uses "{match[0]}", which is on your list of words to avoid.'
            findings.append(_error("avoided_word", message))
    return findings


def _inflections(word: str) -> set[str]:
    forms = {word, word + "s", word + "es", word + "d", word + "ed", word + "ing"}
    if word.endswith("e"):
        forms.add(word[:-1] + "ing")
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        forms |= {word[:-1] + "ies", word[:-1] + "ied"}
    if len(word) > 2 and word[-1] not in "aeiouwxy" and word[-2] in "aeiou":
        forms |= {word + word[-1] + "ed", word + word[-1] + "ing"}
    return forms


def _error(code: StyleCode, message: str) -> StyleFinding:
    return StyleFinding(severity="error", code=code, message=message)


def _warning(code: StyleCode, message: str) -> StyleFinding:
    return StyleFinding(severity="warning", code=code, message=message)
