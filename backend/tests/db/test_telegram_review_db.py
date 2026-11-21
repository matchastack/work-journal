"""Reviewing a refresh's proposed changes in Telegram, one at a time (FR-REV-5, T-077)."""

import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import TelegramLink, User
from app.db.profiles import (
    decide_op,
    get_change_set,
    latest_version,
    propose_change_set,
    save_version,
)
from app.db.settings import save_confidential_terms
from app.db.store import save_facts
from app.inbox import review as inbox
from app.schema.changes import (
    AddBullet,
    AddRole,
    AddSkill,
    ChangeSet,
    EditBullet,
    ReviewedOp,
    SetBulletStatus,
    UpdateField,
)
from app.schema.fact import Fact
from app.schema.profile import Bullet, Profile, Role, Skill
from app.schema.settings import ConfidentialTerms
from app.schema.verification import VerifierFinding, VerifierReport
from app.telegram import review
from app.telegram.api import BotCall
from app.telegram.journal import Handled, handle_update

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
IMPORTED = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
APP_URL = "https://journal.example.com"
INBOX = f"\n\nIn the Inbox: {APP_URL}/inbox"
EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
REWORDED = "Cut the nightly export from 50 to 12 minutes by batching database writes."
IN_GO = "Wrote the runbook in Go."
PROPOSALS_ID = 700
"""The bot's message about the refresh's proposals, with the Review here button."""
REVIEW_ID = 701
"""The message the bot sent to review them in."""
QUERY_ID = "4382bfdwdsb323b2d9"
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


def report(text: str, *errors: str) -> dict[str, Any]:
    findings = tuple(
        VerifierFinding(check="claims", severity="error", code="added_tool", message=error)
        for error in errors
    )
    return VerifierReport(text=text, passed=not errors, findings=findings).model_dump(mode="json")


THREE_CHANGES = (
    ReviewedOp(
        op=EditBullet(bullet_id="nw_export", text=REWORDED, fact_ids=("fact_nw_export",)),
        verifier_report=report(REWORDED),
    ),
    ReviewedOp(
        op=AddBullet(
            parent_id="northwind",
            bullet=Bullet(id="nw_runbook", text=IN_GO),
            fact_ids=("fact_nw_export",),
        ),
        verifier_report=report(IN_GO, "No fact names Go."),
    ),
    ReviewedOp(op=SetBulletStatus(bullet_id="nw_orders", status="benched", reason="Older")),
)
"""A reworded bullet the verifier passed, a new bullet it flagged, and benching a bullet."""

NEW_ROLE = (
    ReviewedOp(
        op=AddRole(
            role=Role(id="fabrikam", name="Fabrikam", position="Engineer", start_date="2026-09"),
            position=0,
        )
    ),
    ReviewedOp(
        op=AddBullet(parent_id="fabrikam", bullet=Bullet(id="fb_queue", text="Built a queue.")),
        verifier_report=report("Built a queue."),
    ),
    ReviewedOp(op=UpdateField(target_id="fabrikam", field="location", value="Remote")),
    ReviewedOp(op=AddSkill(skill=Skill(name="Go", category="Languages"))),
)
"""A new role, a bullet under it and a change to it, which both need it, and a new skill."""


async def proposed(
    session: AsyncSession, chat_id: int, ops: tuple[ReviewedOp, ...] = THREE_CHANGES
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user linked to `chat_id`, with the profile loaded and `ops` proposed. Returns the user's
    and the change set's IDs."""
    user = User()
    session.add(user)
    await session.flush()
    session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    await save_version(session, user.id, PROFILE, author="owner")
    await save_facts(session, user.id, IMPORTED)
    change_set = ChangeSet(author="ai", ops=ops, summary="From your journal", base_version=1)
    return user.id, await propose_change_set(session, user.id, change_set)


def press(chat_id: int, data: str, *, message_id: int = REVIEW_ID) -> str:
    """An update for a press of the button carrying `data`, under message `message_id`."""
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "callback_query": {
                "id": QUERY_ID,
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat_instance": "-1234",
                "message": {
                    "message_id": message_id,
                    "from": {"id": 99, "is_bot": True, "first_name": "Journal"},
                    "chat": {"id": chat_id, "type": "private"},
                    "date": 1791106200,
                    "text": "Change 1 of 3",
                },
                "data": data,
            },
        }
    )


