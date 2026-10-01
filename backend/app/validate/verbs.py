"""Action verbs for resume bullets, and which tense a bullet's first word is in.

The list holds base forms of verbs that open bullets in software and data work, plus irregular
past forms. Regular inflections (-s, -ed and -ing, with doubled consonants and y to i), British
spellings (-ise, -yse) and the prefixes re-, co-, pre-, de- and un- are derived, so "Optimised",
"Co-led" and "Rearchitected" are all recognised. A word outside the list returns None, which the
style checker reports as a warning, never an error.
"""

import re
from collections.abc import Iterator
from typing import Literal

VerbForm = Literal["present", "past", "either", "gerund"]
"""`either` is a verb spelled the same in both tenses, such as "cut" or "set"."""

ACTION_VERBS = frozenset(
    """
    accelerate accomplish achieve acquire adapt add address adopt advance advise advocate align
    allocate analyze annotate anonymize apply architect arrange assemble assess assign assist
    attain audit author automate backfill balance become begin benchmark boost bootstrap bring
    broaden budget build cache calculate calibrate capture catalog catalogue centralize champion
    change clarify classify clean close coach code codify collaborate collect combine commit
    communicate compile complete compose compress compute conceive conceptualize conduct
    configure connect consolidate construct consult containerize contribute control convert
    coordinate correct craft create curate customize cut debug decommission decouple decrease
    deduplicate define delegate deliver demonstrate deploy deprecate design detect determine
    develop devise diagnose digitize direct discover distill dockerize document double draft
    draw drive earn eliminate embed enable encrypt enforce engineer enhance ensure establish
    estimate evaluate evangelize exceed execute expand expedite experiment explore expose extend
    extract facilitate find finish fix forecast formalize formulate found generate give grow
    guide halve handle harden head help hire hit hold host identify implement improve increase
    index influence ingest initiate innovate inspect install instrument integrate interview
    introduce invent investigate iterate keep label launch lead leverage liaise lift log lower
    maintain make manage map maximize measure meet mentor merge migrate minimize mitigate model
    moderate modernize modify monitor motivate negotiate normalize obtain onboard operate
    optimize orchestrate organize outperform overhaul oversee own package parallelize partner
    patch perform pilot pioneer pitch place plan port predict prepare present preserve prevent
    prioritize process produce productionize profile program promote propose prototype provide
    provision prune publish put quantify quantize query raise rank reach rebuild receive
    recommend reconcile record recruit redesign reduce refactor refine release remediate remove
    reorganize replace replicate report represent research resolve restore restructure retrain
    revamp review revise rewrite roll run safeguard save scale schedule scope script secure
    segment select sell serve set shape shard share ship shorten shrink simplify simulate slash
    solve source speak spearhead specify speed spin split sponsor stabilize stand standardize
    start steer stream streamline strengthen structure study submit supervise support surface
    survey sustain synthesize systematize tackle take teach test trace track train transform
    transition translate triage trim triple troubleshoot tune tutor unblock undertake unify
    update upgrade use utilize validate vectorize verify visualize volunteer win wire write
    """.split()  # noqa: SIM905 - a word list reads better as text than as 354 quoted strings
)

IRREGULAR_PAST = {
    "became": "become",
    "began": "begin",
    "brought": "bring",
    "built": "build",
    "cut": "cut",
    "drew": "draw",
    "drove": "drive",
    "forecast": "forecast",
    "found": "find",
    "gave": "give",
    "grew": "grow",
    "held": "hold",
    "hit": "hit",
    "kept": "keep",
    "led": "lead",
    "made": "make",
    "met": "meet",
    "oversaw": "oversee",
    "put": "put",
    "ran": "run",
    "rebuilt": "rebuild",
    "rewrote": "rewrite",
    "set": "set",
    "shrank": "shrink",
    "shrunk": "shrink",
    "sold": "sell",
    "sped": "speed",
    "split": "split",
    "spoke": "speak",
    "spun": "spin",
    "stood": "stand",
    "taught": "teach",
    "took": "take",
    "undertook": "undertake",
    "won": "win",
    "wrote": "write",
}
"""Irregular past forms and their base forms."""

_PREFIXES = ("re", "co", "pre", "de", "un")


def verb_form(word: str) -> VerbForm | None:
    """The form of `word` as an action verb, or None if the list doesn't know it."""
    for candidate in _candidates(word.casefold()):
        form = _form(candidate)
        if form is not None:
            return form
    return None


def _candidates(word: str) -> Iterator[str]:
    """The word as written, then its parts after a hyphen, then without a prefix."""
    parts = [word]
    if "-" in word:
        first, *_, last = word.split("-")
        parts += [last, first]
    parts += [
        word.removeprefix(prefix)
        for prefix in _PREFIXES
        if word.startswith(prefix) and len(word) - len(prefix) >= 3
    ]
    for part in parts:
        yield part
        yield _americanize(part)


def _americanize(word: str) -> str:
    """American spelling of -ise and -yse verbs: "optimised" -> "optimized"."""
    return re.sub(r"(?<=[iy])s(e|es|ed|ing)$", r"z\1", word)


def _form(word: str) -> VerbForm | None:
    present = word in ACTION_VERBS or _known(_third_person_stems(word))
    past = word in IRREGULAR_PAST or _known(_past_stems(word))
    if present and past:
        return "either"
    if present:
        return "present"
    if past:
        return "past"
    if _known(_gerund_stems(word)):
        return "gerund"
    return None


def _known(stems: list[str]) -> bool:
    return any(stem in ACTION_VERBS for stem in stems)


def _third_person_stems(word: str) -> list[str]:
    """ "builds" -> build, "fixes" -> fix, "simplifies" -> simplify."""
    if not word.endswith("s"):
        return []
    stems = [word[:-1]]
    if word.endswith("es"):
        stems.append(word[:-2])
    if word.endswith("ies"):
        stems.append(word[:-3] + "y")
    return stems


def _past_stems(word: str) -> list[str]:
    """ "deployed" -> deploy, "automated" -> automate, "shipped" -> ship, "tried" -> try."""
    if not word.endswith("ed") or len(word) < 4:
        return []
    stems = [word[:-2], word[:-1]]
    if word[-3] == word[-4]:
        stems.append(word[:-3])
    if word.endswith("ied"):
        stems.append(word[:-3] + "y")
    return stems


def _gerund_stems(word: str) -> list[str]:
    """ "building" -> build, "automating" -> automate, "shipping" -> ship."""
    if not word.endswith("ing") or len(word) < 5:
        return []
    stems = [word[:-3], word[:-3] + "e"]
    if word[-4] == word[-5]:
        stems.append(word[:-4])
    return stems
