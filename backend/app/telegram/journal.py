"""Turning Telegram updates into journal messages (FR-JRN-1, FR-JRN-3, FR-CAP-2).

`handle_update` is shared by the webhook and by `wj telegram poll`. It stores the raw update
first, so nothing is lost, then the journal message in it. Only private chats linked to a user
become journal messages, grouped into entries (`app/telegram/entries.py`). `/start <token>` from
a link to the bot links a chat (`app/telegram/linking.py`); any other unlinked chat is told how
to link itself. Commands aren't journaled: `/done` closes the open entry, `/skip` skips the
follow-up question, `/refresh` proposes profile changes from the facts since the last refresh
(`app/telegram/refresh.py`), `/pause` and `/resume` stop and restart the reminders
(`app/telegram/reminders.py`), and `/help` lists the commands (FR-CAP-7), as Telegram's command
menu does (`COMMANDS`). Button presses, and the message that names a new role, answer which
role an entry is about (`app/telegram/roles.py`). A message that answers a closed entry's
follow-up question joins that entry, and its Skip button skips the question
(`app/telegram/follow_up.py`). A reminder's buttons say there's nothing this week or ask for it
again tomorrow. Message text is never logged.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalMessage, JournalMessageEdit, TelegramLink, TelegramUpdate
from app.reminders import DEFAULT_SCHEDULE, Schedule
from app.telegram.api import BotCall, send
from app.telegram.entries import DEFAULT_TIMEOUT, close_open_entry, entry_for
from app.telegram.follow_up import SKIP, answered, awaiting, lapsed, press_skip, skip
from app.telegram.linking import link_chat, start_token
from app.telegram.refresh import refresh
from app.telegram.reminders import BUTTONS as REMINDER_BUTTONS
from app.telegram.reminders import pause, press_reminder, resume
from app.telegram.roles import Answer, name_role, naming_entry, press
from app.telegram.updates import CallbackQuery, Message, Update

LINK_HINT = (
    "This bot keeps a private work journal. To journal here, open the link to this bot from "
    "your Work Journal account."
)
DONE = "Closed this entry. Your next message starts a new one."
NOTHING_OPEN = "There's no open entry to close. Send me a note to start one."
UNKNOWN = "I don't know that command, so I didn't journal it."
COMMANDS: tuple[tuple[str, str], ...] = (
    ("done", "close the current entry now"),
    ("skip", "skip the question about the last entry"),
    ("refresh", "propose profile changes from your new facts"),
    ("pause", "stop reminders"),
    ("resume", "restart reminders"),
    ("help", "list the commands"),
)
"""The commands `/help` lists and Telegram's command menu shows, without their "/". Later tasks
add theirs. `/start` is left out: Telegram sends it when a chat opens the bot."""


def help_text(timeout: timedelta) -> str:
    """The list of commands (FR-CAP-7)."""
    minutes = round(timeout.total_seconds() / 60)
    unit = "minute" if minutes == 1 else "minutes"
    return (
        "Send me notes about your work, in as many messages as you like. A message "
        f"{minutes} {unit} after the last one starts a new entry.\n\n"
        + "\n".join(f"/{command} - {description}" for command, description in COMMANDS)
    )


@dataclass(frozen=True)
class Handled:
    """What came of an update: the Bot API calls that answer it, in order, and the entry whose
    jobs can run right away (FR-CAP-5), if there is one: an entry the update closed, so that the
    bot asks about it, one whose follow-up question it answered or skipped, so that the bot asks
    which role it's about, or one whose role it chose, so that its facts are extracted. A
    `/refresh` hands on its refresh instead, whose job can run right away too."""

    calls: tuple[BotCall, ...] = ()
    ready: uuid.UUID | None = None
    refresh: uuid.UUID | None = None

    @classmethod
    def of(cls, answer: Answer) -> Self:
        return cls(answer.calls, answer.ready)


async def handle_update(
    session: AsyncSession,
    payload: str,
    *,
    entry_timeout: timedelta = DEFAULT_TIMEOUT,
    app_url: str | None = None,
    reminders: Schedule = DEFAULT_SCHEDULE,
    now: datetime | None = None,
) -> Handled:
    """Store an update from Telegram, given as the JSON it arrived as, and the journal message in
    it. Returns the calls that answer it, and the entry or refresh whose jobs can run. An update
    Telegram sent before is ignored. `app_url` is the web app's address, for links to it, and
    `reminders` the schedule of an owner who hasn't chosen theirs. `now`, by default the time
    the update is handled, is when a button was pressed: a press carries no time of its own.

    Raises `pydantic.ValidationError` when the payload isn't an update.
    """
    update = Update.model_validate_json(payload)
    if not await _store_raw(session, update.update_id, payload):
        return Handled()
    if update.message is not None:
        return await _new_message(session, update.message, entry_timeout, app_url, reminders)
    if update.edited_message is not None:
        await _edit(session, update.edited_message)
    if update.callback_query is not None:
        pressed_at = datetime.now(UTC) if now is None else now
        return await _press(session, update.callback_query, pressed_at, reminders)
    return Handled()


async def purge_updates(session: AsyncSession, *, before: datetime) -> int:
    """Delete the raw updates received before `before` (FR-JRN-2). Returns how many there were."""
    result = await session.execute(
        delete(TelegramUpdate)
        .where(TelegramUpdate.received_at < before)
        .returning(TelegramUpdate.update_id)
    )
    return len(result.all())


async def _store_raw(session: AsyncSession, update_id: int, payload: str) -> bool:
    """Keep the raw update. False when it was kept before: Telegram resent it."""
    statement = (
        insert(TelegramUpdate)
        .values(update_id=update_id, payload=payload)
        .on_conflict_do_nothing(index_elements=[TelegramUpdate.update_id])
        .returning(TelegramUpdate.update_id)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


async def _chat_owner(session: AsyncSession, chat_id: int) -> uuid.UUID | None:
    statement = select(TelegramLink.user_id).where(TelegramLink.chat_id == chat_id)
    return await session.scalar(statement)


async def _new_message(
    session: AsyncSession,
    message: Message,
    entry_timeout: timedelta,
    app_url: str | None,
    reminders: Schedule,
) -> Handled:
    chat_id = message.chat.id
    if message.chat.type != "private":
        return Handled()
    if message.text is not None and (token := start_token(message.text)) is not None:
        return Handled((send(chat_id, await link_chat(session, chat_id, token)),))
    owner = await _chat_owner(session, chat_id)
    if owner is None:
        return Handled((send(chat_id, LINK_HINT),))
    body = message.body
    if body is None:
        return Handled()
    if body.startswith("/"):
        return await _command(
            session, owner, chat_id, body, message.sent_at, entry_timeout, app_url, reminders
        )
    if (naming := await naming_entry(session, owner)) is not None:
        return Handled.of(name_role(naming, chat_id, body))
    asked = await awaiting(session, owner)
    if asked is not None and not lapsed(asked, message.sent_at, entry_timeout):
        entry_id, handled = asked.id, Handled.of(answered(asked, chat_id))
    else:
        entry_id, closed = await entry_for(session, owner, message.sent_at, entry_timeout)
        handled = Handled(ready=closed)
    statement = (
        insert(JournalMessage)
        .values(
            user_id=owner,
            chat_id=message.chat.id,
            message_id=message.message_id,
            entry_id=entry_id,
            sender="owner",
            text=body,
            sent_at=message.sent_at,
        )
        .on_conflict_do_nothing(index_elements=[JournalMessage.chat_id, JournalMessage.message_id])
    )
    await session.execute(statement)
    return handled


async def _press(
    session: AsyncSession, query: CallbackQuery, now: datetime, reminders: Schedule
) -> Handled:
    """A button under one of the bot's messages, in a linked private chat, pressed at `now`."""
    message = query.message
    owner = None
    if message is not None and message.chat.type == "private":
        owner = await _chat_owner(session, message.chat.id)
    if owner is None:
        return Handled((BotCall("answerCallbackQuery", {"callback_query_id": query.id}),))
    if query.data == SKIP:
        return Handled.of(await press_skip(session, owner, query))
    if query.data in REMINDER_BUTTONS:
        answer = await press_reminder(session, owner, query, now=now, defaults=reminders)
        return Handled.of(answer)
    return Handled.of(await press(session, owner, query))


