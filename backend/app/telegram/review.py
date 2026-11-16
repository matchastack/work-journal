"""Reviewing a refresh's proposed changes in the chat, one at a time (FR-REV-5, T-077).

The bot's message about a refresh's proposals has a Review here button (`review_keyboard`).
Pressing it sends a message that shows the first change still waiting for a decision: what it
does, before and after, how many facts it rests on, the verifier's result and a link to the
Inbox, with buttons to accept it, reject it or go on to the next one. Each press edits that
message to show the next change waiting, so the chat holds one message, not one per change. Once
every change is decided, it shows how many were accepted, with a button that applies them.

The decisions are the Inbox's own (`app/inbox/review.py`): rejecting a change rejects the ones
that need it, Apply makes one new version and brings the outputs up to date, and only a change
the verifier passed can be accepted here; a flagged one is accepted in the Inbox, where its whole
report shows (FR-FID-7). Edits and rejection reasons stay in the Inbox too.

A button carries the change set, the change it was shown with and a fingerprint of what the
message showed (`_fingerprint`). When the change set changed since, in the Inbox or in another
message, a press does nothing but say so and show where it stands now. A change's text is sent
only to the owner's own chat, and never logged.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.review import (
    ChatReview,
    InboxOp,
    NotFound,
    ReviewError,
    accept_if_passed,
    apply_change_set,
    decide,
    load_review,
)
from app.telegram.api import BotCall, send
from app.telegram.refresh import inbox_link
from app.telegram.updates import CallbackQuery

PREFIX = "rv:"
"""What every button here carries first. Profile IDs, which the role question's buttons carry,
never hold a colon."""
REVIEW, ACCEPT, REJECT, NEXT, APPLY = "s", "a", "r", "n", "p"
TEXT_LIMIT = 900
"""The most characters of one text, such as a bullet, that a message shows: well past a bullet's
two lines, and well inside the 4096 characters of a Telegram message."""

GONE = "These changes aren't there any more."
STALE = "That changed since this message was sent, so I did nothing. Here's where it stands now."
CLOSED = {
    "applied": "These changes are applied already.",
    "rejected": "These changes are closed already.",
}


@dataclass(frozen=True)
class Pressed:
    """What a button carries."""

    change_set_id: uuid.UUID
    position: int
    action: str
    fingerprint: str


def review_keyboard(change_set_id: uuid.UUID) -> dict[str, Any]:
    """The Review here button under the bot's message about a refresh's proposals."""
    return _keyboard([[("Review here", _data(change_set_id, 0, REVIEW))]])


def parse(data: str | None) -> Pressed | None:
    """What a button carries, or None for a button that isn't one of these."""
    if data is None or not data.startswith(PREFIX):
        return None
    parts = data.removeprefix(PREFIX).split(":")
    if len(parts) != 4:
        return None
    change_set, position, action, fingerprint = parts
    try:
        return Pressed(uuid.UUID(hex=change_set), int(position), action, fingerprint)
    except ValueError:
        return None


