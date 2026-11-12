import pytest

from app.validate.confidential import (
    TermMatch,
    describe,
    find_terms,
    quoted,
    screen,
    term_pattern,
)


@pytest.mark.parametrize(
    "text",
    [
        "Moved Atlas to a message queue.",
        "moved atlas to a message queue",
        "MOVED ATLAS TO A MESSAGE QUEUE",
        "Atlas.",
        "(Atlas)",
        '"Atlas"',
        "Atlas's export job",
        "the atlas-db cluster",
        "the atlas_db table",
        "Atlas/Orion",
        "Atlas\N{NO-BREAK SPACE}team",
    ],
)
def test_a_term_matches_as_a_whole_word_in_any_case(text: str) -> None:
    assert find_terms(text, ["Atlas"]) == ("Atlas",)


@pytest.mark.parametrize(
    "text",
    [
        "Moved Jira to Atlassian Cloud.",
        "Built the Atlases service.",
        "Ran Atlas2 on the old cluster.",
        "Ran 2Atlas on the old cluster.",
        "MetAtlas",
        "",
    ],
)
def test_a_term_inside_another_word_doesnt_match(text: str) -> None:
    assert find_terms(text, ["Atlas"]) == ()


@pytest.mark.parametrize(
    "text",
    [
        "Shipped Project Falcon on time.",
        "shipped project falcon on time",
        "Shipped Project\nFalcon on time.",
        "Shipped Project  Falcon on time.",
        "Shipped Project-Falcon on time.",
    ],
)
def test_a_phrase_matches_across_spaces_line_breaks_and_hyphens(text: str) -> None:
    assert find_terms(text, ["Project Falcon"]) == ("Project Falcon",)


def test_a_phrase_needs_all_its_words_in_order() -> None:
    terms = ["Project Falcon"]
    assert find_terms("Falcon was a project of ours.", terms) == ()
    assert find_terms("Project Falconer", terms) == ()
    assert find_terms("Project, Falcon", terms) == ()


def test_terms_with_punctuation_match_as_written() -> None:
    assert find_terms("Wrote C++ services.", ["C++"]) == ("C++",)
    assert find_terms("Wrote C++20 services.", ["C++"]) == ()
    assert find_terms("Sold to Acme Inc. in 2025.", ["Acme Inc."]) == ("Acme Inc.",)
    assert find_terms("Worked with O'Brien Logistics.", ["O'Brien"]) == ("O'Brien",)


def test_typographic_characters_match_their_plain_versions() -> None:
    assert find_terms("Built the \N{LATIN SMALL LIGATURE FI}nch service.", ["Finch"]) == ("Finch",)
    assert find_terms("Worked with O\N{RIGHT SINGLE QUOTATION MARK}Brien.", ["O'Brien"]) == (
        "O'Brien",
    )
    assert find_terms("Shipped Project\N{EN DASH}Falcon.", ["Project Falcon"]) == (
        "Project Falcon",
    )


def test_each_term_found_is_named_once_in_the_owners_order_and_spelling() -> None:
    text = "Atlas fed Project Falcon, and atlas fed falcon again."
    terms = ["falcon", "Orion", "ATLAS", "Project Falcon"]
    assert find_terms(text, terms) == ("falcon", "ATLAS", "Project Falcon")


def test_no_terms_or_terms_without_words_find_nothing() -> None:
    assert find_terms("Atlas", []) == ()
    assert find_terms("a - b", ["-"]) == ()
    assert term_pattern("-") is None
    assert term_pattern("  ") is None


def test_a_term_is_matched_literally() -> None:
    assert find_terms("Shipped a.b to production.", ["a*b"]) == ()
    assert find_terms("Shipped a*b to production.", ["a*b"]) == ("a*b",)
    assert find_terms("Fixed (x) twice.", ["(x)"]) == ("(x)",)


def test_screen_names_each_term_once_for_each_place() -> None:
    texts = [
        ("About", "Atlas and Falcon, then Atlas again."),
        ("Experience: Engineer", "Ran atlas."),
        ("About", "Atlas once more."),
        ("Skills", "Python"),
    ]
    assert screen(texts, ["Atlas", "Falcon"]) == (
        TermMatch(term="Atlas", where="About"),
        TermMatch(term="Falcon", where="About"),
        TermMatch(term="Atlas", where="Experience: Engineer"),
    )
    assert screen(texts, []) == ()


def test_matches_are_described_term_by_term() -> None:
    matches = (
        TermMatch(term="Atlas", where="About"),
        TermMatch(term="Falcon", where="Projects: Recipe Box"),
        TermMatch(term="Atlas", where="Experience: Engineer"),
    )
    assert describe(matches) == (
        '"Atlas" (About; Experience: Engineer), "Falcon" (Projects: Recipe Box)'
    )
    assert quoted(["Atlas"]) == '"Atlas"'
    assert quoted(["Atlas", "Falcon", "Atlas", "Orion"]) == '"Atlas", "Falcon" and "Orion"'
    assert quoted([]) == ""
