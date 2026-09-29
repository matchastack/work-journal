import pytest

from app.text import RULES, plain, plain_strings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("saved 2\N{EN DASH}3 hours", "saved 2-3 hours"),
        ("Aug 2020 \N{EN DASH} May 2024", "Aug 2020 - May 2024"),
        ("already plain - text", "already plain - text"),
        ("", ""),
    ],
)
def test_en_dashes_become_hyphens(text: str, expected: str) -> None:
    assert plain(text) == expected


def test_characters_without_a_rule_stay() -> None:
    quoted = "\N{LEFT DOUBLE QUOTATION MARK}quotes\N{RIGHT DOUBLE QUOTATION MARK}"
    text = f"an em dash \N{EM DASH} and {quoted}"
    assert plain(text) == text


def test_applying_the_rules_twice_changes_nothing() -> None:
    once = plain("2\N{EN DASH}3 \N{EN DASH} 4")
    assert plain(once) == once


def test_every_replacement_is_plain_ascii() -> None:
    for _, replacement in RULES:
        assert replacement.isascii()


def test_strings_inside_containers_are_replaced_and_keys_kept() -> None:
    data = {
        "a\N{EN DASH}key": ["1\N{EN DASH}2", ("3\N{EN DASH}4", 5)],
        "nested": {"text": "x \N{EN DASH} y", "count": 2, "missing": None},
        "tags": {"5\N{EN DASH}6"},
    }
    assert plain_strings(data) == {
        "a\N{EN DASH}key": ["1-2", ("3-4", 5)],
        "nested": {"text": "x - y", "count": 2, "missing": None},
        "tags": {"5-6"},
    }