async def pressing(session: AsyncSession, chat_id: int, data: str, **options: Any) -> Handled:
    return await handle_update(session, press(chat_id, data, **options), app_url=APP_URL)


def answered(notice: str | None = None) -> BotCall:
    params: dict[str, Any] = {"callback_query_id": QUERY_ID}
    if notice is not None:
        params["text"] = notice
    return BotCall("answerCallbackQuery", params)


def buttons(call: BotCall) -> dict[str, str]:
    """The buttons under a message, by their text, with what each carries."""
    rows = call.params["reply_markup"]["inline_keyboard"]
    return {button["text"]: button["callback_data"] for row in rows for button in row}


async def statuses(
    session: AsyncSession, user_id: uuid.UUID, change_set_id: uuid.UUID
) -> list[str]:
    saved = await get_change_set(session, user_id, change_set_id)
    return [reviewed.status for reviewed in saved.change_set.ops]


def review_here(change_set_id: uuid.UUID) -> str:
    """What the Review here button under the bot's message about the proposals carries."""
    [[button]] = review.review_keyboard(change_set_id)["inline_keyboard"]
    return button["callback_data"]


async def shown(session: AsyncSession, chat_id: int, change_set_id: uuid.UUID) -> BotCall:
    """The review's message, as Review here sends it."""
    handled = await pressing(session, chat_id, review_here(change_set_id), message_id=PROPOSALS_ID)
    assert handled.calls[0] == answered()
    return handled.calls[1]


async def test_review_here_sends_the_first_change_with_its_buttons(
    session: AsyncSession, chat_id: int
) -> None:
    _, change_set_id = await proposed(session, chat_id)
    keyboard = review.review_keyboard(change_set_id)
    [[button]] = keyboard["inline_keyboard"]
    assert button["text"] == "Review here"
    sent = await shown(session, chat_id, change_set_id)
    assert (sent.method, sent.params["chat_id"]) == ("sendMessage", chat_id)
    assert sent.params["text"] == (
        "Change 1 of 3: Reword a bullet under Engineer, Northwind Traders\n"
        f"Before: {EXPORT}\n"
        f"After: {REWORDED}\n\n"
        "From 1 fact. The verifier passed it. 3 waiting." + INBOX
    )
    assert list(buttons(sent)) == ["Accept", "Reject", "Next"]
    assert all(len(data.encode()) <= 64 for data in buttons(sent).values())


async def test_accepting_shows_the_next_change_in_the_same_message(
    session: AsyncSession, chat_id: int
) -> None:
    """A flagged change has no Accept button: it's accepted in the Inbox (FR-FID-7)."""
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    handled = await pressing(session, chat_id, buttons(sent)["Accept"])
    assert await statuses(session, user_id, change_set_id) == ["accepted", "proposed", "proposed"]
    answer, edit = handled.calls
    assert answer == answered()
    assert (edit.method, edit.params["message_id"]) == ("editMessageText", REVIEW_ID)
    assert edit.params["text"] == (
        "Change 2 of 3: Add a bullet under Engineer, Northwind Traders\n"
        f"After: {IN_GO}\n\n"
        "From 1 fact. The verifier flagged it: No fact names Go. Accept it in the Inbox, where "
        "its whole report shows. 2 waiting." + INBOX
    )
    assert list(buttons(edit)) == ["Reject", "Next"]


