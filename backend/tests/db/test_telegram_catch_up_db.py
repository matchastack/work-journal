"""The catch-up interview in Telegram (FR-REM-4): starting it on the first link and on
`/catchup`, answers that become entries, Skip, Stop for now and `/done`, lapsed questions, and
the `catch_up_question` job that writes and sends each question."""

import json
import random
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.jobs as job_module
from app.catch_up import FIXED_QUESTIONS, InterviewReply, Turn
from app.config import Settings, get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import CatchUp, JournalEntry, JournalMessage, TelegramLink, User
from app.db.profiles import save_version
from app.follow_up import FollowUpReply
from app.jobs import database, run_catch_up
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import CallLog, MemoryCallLog
from app.schema.profile import Profile
from app.telegram.api import BotCall
from app.telegram.catch_up import (
    FINISHED,
    INTRO,
    KEYBOARD,
    NO_ANSWERS,
    NOT_WAITING,
    PAUSED,
    RESUMED,
    SKIP_QUESTION,
    SKIPPED,
    STOP,
    THINKING,
    next_message,
    record_question,
    turns_of,
)
from app.telegram.follow_up import follow_up
from app.telegram.journal import DONE_AND_PAUSED, Handled, handle_update
from app.telegram.linking import LINKED, new_link_token
from app.telegram.processing import process_entry
from app.triage import TriageReply

pytestmark = pytest.mark.anyio

QUESTION_ID = 920
"""The bot's catch-up question, as a message in the owner's chat."""
SENT = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
"""When the owner's messages in these tests are sent."""
TIMEOUT = timedelta(minutes=30)
ASKED = "What did the platform team work on after you joined?"
ANSWER = "Moved to the platform team in May and took over the deploy pipeline."
PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
_random = random.SystemRandom()


@pytest.fixture(autouse=True)
def key_ring() -> Iterator[None]:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


@pytest.fixture
def chat_id() -> int:
    return _random.randrange(10**9, 10**12)


def llm(*replies: Any) -> tuple[LLMClient, FakeMessages]:
    fake = FakeMessages(*replies)
    settings = Settings(llm_model_light="light-model", llm_model_standard="standard-model")
    return LLMClient(settings, fake, MemoryCallLog()), fake


async def linked_user(session: AsyncSession, chat_id: int) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    session.add(TelegramLink(user_id=user.id, chat_id=chat_id))
    await session.flush()
    return user.id


