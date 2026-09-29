from app.importers.latex import Blank, Comment
from app.importers.tags import (
    CommentLine,
    Segment,
    benched_entries,
    comment_lines,
    hanging_entries,
    id_fields,
    list_items,
    needed_questions,
    segments,
    swap_entries,
    tag_of,
)


def lines(*texts: str) -> list[CommentLine | None]:
    """Comment lines from `%` texts; an empty text is a blank line."""
    events: list[Comment | Blank] = [
        Comment(number, text) if text else Blank(number) for number, text in enumerate(texts, 1)
    ]
    return comment_lines(events)


def tags(*texts: str) -> list[tuple[str | None, int]]:
    """The tag and line count of each segment."""
    return [(segment.tag, len(segment.lines)) for segment in segments(lines(*texts))]


def segment(*texts: str) -> Segment:
    [only] = segments(lines(*texts))
    return only


def test_comment_lines_mark_breaks_and_indents() -> None:
    result = lines(" ID: x", "", "------- NORTHWIND", "", "   hanging")
    assert result == [CommentLine(1, 1, "ID: x"), None, None, None, CommentLine(5, 3, "hanging")]


def test_tags_are_recognised_at_the_start_of_a_line() -> None:
    assert tag_of("ID: st_x | STRENGTH: high") == "ID"
    assert tag_of("KEEP for: ML/data") == "KEEP"
    assert tag_of("STRONGEST FOR: backend") == "KEEP"
    assert tag_of("OMIT for:    gaming") == "CUT"
    assert tag_of("*** CHECK BEFORE INCLUDING ***") == "WARNING"
    assert tag_of("NOTE ON ORDER: minor after major") is None
    assert tag_of("Keep the title consistent") is None
    assert tag_of("OPEN QUESTIONS -- still to answer") == "QUESTIONS"
    assert tag_of("OPEN QUESTION: how many users?") == "OPEN_QUESTION"


def test_an_id_line_stands_alone_and_prose_follows_its_sentence() -> None:
    assert tags(
        " ID: nw_x | STRENGTH: high",
        " The only public repo; link it and",
        " say so.",
        " A second note.",
        " WARNING: never claim the design;",
        " say implemented.",
    ) == [("ID", 1), (None, 2), (None, 1), ("WARNING", 2)]


def test_blank_comment_lines_end_prose() -> None:
    assert tags(" first note", "", " second note") == [(None, 1), (None, 1)]


def test_hanging_lines_continue_but_a_tagged_line_always_starts_a_segment() -> None:
    assert tags(
        " *** CHECK BEFORE INCLUDING ***",
        " The rule set is small.",
        "   INCLUDE for: backend roles.",
        "   OMIT for: design roles.",
        ' TITLE: official title is "Analyst". Approved variants:',
        "   Data Analyst | Analytics Engineer",
        "   BI Developer",
        " Never add Senior.",
    ) == [("WARNING", 2), ("KEEP", 1), ("CUT", 1), ("TITLE", 4)]


def test_swaps_continue_while_a_quote_or_parenthesis_is_open() -> None:
    assert tags(
        ' SWAPS: "services that consume events and',
        ' write them" (data roles -- matches',
        ' "streaming")',
        '        "event-driven services" (platform roles)',
        " The second clause is optional.",
    ) == [("SWAPS", 4), (None, 1)]


def test_blocks_take_blank_lines_until_the_next_block() -> None:
    assert tags(
        " SKILLS PRESETS -- pick one",
        "",
        " BACKEND",
        "   Languages: Python",
        "",
        " OPEN QUESTIONS -- still to answer",
        "",
        "   q_x   how many?",
    ) == [("PRESETS", 3), ("QUESTIONS", 2)]


def test_benched_entries_allow_breaks_and_end_at_other_comments() -> None:
    assert tags(
        " BENCHED -- never rendered.",
        ' ID: a -- "First text',
        '   continued."',
        "   REASON: no outcome.",
        "",
        ' ID: b -- "Second."',
        "   REASON: filler.",
        " An unrelated note.",
    ) == [("BENCHED", 6), (None, 1)]


def test_id_fields() -> None:
    fields = id_fields(segment(" ID: nw_x | STRENGTH: low (HIGH for data roles) | VERIFIED: yes"))
    assert fields == {"ID": "nw_x", "STRENGTH": "low (HIGH for data roles)", "VERIFIED": "yes"}


def test_swap_entries() -> None:
    entries, unparsed = swap_entries(
        segment(
            ' SWAPS: "event-driven services" (platform roles)',
            '        "services that consume events',
            '         and write them" (data roles -- matches "streaming")',
            '        drop ", a message broker" for non-platform roles',
            '        append " for two teams" when applying to data roles.',
            '        lead with "Rewrote the script"',
            "        for testing roles -- shows discipline.",
            '        "Postgres (SQL)" -- use the "relational" gloss.',
            "        mention the migration",
        )
    )
    assert [(entry.verb, entry.phrase, entry.context) for entry in entries] == [
        (None, "event-driven services", "platform roles"),
        (None, "services that consume events and write them", 'data roles -- matches "streaming"'),
        ("drop", ", a message broker", "for non-platform roles"),
        ("append", " for two teams", "when applying to data roles."),
        ("lead with", "Rewrote the script", "for testing roles -- shows discipline."),
        (None, "Postgres (SQL)", 'use the "relational" gloss. mention the migration'),
    ]
    assert unparsed == []
    assert swap_entries(segment(" SWAPS: mention the migration"))[1] == ["mention the migration"]


def test_needed_questions_are_found_anywhere() -> None:
    assert needed_questions("NEEDS: q_nw_volume -- orders per day") == ["q_nw_volume"]
    assert needed_questions("Weak bullet. NEEDS: q_a, q_b and q_c.") == ["q_a", "q_b", "q_c"]
    assert needed_questions("until q_nw_scale is answered") == []


def test_benched_entries() -> None:
    entries = benched_entries(
        segment(
            " BENCHED -- never rendered.",
            ' ID: nw_meet -- "Attended design reviews with the',
            '   platform team."',
            "   REASON: attendance is not",
            "   an outcome.",
            ' ID: nw_misc -- "No reason given."',
        )
    )
    assert [(e.line, e.id, e.text, e.reason) for e in entries] == [
        (
            2,
            "nw_meet",
            "Attended design reviews with the platform team.",
            "attendance is not an outcome.",
        ),
        (6, "nw_misc", "No reason given.", None),
    ]


def test_hanging_entries_join_continuation_lines() -> None:
    body = lines(
        "   Backend : Algorithms, Operating Systems,",
        "             Distributed Systems",
        "   Data    : Statistics",
    )
    present = [line for line in body if line is not None]
    assert [(first.line, text) for first, text in hanging_entries(present)] == [
        (1, "Backend : Algorithms, Operating Systems, Distributed Systems"),
        (3, "Data    : Statistics"),
    ]


def test_list_items_drop_the_explanation_and_split_on_and() -> None:
    assert list_items("ML/data, search and ranking roles -- because of the index work.") == [
        "ML/data",
        "search",
        "ranking roles",
    ]
    assert list_items("mobile, design/UX, and games roles.") == [
        "mobile",
        "design/UX",
        "games roles",
    ]
