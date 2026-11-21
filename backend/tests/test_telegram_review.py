"""The review's buttons and texts in Telegram (FR-REV-5, T-077), without the database."""

import uuid
from datetime import UTC, datetime

from app.inbox.review import ChatReview, InboxChangeSet, InboxOp
from app.telegram import review

CHANGE_SET = uuid.UUID("0b8f2c1e-5d6a-4c3b-9e7f-1a2b3c4d5e6f")


def test_buttons_carry_the_change_set_the_change_and_what_was_shown() -> None:
    data = f"rv:{CHANGE_SET.hex}:12:a:0123abcd"
    assert review.parse(data) == review.Pressed(CHANGE_SET, 12, "a", "0123abcd")
    assert len(data.encode()) <= 64, "Telegram's limit for what a button carries"


def test_other_buttons_arent_the_reviews() -> None:
    """The role question's buttons carry a role's ID, which never holds a colon."""
    for data in (None, "northwind", "rv:", "rv:nothex:1:a:", f"rv:{CHANGE_SET.hex}:x:a:"):
        assert review.parse(data) is None


def test_a_long_text_is_cut_to_fit_the_message() -> None:
    long = "Wrote " + "the runbook " * 200
    op = InboxOp(
        position=0,
        kind="edit_bullet",
        status="proposed",
        reason=None,
        title="Reword a bullet under Engineer, Northwind Traders",
        context=None,
        note=None,
        before=long,
        after="Wrote the runbook.",
        diff=(),
        outputs=(),
        sources=(),
        verifier_report=None,
        editable=True,
        needs=None,
    )
    change_set = InboxChangeSet(
        id=CHANGE_SET,
        author="ai",
        summary=None,
        base_version=1,
        created_at=datetime(2026, 11, 16, tzinfo=UTC),
        ops=(op,),
    )
    text = review.op_text(ChatReview(change_set, "proposed", frozenset()), op, None)
    before = text.split("\n")[1]
    assert len(before) == len("Before: ") + review.TEXT_LIMIT
    assert before.endswith("...")
    assert len(text) < 4096