async def _command(
    session: AsyncSession,
    owner: uuid.UUID,
    chat_id: int,
    body: str,
    sent_at: datetime,
    entry_timeout: timedelta,
    app_url: str | None,
    reminders: Schedule,
) -> Handled:
    """The bot's answer to a command sent at `sent_at`. `/done` closes the open entry as of then,
    `/skip` skips the follow-up question that waits for an answer, `/refresh` starts a refresh,
    and `/pause` and `/resume` stop and restart the reminders. `/start` and `/help` get the list
    of commands, and so does an unknown command, with a note that it wasn't journaled."""
    command = body.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command == "/done":
        closed = await close_open_entry(session, owner, sent_at)
        return Handled((send(chat_id, DONE if closed else NOTHING_OPEN),), closed)
    if command == "/skip":
        return Handled.of(await skip(session, owner, chat_id))
    if command == "/refresh":
        answer = await refresh(session, owner, chat_id, app_url=app_url)
        return Handled(answer.calls, refresh=answer.refresh)
    if command == "/pause":
        return Handled.of(await pause(session, owner, chat_id))
    if command == "/resume":
        return Handled.of(await resume(session, owner, chat_id, defaults=reminders))
    if command in ("/start", "/help"):
        return Handled((send(chat_id, help_text(entry_timeout)),))
    return Handled((send(chat_id, f"{UNKNOWN}\n\n{help_text(entry_timeout)}"),))


async def _edit(session: AsyncSession, message: Message) -> None:
    """Save the new text of an edited journal message, and keep the one it replaces. An edit
    older than the stored text, delivered late, is ignored."""
    body = message.body
    if body is None:
        return
    row = await session.scalar(
        select(JournalMessage).where(
            JournalMessage.chat_id == message.chat.id,
            JournalMessage.message_id == message.message_id,
        )
    )
    if row is None or row.text == body:
        return
    edited_at = message.edited_at
    if row.edited_at is not None and edited_at <= row.edited_at:
        return
    session.add(
        JournalMessageEdit(
            user_id=row.user_id, message_id=row.id, text=row.text, replaced_at=edited_at
        )
    )
    row.text = body
    row.edited_at = edited_at
