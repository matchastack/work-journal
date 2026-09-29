import pytest

from app.importers.latex import Comment
from app.importers.tags import comment_lines, segments
from app.importers.values import (
    append_phrase,
    dates_and_place,
    degree_parts,
    display_label,
    drop_phrase,
    label_parts,
    note_of,
    parse_dates,
    parse_strength,
    parse_verification,
    slug,
    split_list,
    strength_exception,
    title_parts,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Aug 2021 \u2013 Jun 2025", ("2021-08", "2025-06")),
        ("Sep 2025 \u2013 Present", ("2025-09", None)),
        ("Sept. 2023 to March 2024", ("2023-09", "2024-03")),
        ("2021-08 \u2013 2022-01", ("2021-08", "2022-01")),
        ("May 2023", ("2023-05", "2023-05")),
        ("", (None, None)),
        ("2023", None),
        ("Spring 2023", None),
        ("Aug 2021 \u2013 soon", None),
    ],
)
def test_parse_dates(text: str, expected: tuple[str | None, str | None] | None) -> None:
    assert parse_dates(text) == expected


def test_dates_may_be_on_either_line_of_a_heading() -> None:
    assert dates_and_place("Aug 2020 \u2013 May 2024", "") == ("Aug 2020 \u2013 May 2024", None)
    assert dates_and_place("Springfield, IL", "Jan 2023 \u2013 Jun 2023") == (
        "Jan 2023 \u2013 Jun 2023",
        "Springfield, IL",
    )


def test_split_list_keeps_commas_inside_parentheses() -> None:
    assert split_list(" SQL (Postgres, MySQL), Python ,, R") == [
        "SQL (Postgres, MySQL)",
        "Python",
        "R",
    ]


def test_degree_parts() -> None:
    assert degree_parts("Bachelor of Engineering (Hons) in Physics, Cum Laude") == (
        "Bachelor of Engineering (Hons)",
        "Physics",
        "Cum Laude",
    )
    assert degree_parts("Master of Arts in History") == ("Master of Arts", "History", None)
    assert degree_parts("BSc Physics") is None


def test_title_parts() -> None:
    lines = [
        Comment(1, ' TITLE: the official title is "Analyst". Approved variants:'),
        Comment(2, "   Data Analyst | Analytics Engineer"),
        Comment(3, "   BI Developer"),
        Comment(4, " Never add Senior."),
    ]
    [segment] = segments(comment_lines(lines))
    official, variants, note = title_parts(segment)
    assert official == "Analyst"
    assert variants == ["Data Analyst", "Analytics Engineer", "BI Developer"]
    assert note.text == (
        'TITLE: the official title is "Analyst". Approved variants: Data Analyst | '
        "Analytics Engineer | BI Developer. Never add Senior."
    )


def test_strength() -> None:
    assert parse_strength("high") == ("high", "")
    assert parse_strength("low (HIGH for data roles)") == ("low", "HIGH for data roles")
    assert parse_strength("very") is None
    assert strength_exception("HIGH for data roles") == ("high", "data roles")
    assert strength_exception("HIGH when the posting stresses testing") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("yes", ("yes", None)),
        ("partial (scale unknown)", ("partial", "scale unknown")),
        ("yes (metric = recall, checked)", ("yes", "metric = recall, checked")),
        ("dates yes, scope no", ("partial", "dates yes, scope no")),
        ("maybe", None),
    ],
)
def test_parse_verification(value: str, expected: tuple[str, str | None] | None) -> None:
    assert parse_verification(value) == expected


def test_drop_and_append_phrases() -> None:
    bullet = "Queued orders in RabbitMQ, a message broker."
    assert drop_phrase(bullet, ", a message broker") == "Queued orders in RabbitMQ."
    assert drop_phrase(bullet, "Okta") is None
    assert (
        append_phrase("Built a tool.", "\u2013 reused by two teams")
        == "Built a tool \u2013 reused by two teams."
    )
    assert (
        append_phrase("Built a tool", ", reused by two teams")
        == "Built a tool, reused by two teams"
    )


def test_note_kinds_come_from_tags() -> None:
    notes = [
        note_of(segment)
        for segment in segments(
            comment_lines(
                [
                    Comment(1, " WARNING: never claim the design."),
                    Comment(2, " UPGRADE AVAILABLE: add the team size."),
                    Comment(3, " TODO: add the dates"),
                    Comment(4, " NOTE ON ORDER: minor after major."),
                ]
            )
        )
    ]
    assert [(note.kind, note.text) for note in notes] == [
        ("warning", "never claim the design."),
        ("upgrade", "add the team size."),
        ("warning", "TODO: add the dates"),
        ("note", "NOTE ON ORDER: minor after major."),
    ]


def test_labels() -> None:
    assert label_parts("EMBEDDED / C++ FIRMWARE") == {"embedded", "c++ firmware"}
    assert display_label("DATA / ML PIPELINES:") == "Data / ML Pipelines"
    assert display_label("ML / AI") == "ML / AI"
    assert slug("R&D Labs Caf\u00e9") == "r_d_labs_cafe"