async def press(
    session: AsyncSession,
    owner: uuid.UUID,
    query: CallbackQuery,
    *,
    app_url: str | None,
    now: datetime,
) -> tuple[BotCall, ...]:
    """Answer a press of one of the review's buttons, pressed at `now`, in the owner's chat."""
    pressed = parse(query.data)
    message = query.message
    if pressed is None or message is None:
        return (_answer(query, GONE),)
    chat_id, message_id = message.chat.id, message.message_id
    try:
        review = await load_review(session, owner, pressed.change_set_id)
    except NotFound:
        return (_answer(query, GONE),)
    if pressed.action == REVIEW:
        if review.status != "proposed":
            return (_answer(query, CLOSED[review.status]),)
        text, keyboard = _showing(review, None, app_url)
        return (_answer(query), send(chat_id, text, reply_markup=keyboard))
    if review.status != "proposed":
        return (
            _answer(query, CLOSED[review.status]),
            _edit(chat_id, message_id, CLOSED[review.status]),
        )
    ops = review.change_set.ops
    if pressed.action == NEXT:
        return _show(query, chat_id, message_id, review, pressed.position, app_url)
    if pressed.action == APPLY:
        if pressed.fingerprint != _fingerprint(ops):
            return _show(query, chat_id, message_id, review, None, app_url, notice=STALE)
        accepted = sum(1 for op in ops if op.status == "accepted")
        try:
            version = await apply_change_set(session, owner, pressed.change_set_id, now=now)
        except ReviewError as error:
            return _show(query, chat_id, message_id, review, None, app_url, notice=_notice(error))
        return (_answer(query), _edit(chat_id, message_id, applied_text(accepted, version)))
    if pressed.action not in (ACCEPT, REJECT) or not 0 <= pressed.position < len(ops):
        return (_answer(query, GONE),)
    op = ops[pressed.position]
    if op.status != "proposed" or pressed.fingerprint != _fingerprint((op,)):
        return _show(
            query, chat_id, message_id, review, pressed.position - 1, app_url, notice=STALE
        )
    try:
        if pressed.action == ACCEPT:
            await accept_if_passed(session, owner, pressed.change_set_id, pressed.position)
        else:
            await decide(session, owner, pressed.change_set_id, pressed.position, "rejected")
    except ReviewError as error:
        return _show(
            query, chat_id, message_id, review, pressed.position - 1, app_url, notice=_notice(error)
        )
    after = await load_review(session, owner, pressed.change_set_id)
    notice = None
    if pressed.action == REJECT:
        also = _newly_rejected(ops, after.change_set.ops) - 1
        if also:
            notice = f"Rejected, with {_count(also, 'change')} that need it."
    return _show(query, chat_id, message_id, after, pressed.position, app_url, notice=notice)


def applied_text(accepted: int, version: int | None) -> str:
    """What the message says once the decided changes are applied, as the Inbox says it, with how
    many were accepted."""
    if version is None:
        return "Closed without changes: your profile stays as it was."
    return f"Applied {_count(accepted, 'change')}: your profile is now at version {version}."


def op_text(review: ChatReview, op: InboxOp, app_url: str | None) -> str:
    """One change as the message shows it, in plain text."""
    ops = review.change_set.ops
    lines = [f"Change {op.position + 1} of {len(ops)}: {op.title}"]
    if op.context is not None:
        lines.append(f"About: {_cut(op.context)}")
    if op.before is not None:
        lines.append(f"Before: {_cut(op.before)}")
    if op.after is not None:
        lines.append(f"After: {_cut(op.after)}")
    if op.note is not None:
        lines.append(f"Note: {_cut(op.note)}")
    about: list[str] = []
    if op.sources:
        about.append(f"From {_count(len(op.sources), 'fact')}.")
    if op.needs is not None:
        about.append(f"It needs change {op.needs + 1}.")
    if (verdict := _verdict(review, op)) is not None:
        about.append(verdict)
    waiting = sum(1 for item in ops if item.status == "proposed")
    about.append(f"{waiting} waiting.")
    text = "\n".join(lines) + "\n\n" + " ".join(about)
    if app_url is not None:
        text += f"\n\nIn the Inbox: {inbox_link(app_url)}"
    return text


def decided_text(review: ChatReview) -> str:
    """What the message says once every change is decided."""
    ops = review.change_set.ops
    accepted = sum(1 for op in ops if op.status == "accepted")
    rejected = len(ops) - accepted
    text = f"Every change is decided: {accepted} accepted, {rejected} rejected."
    if accepted:
        return (
            f"{text} Apply makes a new version of your profile from the accepted ones, and "
            "brings your resume, LinkedIn pack and published portfolio page up to date."
        )
    return f"{text} Finish closes them, and your profile stays as it is."


