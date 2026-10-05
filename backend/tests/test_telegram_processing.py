"""The bot's reply once an entry is processed: the saved facts, as saved (FR-CAP-5)."""

from app.schema.fact import Fact
from app.telegram.processing import failure_text, reply_text

LINK = "https://journal.example.com/journal#entry"


def facts(*statements: str) -> list[Fact]:
    return [Fact(id=f"e_{n}", statement=text) for n, text in enumerate(statements, start=1)]


def test_the_reply_lists_the_saved_facts_and_links_to_the_entry() -> None:
    saved = facts(
        "Cut the nightly export from about 50 to 12 minutes by batching database writes.",
        "Wrote the runbook for the export queue.",
    )
    assert reply_text("work", saved, 0, LINK) == (
        "Saved 2 facts from this entry:\n"
        "- Cut the nightly export from about 50 to 12 minutes by batching database writes.\n"
        "- Wrote the runbook for the export queue.\n"
        "\n"
        "See them in your journal:\n"
        f"{LINK}"
    )


def test_past_three_facts_the_reply_counts_the_rest() -> None:
    reply = reply_text("work", facts("One.", "Two.", "Three.", "Four.", "Five."), 0, LINK)
    assert reply.splitlines()[:5] == [
        "Saved 5 facts from this entry:",
        "- One.",
        "- Two.",
        "- Three.",
        "...and 2 more.",
    ]


def test_one_fact_is_singular() -> None:
    assert reply_text("work", facts("Shipped the login fix."), 0, LINK).startswith(
        "Saved 1 fact from this entry:\n"
    )


def test_facts_left_out_for_their_numbers_are_counted() -> None:
    """FR-EXT-2: a fact with a number the note doesn't give is dropped; the owner hears of it."""
    reply = reply_text("work", facts("Added image uploads."), 1, LINK)
    assert "I left out 1 fact with numbers your note doesn't give." in reply.splitlines()
    nothing = reply_text("work", [], 2, LINK)
    assert nothing.splitlines()[:2] == [
        "I didn't find a fact to save in this entry.",
        "I left out 2 facts with numbers your note doesn't give.",
    ]
    assert nothing.endswith(f"It's in your journal:\n{LINK}")


def test_an_entry_not_about_work_is_only_acknowledged() -> None:
    """FR-CAP-8."""
    assert reply_text("other", [], 0, LINK) == (
        "Noted. This doesn't look like work, so I saved no facts from it."
    )


def test_the_failure_notice_says_why_and_where_the_entry_is() -> None:
    assert failure_text("the model declined it", LINK) == (
        "I couldn't read this entry because the model declined it, so I saved no facts from it. "
        f"It's still in your journal:\n{LINK}"
    )
