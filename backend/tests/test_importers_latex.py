import pytest

from app.importers.latex import (
    Blank,
    Command,
    Comment,
    LatexError,
    Text,
    read_group,
    scan,
    to_plain,
    unescape_comment,
)

ARITY = {"resumeItem": 1, "resumeSubheading": 4, "textbf": 1, "begin": 1, "href": 2}


def test_read_group_handles_nesting_escapes_and_comments() -> None:
    source = "{a {b} \\} 50\\% % dropped\nc}rest"
    content, end = read_group(source, 0)
    assert content == "a {b} \\} 50\\% c"
    assert source[end:] == "rest"


def test_read_group_rejects_an_unclosed_group() -> None:
    with pytest.raises(LatexError):
        read_group("{never closed", 0)


def test_scan_finds_comments_blank_lines_commands_and_text() -> None:
    source = (
        "% above\n\n\\resumeItem{Built a\n  thing} % trailing\n\\vspace{-2pt} +1 555 0100 \\\\\n"
    )
    events = scan(source, ARITY, first_line=10)
    assert events == [
        Comment(10, " above"),
        Blank(11),
        Command(12, "resumeItem", ("Built a\n  thing",)),
        Comment(13, " trailing", trailing=True),
        Text(14, "+1 555 0100 \\\\"),
    ]


def test_scan_reads_arguments_across_lines_and_skips_optional_ones() -> None:
    source = (
        "\\resumeSubheading\n  {A}{Jan 2020 -- Present}\n  {B}{}\n"
        "\\begin{itemize}[leftmargin=0.15in, label={}]\n"
    )
    events = scan(source, ARITY)
    assert events == [
        Command(1, "resumeSubheading", ("A", "Jan 2020 -- Present", "B", "")),
        Command(4, "begin", ("itemize",)),
    ]


def test_scan_takes_an_immediate_second_group_when_asked() -> None:
    source = "\\textbf{Languages}{: Python, SQL} \\\\\n\\textbf{Name} \\\\"
    events = scan(source, ARITY, optional_group=frozenset({"textbf"}))
    assert events == [
        Command(1, "textbf", ("Languages", ": Python, SQL")),
        Command(2, "textbf", ("Name",)),
    ]


def test_scan_keeps_escaped_percent_signs_in_text() -> None:
    assert scan("cut by 33\\% overall", ARITY) == [Text(1, "cut by 33\\% overall")]


def test_scan_reports_the_line_of_an_unclosed_group() -> None:
    with pytest.raises(LatexError, match="line 2"):
        scan("\n\\resumeItem{never closed", ARITY)


@pytest.mark.parametrize(
    ("tex", "plain"),
    [
        ("raising recall by 15\\%", "raising recall by 15%"),
        ("Networks \\& Security", "Networks & Security"),
        ("2--3 hours and 2020---2021", "2\u20133 hours and 2020\u20142021"),
        ("\\textbf{\\Huge \\scshape Casey Morgan}", "Casey Morgan"),
        ("\\textbf{Recipe Box} $|$ \\emph{React}", "Recipe Box | React"),
        ("\\href{mailto:casey@example.com}{\\underline{casey@example.com}}", "casey@example.com"),
        ("a~b \\\\ c\n   d", "a b c d"),
        ("``quoted''", "\u201cquoted\u201d"),
        ("\\vspace{-2pt}text", "text"),
    ],
)
def test_to_plain(tex: str, plain: str) -> None:
    assert to_plain(tex) == plain


def test_to_plain_collects_commands_it_does_not_know() -> None:
    unknown: set[str] = set()
    assert to_plain("Caf\\'e with \\foo{bar}", unknown) == "Cafe with bar"
    assert unknown == {"'", "foo"}


def test_unescape_comment_handles_escapes_and_dashes_but_keeps_tildes() -> None:
    assert unescape_comment("  Backend \\& APIs -- about ~3 pages  ") == (
        "Backend & APIs \u2013 about ~3 pages"
    )