async def asking_interview(
    session: AsyncSession,
    chat_id: int,
    *,
    asked: datetime = SENT - timedelta(minutes=5),
    turns: tuple[Turn, ...] = (),
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user linked to `chat_id`, whose interview asked `ASKED` at `asked`, after `turns`.
    Returns both IDs."""
    user_id = await linked_user(session, chat_id)
    interview = CatchUp(
        user_id=user_id,
        status="asking",
        transcript=json.dumps([turn.model_dump() for turn in turns]),
        question=ASKED,
        question_id=QUESTION_ID,
        asked_at=asked,
    )
    session.add(interview)
    await session.flush()
    return user_id, interview.id


async def interview_of(session: AsyncSession, catch_up_id: uuid.UUID) -> CatchUp:
    interview = await session.get(CatchUp, catch_up_id, populate_existing=True)
    assert interview is not None
    return interview


def message(chat_id: int, text: str, *, sent: datetime = SENT, message_id: int = 60) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "message": {
                "message_id": message_id,
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat": {"id": chat_id, "type": "private"},
                "date": int(sent.timestamp()),
                "text": text,
            },
        }
    )


def press(chat_id: int, data: str, message_id: int = QUESTION_ID) -> str:
    return json.dumps(
        {
            "update_id": _random.randrange(10**9, 10**12),
            "callback_query": {
                "id": "6493cgexetc434c3e0",
                "from": {"id": chat_id, "is_bot": False, "first_name": "Casey"},
                "chat_instance": "-1234",
                "message": {
                    "message_id": message_id,
                    "from": {"id": 99, "is_bot": True, "first_name": "Journal"},
                    "chat": {"id": chat_id, "type": "private"},
                    "date": int(SENT.timestamp()) - 300,
                    "text": ASKED,
                },
                "data": data,
            },
        }
    )


def answered_press(notice: str | None = None) -> BotCall:
    params: dict[str, Any] = {"callback_query_id": "6493cgexetc434c3e0"}
    if notice is not None:
        params["text"] = notice
    return BotCall("answerCallbackQuery", params)


def sent(chat_id: int, text: str) -> BotCall:
    return BotCall("sendMessage", {"chat_id": chat_id, "text": text})


def without_buttons(chat_id: int) -> BotCall:
    return BotCall("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": QUESTION_ID})


async def handle(session: AsyncSession, payload: str) -> Handled:
    return await handle_update(session, payload, entry_timeout=TIMEOUT)


# --- Starting -------------------------------------------------------------------------------------


async def test_the_first_link_starts_the_interview(session: AsyncSession, chat_id: int) -> None:
    user = User()
    session.add(user)
    await session.flush()
    token, _ = await new_link_token(session, user.id)
    handled = await handle(session, message(chat_id, f"/start {token}"))
    [interview] = await session.scalars(select(CatchUp).where(CatchUp.user_id == user.id))
    assert handled == Handled((sent(chat_id, LINKED), sent(chat_id, INTRO)), catch_up=interview.id)
    assert (interview.status, turns_of(interview), interview.question) == ("thinking", [], None)


async def test_linking_again_doesnt_start_another(session: AsyncSession, chat_id: int) -> None:
    user_id, interview_id = await asking_interview(session, chat_id)
    interview = await interview_of(session, interview_id)
    interview.status, interview.finished_at = "finished", SENT
    await session.execute(delete(TelegramLink).where(TelegramLink.user_id == user_id))
    token, _ = await new_link_token(session, user_id)
    handled = await handle(session, message(chat_id, f"/start {token}"))
    assert handled == Handled((sent(chat_id, LINKED),))


async def test_catchup_starts_an_interview_after_closing_the_open_entry(
    session: AsyncSession, chat_id: int
) -> None:
    user_id = await linked_user(session, chat_id)
    await handle(session, message(chat_id, "half a note", sent=SENT - timedelta(minutes=1)))
    handled = await handle(session, message(chat_id, "/catchup", message_id=61))
    [interview] = await session.scalars(select(CatchUp).where(CatchUp.user_id == user_id))
    [entry] = await session.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id))
    assert handled == Handled((sent(chat_id, INTRO),), entry.id, catch_up=interview.id)
    assert (entry.closed_at, entry.closed_by) == (SENT, "done")


async def test_catchup_while_thinking_hands_the_question_on_again(
    session: AsyncSession, chat_id: int
) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    (await interview_of(session, interview_id)).status = "thinking"
    handled = await handle(session, message(chat_id, "/catchup"))
    assert handled == Handled((sent(chat_id, THINKING),), catch_up=interview_id)


async def test_catchup_carries_on_with_a_paused_interview(
    session: AsyncSession, chat_id: int
) -> None:
    """The waiting question is asked again, by the job, so that its new message is kept."""
    _, interview_id = await asking_interview(session, chat_id)
    (await interview_of(session, interview_id)).status = "paused"
    handled = await handle(session, message(chat_id, "/catchup"))
    assert handled == Handled(
        (sent(chat_id, RESUMED), without_buttons(chat_id)), catch_up=interview_id
    )
    interview = await interview_of(session, interview_id)
    assert (interview.status, interview.question, interview.question_id) == (
        "thinking",
        ASKED,
        None,
    )


# --- Answering ------------------------------------------------------------------------------------


async def test_an_answer_becomes_an_entry_of_its_own_with_the_question(
    session: AsyncSession, chat_id: int
) -> None:
    """The entry closes at once, so its jobs ask which role it's about, and the next question
    follows."""
    user_id, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, message(chat_id, ANSWER))
    [entry] = await session.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id))
    assert handled == Handled((without_buttons(chat_id),), entry.id, catch_up=interview_id)
    assert (entry.closed_at, entry.closed_by, entry.question) == (SENT, "catchup", ASKED)
    texts = await session.scalars(
        select(JournalMessage.text).where(JournalMessage.entry_id == entry.id)
    )
    assert list(texts) == [ANSWER]
    interview = await interview_of(session, interview_id)
    assert turns_of(interview) == [Turn(question=ASKED, answer=ANSWER)]
    assert (interview.status, interview.question, interview.question_id) == (
        "thinking",
        None,
        None,
    )


async def test_a_message_after_the_question_lapsed_is_a_note(
    session: AsyncSession, chat_id: int
) -> None:
    """The interview pauses, keeping its question for `/catchup`."""
    user_id, interview_id = await asking_interview(session, chat_id, asked=SENT - TIMEOUT)
    handled = await handle(session, message(chat_id, "an ordinary note"))
    assert handled == Handled((without_buttons(chat_id),))
    [entry] = await session.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id))
    assert (entry.closed_at, entry.question) == (None, None)
    interview = await interview_of(session, interview_id)
    assert (interview.status, interview.question, turns_of(interview)) == ("paused", ASKED, [])


async def test_a_follow_up_answer_comes_before_the_interview(
    session: AsyncSession, chat_id: int
) -> None:
    user_id, interview_id = await asking_interview(session, chat_id)
    entry = JournalEntry(
        user_id=user_id,
        opened_at=SENT - timedelta(minutes=20),
        last_message_at=SENT - timedelta(minutes=10),
        closed_at=SENT - timedelta(minutes=10),
        closed_by="done",
        follow_up_at=SENT - timedelta(minutes=8),
        awaiting_follow_up=True,
    )
    session.add(entry)
    await session.flush()
    handled = await handle(session, message(chat_id, "about 12 minutes"))
    assert handled.ready == entry.id
    assert handled.catch_up is None
    assert (await interview_of(session, interview_id)).status == "asking"


# --- Skip, Stop for now and /done -----------------------------------------------------------------


async def test_skip_moves_on_to_the_next_question(session: AsyncSession, chat_id: int) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, press(chat_id, SKIP_QUESTION))
    assert handled == Handled(
        (answered_press(), sent(chat_id, SKIPPED), without_buttons(chat_id)),
        catch_up=interview_id,
    )
    interview = await interview_of(session, interview_id)
    assert (interview.status, turns_of(interview)) == ("thinking", [Turn(question=ASKED)])


async def test_the_skip_command_skips_the_interviews_question(
    session: AsyncSession, chat_id: int
) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, message(chat_id, "/skip"))
    assert handled == Handled(
        (sent(chat_id, SKIPPED), without_buttons(chat_id)), catch_up=interview_id
    )


async def test_stop_for_now_pauses_the_interview(session: AsyncSession, chat_id: int) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, press(chat_id, STOP))
    assert handled == Handled((answered_press(), without_buttons(chat_id), sent(chat_id, PAUSED)))
    interview = await interview_of(session, interview_id)
    assert (interview.status, interview.question, interview.question_id) == (
        "paused",
        ASKED,
        None,
    )


async def test_done_pauses_the_interview(session: AsyncSession, chat_id: int) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, message(chat_id, "/done"))
    assert handled == Handled((sent(chat_id, PAUSED), without_buttons(chat_id)))
    assert (await interview_of(session, interview_id)).status == "paused"


async def test_done_closes_the_open_entry_too(session: AsyncSession, chat_id: int) -> None:
    user_id, interview_id = await asking_interview(session, chat_id, asked=SENT - TIMEOUT * 2)
    await handle(session, message(chat_id, "a note", sent=SENT - TIMEOUT))
    (await interview_of(session, interview_id)).status = "thinking"
    handled = await handle(session, message(chat_id, "/done", message_id=61))
    [entry] = await session.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id))
    assert handled == Handled((sent(chat_id, DONE_AND_PAUSED),), entry.id)
    assert (await interview_of(session, interview_id)).status == "paused"


async def test_a_button_on_an_old_question_changes_nothing(
    session: AsyncSession, chat_id: int
) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, press(chat_id, STOP, message_id=QUESTION_ID - 1))
    assert handled == Handled((answered_press(NOT_WAITING),))
    assert (await interview_of(session, interview_id)).status == "asking"


# --- Writing the next question --------------------------------------------------------------------


async def thinking_interview(
    session: AsyncSession, chat_id: int, *turns: Turn, profile: Profile | None = PROFILE
) -> uuid.UUID:
    user_id = await linked_user(session, chat_id)
    if profile is not None:
        await save_version(session, user_id, profile, author="owner")
    interview = CatchUp(
        user_id=user_id,
        transcript=json.dumps([turn.model_dump() for turn in turns]),
    )
    session.add(interview)
    await session.flush()
    return interview.id


async def test_the_next_question_comes_from_the_standard_tier_with_buttons(
    session: AsyncSession, chat_id: int
) -> None:
    interview_id = await thinking_interview(session, chat_id, Turn(question="What changed?"))
    client, fake = llm(reply(InterviewReply(done=False, question=ASKED)))
    question = await next_message(session, interview_id, client, now=SENT)
    assert question is not None
    assert (question.chat_id, question.text, question.options, question.waits) == (
        chat_id,
        ASKED,
        {"reply_markup": KEYBOARD},
        True,
    )
    assert fake.requests[0]["model"] == "standard-model"
    assert PROFILE.work[0].id in fake.requests[0]["system"][1]["text"]
    await record_question(session, interview_id, QUESTION_ID)
    interview = await interview_of(session, interview_id)
    assert (interview.status, interview.question, interview.question_id, interview.asked_at) == (
        "asking",
        ASKED,
        QUESTION_ID,
        SENT,
    )


async def test_a_waiting_question_is_asked_again_without_a_call(
    session: AsyncSession, chat_id: int
) -> None:
    interview_id = await thinking_interview(session, chat_id)
    (await interview_of(session, interview_id)).question = ASKED
    client, fake = llm()
    question = await next_message(session, interview_id, client, now=SENT)
    assert question is not None
    assert question.text == ASKED
    assert fake.requests == []


async def test_without_a_profile_the_interview_still_asks(
    session: AsyncSession, chat_id: int
) -> None:
    interview_id = await thinking_interview(session, chat_id, profile=None)
    client, _ = llm(reply(InterviewReply(done=True)))
    question = await next_message(session, interview_id, client, now=SENT)
    assert question is not None
    assert question.text == FIXED_QUESTIONS[0], "never ends before its first question"


async def test_the_end_offers_a_refresh(session: AsyncSession, chat_id: int) -> None:
    turn = Turn(question="What changed?", answer=ANSWER)
    interview_id = await thinking_interview(session, chat_id, turn)
    client, _ = llm(reply(InterviewReply(done=True)))
    question = await next_message(session, interview_id, client, now=SENT)
    assert question is not None
    assert (question.text, question.options, question.waits) == (FINISHED, {}, False)
    interview = await interview_of(session, interview_id)
    assert (interview.status, interview.finished_at) == ("finished", SENT)


async def test_an_interview_without_answers_ends_without_a_refresh(
    session: AsyncSession, chat_id: int
) -> None:
    interview_id = await thinking_interview(session, chat_id, Turn(question="What changed?"))
    client, _ = llm(reply(InterviewReply(done=True)))
    question = await next_message(session, interview_id, client, now=SENT)
    assert question is not None
    assert question.text == NO_ANSWERS


async def test_catchup_after_the_end_starts_a_new_interview(
    session: AsyncSession, chat_id: int
) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    interview = await interview_of(session, interview_id)
    interview.status, interview.finished_at = "finished", SENT
    handled = await handle(session, message(chat_id, "/catchup"))
    assert handled.calls == (sent(chat_id, INTRO),)
    assert handled.catch_up not in (None, interview_id)


async def test_a_question_that_cant_be_sent_pauses_the_interview(
    session: AsyncSession, chat_id: int
) -> None:
    interview_id = await thinking_interview(session, chat_id)
    client, _ = llm(reply(InterviewReply(done=False, question=ASKED)))
    await next_message(session, interview_id, client, now=SENT)
    await record_question(session, interview_id, None)
    assert (await interview_of(session, interview_id)).status == "paused"


async def test_only_an_interview_writing_its_question_asks(
    session: AsyncSession, chat_id: int
) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    client, _ = llm()
    assert await next_message(session, interview_id, client, now=SENT) is None


# --- Answers' entries -----------------------------------------------------------------------------


async def test_no_follow_up_question_while_the_interview_runs(
    session: AsyncSession, chat_id: int
) -> None:
    """Neither about an answer, nor about another entry, so the owner's next message is never
    ambiguous."""
    user_id, _ = await asking_interview(session, chat_id)
    entry = JournalEntry(
        user_id=user_id,
        opened_at=SENT - timedelta(minutes=3),
        last_message_at=SENT - timedelta(minutes=2),
        closed_at=SENT - timedelta(minutes=1),
        closed_by="done",
    )
    session.add(entry)
    await session.flush()
    session.add(
        JournalMessage(
            user_id=user_id,
            chat_id=chat_id,
            message_id=1,
            entry_id=entry.id,
            sender="owner",
            text="sped up the search page a lot",
            sent_at=SENT - timedelta(minutes=2),
        )
    )
    await session.flush()
    client, fake = llm(reply(FollowUpReply(missing="metrics", question="How much faster?")))
    assert await follow_up(session, entry.id, client, now=SENT, timeout=TIMEOUT) is None
    assert fake.requests == []


async def test_an_answer_gets_no_follow_up_question(session: AsyncSession, chat_id: int) -> None:
    _, interview_id = await asking_interview(session, chat_id)
    handled = await handle(session, message(chat_id, ANSWER))
    assert handled.ready is not None
    interview = await interview_of(session, interview_id)
    interview.status, interview.finished_at = "finished", SENT
    client, fake = llm()
    assert await follow_up(session, handled.ready, client, now=SENT, timeout=TIMEOUT) is None
    assert fake.requests == []


async def test_an_answer_is_read_alone(session: AsyncSession, chat_id: int) -> None:
    """The bot's question is never evidence: nothing it wrote can become a fact (FR-EXT-2)."""
    await asking_interview(session, chat_id)
    handled = await handle(session, message(chat_id, ANSWER))
    assert handled.ready is not None
    entry = await session.get(JournalEntry, handled.ready)
    assert entry is not None
    entry.role_id = "northwind"
    client, fake = llm(reply(TriageReply(label="other")))
    await process_entry(session, entry.id, client, now=SENT, app_url="https://example.com")
    assert fake.requests[0]["messages"][0]["content"] == f"<note>\n{ANSWER}\n</note>"


# --- The catch_up_question job --------------------------------------------------------------------


class Telegram:
    """A stand-in for the Bot API that keeps each message sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": QUESTION_ID}})


@pytest.fixture
def telegram(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Telegram]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    get_settings.cache_clear()
    bot = Telegram()
    transport = httpx2.MockTransport(bot.handle)
    monkeypatch.setattr(
        job_module, "_telegram_http", lambda: httpx2.AsyncClient(transport=transport)
    )
    yield bot
    get_settings.cache_clear()


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeMessages]:
    def script(*replies: Any, settings: Settings | None = None) -> FakeMessages:
        fake = FakeMessages(*replies)
        used = settings or Settings(llm_model_standard="standard-model")

        def client(log: CallLog) -> LLMClient:
            return LLMClient(used, fake, log)

        monkeypatch.setattr(job_module, "_llm_client", client)
        return fake

    return script


@pytest.fixture
async def committed(telegram: Telegram, chat_id: int) -> AsyncIterator[uuid.UUID]:
    """A thinking interview, committed, as the job sees it. Its user is deleted afterwards, with
    all their rows."""
    async with database() as session:
        interview_id = await thinking_interview(session, chat_id)
        user_id = (await interview_of(session, interview_id)).user_id
    yield interview_id
    async with database() as session:
        await session.execute(delete(User).where(User.id == user_id))


def never(error: BaseException) -> bool:
    return False


async def test_the_job_asks_and_keeps_the_questions_message(
    committed: uuid.UUID, chat_id: int, telegram: Telegram, claude: Callable[..., FakeMessages]
) -> None:
    claude(reply(InterviewReply(done=False, question=ASKED)))
    await run_catch_up(committed, last_attempt=never)
    assert telegram.sent == [{"chat_id": chat_id, "text": ASKED, "reply_markup": KEYBOARD}]
    async with database() as session:
        interview = await interview_of(session, committed)
        assert (interview.status, interview.question_id) == ("asking", QUESTION_ID)


async def test_missing_model_settings_pause_the_interview_and_tell_the_owner(
    committed: uuid.UUID, chat_id: int, telegram: Telegram, claude: Callable[..., FakeMessages]
) -> None:
    claude(settings=Settings())
    await run_catch_up(committed, last_attempt=never)
    assert telegram.sent == [
        {
            "chat_id": chat_id,
            "text": "I couldn't think of the next catch-up question because the model settings "
            "are missing. Send /catchup to try again.",
        }
    ]
    async with database() as session:
        assert (await interview_of(session, committed)).status == "paused"
