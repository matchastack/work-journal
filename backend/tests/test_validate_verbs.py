import pytest

from app.validate.verbs import ACTION_VERBS, IRREGULAR_PAST, verb_form


@pytest.mark.parametrize(
    ("word", "form"),
    [
        ("Build", "present"),
        ("Builds", "present"),
        ("Fixes", "present"),
        ("Simplifies", "present"),
        ("Processes", "present"),
        ("Built", "past"),
        ("Deployed", "past"),
        ("Automated", "past"),
        ("Shipped", "past"),
        ("Simplified", "past"),
        ("Embedded", "past"),
        ("Led", "past"),
        ("Oversaw", "past"),
        ("Building", "gerund"),
        ("Automating", "gerund"),
        ("Shipping", "gerund"),
        ("Cut", "either"),
        ("Set", "either"),
        ("Found", "either"),  # the past of "find", or "found" a company
        ("Founded", "past"),
    ],
)
def test_tense_of_known_verbs(word: str, form: str) -> None:
    assert verb_form(word) == form


@pytest.mark.parametrize(
    ("word", "form"),
    [
        ("Optimised", "past"),
        ("Analysed", "past"),
        ("Modelled", "past"),
        ("Catalogued", "past"),
        ("Optimising", "gerund"),
    ],
)
def test_british_spellings(word: str, form: str) -> None:
    assert verb_form(word) == form


@pytest.mark.parametrize(
    ("word", "form"),
    [
        ("Co-led", "past"),
        ("Hand-built", "past"),
        ("Rearchitected", "past"),
        ("Re-architected", "past"),
        ("Cofounded", "past"),
        ("Deprioritized", "past"),
        ("Preprocesses", "present"),
    ],
)
def test_prefixes_and_hyphens(word: str, form: str) -> None:
    assert verb_form(word) == form


@pytest.mark.parametrize("word", ["Kubernetes", "Successfully", "The", "Revenue", "Team"])
def test_other_words_are_unknown(word: str) -> None:
    assert verb_form(word) is None


def test_irregular_forms_belong_to_listed_verbs() -> None:
    assert set(IRREGULAR_PAST.values()) <= ACTION_VERBS


def test_the_list_holds_lowercase_base_forms() -> None:
    assert all(verb.isalpha() and verb.islower() for verb in ACTION_VERBS)
