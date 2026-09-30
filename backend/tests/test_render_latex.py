from pathlib import Path

import pytest
from jinja2.exceptions import SecurityError, UndefinedError

from app.render.latex import (
    Latex,
    TemplateValueError,
    dates,
    escape_latex,
    escape_url,
    latex_environment,
    month,
)


@pytest.mark.parametrize(
    ("text", "latex"),
    [
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\ensuremath{\$}"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\ensuremath{\sim}"),
        ("^", r"\textasciicircum{}"),
        ("\\", r"\textbackslash{}"),
        ("<", r"\ensuremath{<}"),
        (">", r"\ensuremath{>}"),
        ("|", r"\ensuremath{|}"),
        ("\N{MULTIPLICATION SIGN}", r"\ensuremath{\times}"),
        ("\N{RIGHTWARDS ARROW}", r"\ensuremath{\rightarrow}"),
        ("\N{ALMOST EQUAL TO}", r"\ensuremath{\approx}"),
        ("\N{DEGREE SIGN}", r"\ensuremath{^\circ}"),
    ],
)
def test_special_characters_are_escaped(text: str, latex: str) -> None:
    assert escape_latex(text) == latex


def test_escaping_is_a_single_pass() -> None:
    # The backslash becomes \textbackslash{}, whose braces must not be escaped again.
    assert escape_latex("\\{") == r"\textbackslash{}\{"


def test_ordinary_text_is_kept() -> None:
    assert escape_latex("Cut costs by 40% in 3 weeks.") == r"Cut costs by 40\% in 3 weeks."


def test_whitespace_collapses_so_a_value_cant_start_a_paragraph() -> None:
    assert escape_latex("one\n\ntwo\t three") == "one two three"


def test_escaped_text_is_marked_as_latex() -> None:
    assert isinstance(escape_latex("text"), Latex)


@pytest.mark.parametrize(
    ("url", "latex"),
    [
        ("https://example.com/a_b~c?x=1&y=2#top", r"https://example.com/a_b~c?x=1&y=2\#top"),
        ("https://example.com/50%25-off", r"https://example.com/50\%25-off"),
        ("https://example.com/a b{c}", r"https://example.com/a\%20b\%7Bc\%7D"),
        ("https://example.com/back\\slash", r"https://example.com/back\%5Cslash"),
    ],
)
def test_urls_are_safe_inside_href(url: str, latex: str) -> None:
    assert escape_url(url) == latex


def test_month_and_date_ranges() -> None:
    assert month("2024-03") == "Mar 2024"
    assert dates("2023-06", "2023-09") == "Jun 2023 - Sep 2023"
    assert dates("2024-03", None) == "Mar 2024 - Present"
    assert dates(None, "2023-09") == "Sep 2023"
    assert dates(None, None) == ""


def render(folder: Path, source: str, **context: object) -> str:
    (folder / "resume.tex").write_text(source, encoding="utf-8")
    return latex_environment(folder).get_template("resume.tex").render(**context)


def test_values_are_escaped_and_template_text_is_not(tmp_path: Path) -> None:
    rendered = render(tmp_path, r"\textbf{\VAR{name}}", name="R&D, 100% {done}")
    assert rendered == r"\textbf{R\&D, 100\% \{done\}}"


def test_latex_values_are_printed_as_they_are(tmp_path: Path) -> None:
    rendered = render(tmp_path, r"\href{\VAR{link|url}}{site}", link="https://example.com/#top")
    assert rendered == r"\href{https://example.com/\#top}{site}"


def test_blocks_and_comments(tmp_path: Path) -> None:
    source = r"\BLOCK{for item in items}[\VAR{item}]\BLOCK{endfor}\#{ not printed }"
    assert render(tmp_path, source, items=["a", "b"]) == "[a][b]"


def test_dates_and_months_are_available(tmp_path: Path) -> None:
    rendered = render(
        tmp_path, r"\VAR{dates(start, end)}; \VAR{start|month}", start="2024-03", end=None
    )
    assert rendered == "Mar 2024 - Present; Mar 2024"


def test_printing_a_missing_value_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(TemplateValueError):
        render(tmp_path, r"\VAR{location}", location=None)
    assert render(tmp_path, r"\VAR{location or ''}", location=None) == ""


def test_undefined_names_are_errors(tmp_path: Path) -> None:
    with pytest.raises(UndefinedError):
        render(tmp_path, r"\VAR{nothing}")


def test_templates_run_in_a_sandbox(tmp_path: Path) -> None:
    with pytest.raises(SecurityError):
        render(tmp_path, r"\VAR{name.__class__.__mro__}", name="Casey")
