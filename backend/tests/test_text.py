import pytest

from app.importers.latex import to_plain
from app.text import RULES, plain, plain_strings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("saved 2\N{EN DASH}3 hours", "saved 2-3 hours"),
        ("Aug 2020 \N{EN DASH} May 2024", "Aug 2020 - May 2024"),
        ("shipped\N{EM DASH}fast", "shipped - fast"),
        ("shipped \N{EM DASH} fast", "shipped - fast"),
        (
            "\N{MINUS SIGN}5% and a non\N{NON-BREAKING HYPHEN}breaking hyphen",
            "-5% and a non-breaking hyphen",
        ),
        ("\N{LEFT SINGLE QUOTATION MARK}it\N{RIGHT SINGLE QUOTATION MARK}s", "'it's"),
        ("\N{LEFT DOUBLE QUOTATION MARK}fast\N{RIGHT DOUBLE QUOTATION MARK}", '"fast"'),
        ("and more\N{HORIZONTAL ELLIPSIS}", "and more..."),
        ("10\N{NO-BREAK SPACE}ms and 5\N{NARROW NO-BREAK SPACE}GB", "10 ms and 5 GB"),
        ("zero\N{ZERO WIDTH SPACE}width\N{SOFT HYPHEN}", "zerowidth"),
        ("5\N{MULTIPLICATION SIGN} faster", "5x faster"),
        ("\N{ALMOST EQUAL TO}30% of runs", "~30% of runs"),
        ("\N{GREATER-THAN OR EQUAL TO}99.9% and \N{LESS-THAN OR EQUAL TO}2 s", ">=99.9% and <=2 s"),
        ("50 \N{RIGHTWARDS ARROW} 12 minutes", "50 -> 12 minutes"),
        ("\N{PLUS-MINUS SIGN}2%", "+/-2%"),
        ("Python \N{MIDDLE DOT} SQL \N{BULLET} Go", "Python | SQL * Go"),
        (
            "\N{COPYRIGHT SIGN} Acme\N{REGISTERED SIGN} Cloud\N{TRADE MARK SIGN}",
            "(c) Acme(R) Cloud(TM)",
        ),
        ("already plain - text", "already plain - text"),
        ("", ""),
    ],
)
def test_typographic_characters_become_their_ascii_versions(text: str, expected: str) -> None:
    assert plain(text) == expected


def test_letters_and_currency_without_an_ascii_version_stay() -> None:
    text = "Jos\N{LATIN SMALL LETTER E WITH ACUTE} saved \N{EURO SIGN}5M and \N{POUND SIGN}2M"
    assert plain(text) == text


def test_new_lines_stay_around_an_em_dash() -> None:
    assert plain("first\N{EM DASH}\nsecond") == "first - \nsecond"


def test_applying_the_rules_twice_changes_nothing() -> None:
    once = plain("2\N{EN DASH}3 \N{EM DASH} 4\N{HORIZONTAL ELLIPSIS}")
    assert plain(once) == once


def test_every_replacement_is_ascii() -> None:
    for _, replacement in RULES:
        assert replacement.isascii()


def test_every_symbol_the_importer_writes_has_an_ascii_version() -> None:
    commands = r"\textendash \textemdash \ldots \dots \textbullet \times \sim \approx \pm \cdot"
    commands += r" \to \rightarrow \geq \leq \ge \le -- --- ``quoted''"
    assert plain(to_plain(commands)).isascii()


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