def _show(
    query: CallbackQuery,
    chat_id: int,
    message_id: int,
    review: ChatReview,
    after: int | None,
    app_url: str | None,
    *,
    notice: str | None = None,
) -> tuple[BotCall, ...]:
    """Answer the press, and edit its message to show the next change waiting after `after`."""
    text, keyboard = _showing(review, after, app_url)
    edit = BotCall(
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": keyboard},
    )
    return (_answer(query, notice), edit)


def _showing(
    review: ChatReview, after: int | None, app_url: str | None
) -> tuple[str, dict[str, Any]]:
    """The text and buttons of the message: the first change waiting after position `after`, or
    else the first one waiting, or once none waits, the decided changes and their Apply button."""
    ops = review.change_set.ops
    change_set_id = review.change_set.id
    waiting = [op for op in ops if op.status == "proposed"]
    if not waiting:
        accepted = sum(1 for op in ops if op.status == "accepted")
        label = f"Apply {_count(accepted, 'change')}" if accepted else "Finish"
        apply = _data(change_set_id, 0, APPLY, _fingerprint(ops))
        return decided_text(review), _keyboard([[(label, apply)]])
    start = -1 if after is None else after
    op = next((op for op in waiting if op.position > start), waiting[0])
    fingerprint = _fingerprint((op,))
    decide_row: list[tuple[str, str]] = []
    if op.position in review.passed:
        decide_row.append(("Accept", _data(change_set_id, op.position, ACCEPT, fingerprint)))
    decide_row.append(("Reject", _data(change_set_id, op.position, REJECT, fingerprint)))
    rows = [decide_row]
    if len(waiting) > 1:
        rows.append([("Next", _data(change_set_id, op.position, NEXT))])
    return op_text(review, op, app_url), _keyboard(rows)


def _verdict(review: ChatReview, op: InboxOp) -> str | None:
    """What the verifier found, if the change has text it checks."""
    report = op.verifier_report
    if op.position in review.passed:
        return None if report is None else "The verifier passed it."
    if report is None:
        return "Its verifier report can't be read, so decide on it in the Inbox."
    errors = report.errors
    why = f": {_cut(errors[0].message).rstrip(' .')}" if errors else ""
    return f"The verifier flagged it{why}. Accept it in the Inbox, where its whole report shows."


def _fingerprint(ops: Sequence[InboxOp]) -> str:
    """A short digest of what the message showed of these changes, and their decisions."""
    shown = "\n".join(
        op.model_dump_json(include={"position", "status", "title", "context", "before", "after"})
        for op in ops
    )
    return hashlib.sha256(shown.encode()).hexdigest()[:8]


def _newly_rejected(before: Sequence[InboxOp], after: Sequence[InboxOp]) -> int:
    return sum(
        1
        for old, new in zip(before, after, strict=True)
        if old.status != "rejected" and new.status == "rejected"
    )


def _data(change_set_id: uuid.UUID, position: int, action: str, fingerprint: str = "") -> str:
    """What a button carries: at most 51 of the 64 bytes Telegram allows."""
    return f"{PREFIX}{change_set_id.hex}:{position}:{action}:{fingerprint}"


def _keyboard(rows: Sequence[Sequence[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows
        ]
    }


NOTICE_LIMIT = 200
"""The most characters Telegram shows in the notice that answers a press."""


def _notice(error: ReviewError) -> str:
    """The Inbox's reason, as a sentence that fits the notice."""
    text = f"{str(error)[:1].upper()}{str(error)[1:]}."
    return text if len(text) <= NOTICE_LIMIT else f"{text[: NOTICE_LIMIT - 3]}..."


def _cut(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else f"{text[: TEXT_LIMIT - 3].rstrip()}..."


def _answer(query: CallbackQuery, text: str | None = None) -> BotCall:
    """Tell Telegram the press was handled, which ends the button's spinner, with a short notice
    if there's something to say."""
    params: dict[str, Any] = {"callback_query_id": query.id}
    if text is not None:
        params["text"] = text
    return BotCall("answerCallbackQuery", params)


def _edit(chat_id: int, message_id: int, text: str) -> BotCall:
    """Replace a message's text. Its buttons go with it."""
    return BotCall("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