async def test_next_goes_round_the_changes_still_waiting(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    titles = []
    for _ in range(3):
        [_, sent] = (await pressing(session, chat_id, buttons(sent)["Next"])).calls
        titles.append(sent.params["text"].split(":")[0])
    assert titles == ["Change 2 of 3", "Change 3 of 3", "Change 1 of 3"]
    assert await statuses(session, user_id, change_set_id) == ["proposed"] * 3
    [_, sent] = (await pressing(session, chat_id, buttons(sent)["Reject"])).calls
    assert sent.params["text"].startswith("Change 2 of 3")


async def test_a_change_with_nothing_to_verify_can_be_accepted(
    session: AsyncSession, chat_id: int
) -> None:
    """Benching a bullet has no text for the verifier, as the Inbox's bulk accept counts it."""
    user_id, change_set_id = await proposed(session, chat_id)
    await decide_op(session, user_id, change_set_id, 0, "accepted")
    await decide_op(session, user_id, change_set_id, 1, "rejected")
    sent = await shown(session, chat_id, change_set_id)
    assert sent.params["text"] == (
        "Change 3 of 3: Bench a bullet under Engineer, Northwind Traders\n"
        f"About: {PROFILE.work[0].highlights[0].text}\n"
        "Before: active\n"
        "After: benched: Older\n\n"
        "1 waiting." + INBOX
    )
    assert list(buttons(sent)) == ["Accept", "Reject"]


async def test_a_flagged_change_is_never_accepted_here(session: AsyncSession, chat_id: int) -> None:
    """Even from a message sent before a confidential term flagged it (T-049)."""
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    accept = buttons(sent)["Accept"]
    await save_confidential_terms(session, user_id, ConfidentialTerms(terms=("nightly export",)))
    handled = await pressing(session, chat_id, accept)
    assert handled.calls[0] == answered("The verifier flagged it, so accept it in the Inbox.")
    assert await statuses(session, user_id, change_set_id) == ["proposed"] * 3
    assert handled.calls[1].params["text"].startswith("Change 1 of 3")
    assert "Accept" not in buttons(handled.calls[1])


async def test_rejecting_a_change_rejects_the_changes_that_need_it(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, change_set_id = await proposed(session, chat_id, NEW_ROLE)
    sent = await shown(session, chat_id, change_set_id)
    assert sent.params["text"].startswith("Change 1 of 4: Add a role")
    handled = await pressing(session, chat_id, buttons(sent)["Reject"])
    assert await statuses(session, user_id, change_set_id) == ["rejected"] * 3 + ["proposed"]
    assert handled.calls[0] == answered("Rejected, with 2 changes that need it.")
    assert handled.calls[1].params["text"].startswith("Change 4 of 4: Add a skill")


async def test_a_change_that_needs_another_says_so(session: AsyncSession, chat_id: int) -> None:
    user_id, change_set_id = await proposed(session, chat_id, NEW_ROLE)
    await decide_op(session, user_id, change_set_id, 0, "accepted")
    sent = await shown(session, chat_id, change_set_id)
    assert sent.params["text"] == (
        "Change 2 of 4: Add a bullet under Engineer, Fabrikam\n"
        "After: Built a queue.\n\n"
        "It needs change 1. The verifier passed it. 3 waiting." + INBOX
    )


async def test_a_change_that_needs_a_rejected_one_cant_be_accepted(
    session: AsyncSession, chat_id: int
) -> None:
    """The Inbox's own rule: undo that rejection first."""
    user_id, change_set_id = await proposed(session, chat_id, NEW_ROLE)
    await decide_op(session, user_id, change_set_id, 0, "rejected")
    sent = await shown(session, chat_id, change_set_id)
    assert sent.params["text"].startswith("Change 2 of 4")
    handled = await pressing(session, chat_id, buttons(sent)["Accept"])
    assert handled.calls[0] == answered(
        "It needs a change you rejected; undo that rejection first."
    )
    assert await statuses(session, user_id, change_set_id) == ["rejected"] + ["proposed"] * 3
    assert handled.calls[1].params["text"].startswith("Change 2 of 4")


async def test_a_press_after_a_decision_in_the_inbox_does_nothing(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    await inbox.decide(session, user_id, change_set_id, 0, "rejected", "Too long")
    handled = await pressing(session, chat_id, buttons(sent)["Accept"])
    assert handled.calls[0] == answered(review.STALE)
    assert await statuses(session, user_id, change_set_id) == ["rejected", "proposed", "proposed"]
    assert handled.calls[1].params["text"].startswith("Change 2 of 3")


async def test_a_press_after_an_edit_in_the_inbox_does_nothing(
    session: AsyncSession, chat_id: int
) -> None:
    """The edited text waits for a decision again, but the message showed the old text."""
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    edited = EditBullet(bullet_id="nw_export", text="Cut the export to 12 minutes.")
    await decide_op(
        session,
        user_id,
        change_set_id,
        0,
        "proposed",
        op=edited,
        verifier_report=report(edited.text),
    )
    handled = await pressing(session, chat_id, buttons(sent)["Accept"])
    assert handled.calls[0] == answered(review.STALE)
    assert await statuses(session, user_id, change_set_id) == ["proposed"] * 3
    assert "After: Cut the export to 12 minutes." in handled.calls[1].params["text"]


async def test_once_every_change_is_decided_apply_makes_a_new_version(
    session: AsyncSession, chat_id: int
) -> None:
    """FR-REV-3, as the Inbox's Apply."""
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    for button in ("Accept", "Reject", "Accept"):
        [_, sent] = (await pressing(session, chat_id, buttons(sent)[button])).calls
    assert sent.params["text"] == (
        "Every change is decided: 2 accepted, 1 rejected. Apply makes a new version of your "
        "profile from the accepted ones, and brings your resume, LinkedIn pack and published "
        "portfolio page up to date."
    )
    assert list(buttons(sent)) == ["Apply 2 changes"]
    handled = await pressing(session, chat_id, buttons(sent)["Apply 2 changes"])
    assert handled.calls == (
        answered(),
        BotCall(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": REVIEW_ID,
                "text": "Applied 2 changes: your profile is now at version 2.",
            },
        ),
    )
    version = await latest_version(session, user_id)
    assert version is not None and version.number == 2
    [bullet] = [item for item in version.profile.work[0].highlights if item.id == "nw_export"]
    assert bullet.text == REWORDED
    saved = await get_change_set(session, user_id, change_set_id)
    assert saved.change_set.status == "applied"


async def test_with_nothing_accepted_finish_closes_the_changes(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    for _ in range(3):
        [_, sent] = (await pressing(session, chat_id, buttons(sent)["Reject"])).calls
    assert sent.params["text"] == (
        "Every change is decided: 0 accepted, 3 rejected. Finish closes them, and your profile "
        "stays as it is."
    )
    [_, closed] = (await pressing(session, chat_id, buttons(sent)["Finish"])).calls
    assert closed.params["text"] == "Closed without changes: your profile stays as it was."
    version = await latest_version(session, user_id)
    assert version is not None and version.number == 1


async def test_apply_after_a_change_in_the_inbox_does_nothing(
    session: AsyncSession, chat_id: int
) -> None:
    """It would apply other changes than the message counted."""
    user_id, change_set_id = await proposed(session, chat_id)
    for position in range(3):
        await decide_op(session, user_id, change_set_id, position, "rejected")
    sent = await shown(session, chat_id, change_set_id)
    await decide_op(session, user_id, change_set_id, 0, "accepted")
    handled = await pressing(session, chat_id, buttons(sent)["Finish"])
    assert handled.calls[0] == answered(review.STALE)
    assert handled.calls[1].params["text"].startswith("Every change is decided: 1 accepted")
    saved = await get_change_set(session, user_id, change_set_id)
    assert saved.change_set.status == "proposed"


async def test_presses_on_applied_changes_say_so(session: AsyncSession, chat_id: int) -> None:
    user_id, change_set_id = await proposed(session, chat_id)
    sent = await shown(session, chat_id, change_set_id)
    accept = buttons(sent)["Accept"]
    for position in range(3):
        await decide_op(session, user_id, change_set_id, position, "accepted")
    await inbox.apply_change_set(session, user_id, change_set_id, now=datetime.now(UTC))
    handled = await pressing(session, chat_id, review_here(change_set_id), message_id=PROPOSALS_ID)
    assert handled.calls == (answered(review.CLOSED["applied"]),)
    handled = await pressing(session, chat_id, accept)
    assert handled.calls[0] == answered(review.CLOSED["applied"])
    assert handled.calls[1].params["text"] == review.CLOSED["applied"]


async def test_only_the_owners_change_sets_can_be_reviewed(
    session: AsyncSession, chat_id: int
) -> None:
    _, change_set_id = await proposed(session, chat_id)
    other_chat = chat_id + 1
    other, _ = await proposed(session, other_chat)
    handled = await pressing(
        session, other_chat, review_here(change_set_id), message_id=PROPOSALS_ID
    )
    assert other and handled.calls == (answered(review.GONE),)
    unlinked = chat_id + 2
    handled = await pressing(session, unlinked, review_here(change_set_id))
    assert handled.calls == (answered(),)
