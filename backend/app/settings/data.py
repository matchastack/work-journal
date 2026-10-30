"""All of a user's data, to export or to delete (FR-SET-6, NFR-PRIV-3).

Every table whose rows belong to a user has a `user_id` (`app/db/models.py`, constraint C4), so
both go through every such table, and a table added later is covered without a change here.

- **The export** holds each of the user's rows, column by column, as JSON: journal and fact text
  decrypted, dates in ISO 8601, and files, such as resume PDFs, in base64. It leaves out the
  sign-in sessions and the one-time Telegram link tokens (`NOT_EXPORTED`): they're keys, not
  data. It adds the raw Telegram updates from the user's chat, which belong to no user until
  they're read, and are kept for 7 days (FR-JRN-2).
- **Deleting** the user's row deletes every row of theirs, since each `user_id` cascades. That
  includes their sessions, so they're signed out everywhere. The raw updates from their chat go
  too, and so do the jobs queued for their journal entries and refreshes, which would otherwise
  run for nothing. A job already running finds its entry or refresh gone, and stops.

Neither logs any content.
"""

import base64
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import LargeBinary, RowMapping, Table, bindparam, delete, select, text, type_coerce
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from app.db.crypto import DecryptionError, key_ring
from app.db.models import (
    NOT_PER_USER,
    Base,
    JournalEntry,
    Refresh,
    TelegramLink,
    TelegramUpdate,
    User,
)
from app.telegram.updates import Update

FORMAT = "work-journal-export"
VERSION = 1
NOT_EXPORTED = frozenset({"sessions", "telegram_link_tokens"})
"""The user's tables the export leaves out: sign-in sessions and one-time link tokens, which are
keys rather than data."""
UPDATE_CONTEXT = "telegram_updates.payload"
"""The column that encrypts a raw update (`TelegramUpdate.payload`)."""
JSON_TEXT = frozenset({("facts", "data")})
"""Text columns that hold JSON, which the export gives as JSON rather than as a string."""
DELETE_JOBS = text(
    """
    DELETE FROM procrastinate_jobs
    WHERE status <> 'doing'
        AND (args->>'entry_id' = ANY(:entries) OR args->>'refresh_id' = ANY(:refreshes))
    """
).bindparams(bindparam("entries", type_=ARRAY(String)), bindparam("refreshes", type_=ARRAY(String)))
"""Jobs for the given journal entries and refreshes, except any running now, which Procrastinate
still has to finish. Their arguments are IDs (`app/jobs.py`)."""


async def export_data(session: AsyncSession, user: User, *, now: datetime) -> dict[str, Any]:
    """Everything stored for `user`, as of `now`, as JSON-ready values."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in user_tables():
        if table.name in NOT_EXPORTED:
            continue
        query = select(table).where(table.c.user_id == user.id).order_by(*table.primary_key.columns)
        rows = await session.execute(query)
        tables[table.name] = [_row(table, row) for row in rows.mappings()]
    chat_id = await _chat(session, user.id)
    tables[TelegramUpdate.__tablename__] = [
        {"update_id": update_id, "received_at": _value(received_at), "payload": json.loads(payload)}
        for update_id, received_at, payload in await _updates_from(session, chat_id)
    ]
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": now.isoformat(),
        "account": {
            "id": str(user.id),
            "github_id": user.github_id,
            "github_login": user.github_login,
            "created_at": _value(user.created_at),
        },
        "tables": tables,
    }


async def delete_all_data(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete the user and everything stored for them. The caller commits."""
    entries = await session.scalars(select(JournalEntry.id).where(JournalEntry.user_id == user_id))
    refreshes = await session.scalars(select(Refresh.id).where(Refresh.user_id == user_id))
    await session.execute(
        DELETE_JOBS,
        {"entries": [str(entry) for entry in entries], "refreshes": [str(r) for r in refreshes]},
    )
    updates = [row[0] for row in await _updates_from(session, await _chat(session, user_id))]
    if updates:
        await session.execute(delete(TelegramUpdate).where(TelegramUpdate.update_id.in_(updates)))
    await session.execute(delete(User).where(User.id == user_id))


def user_tables() -> list[Table]:
    """Every table whose rows belong to a user, parents first."""
    return [table for table in Base.metadata.sorted_tables if table.name not in NOT_PER_USER]


async def _chat(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    return await session.scalar(select(TelegramLink.chat_id).where(TelegramLink.user_id == user_id))


async def _updates_from(
    session: AsyncSession, chat_id: int | None
) -> list[tuple[int, datetime, str]]:
    """The raw updates kept from the chat `chat_id`, oldest first: none without a chat.

    An update the keys in use can't decrypt can't be told apart, so it's passed over: it's
    deleted with the others after 7 days."""
    if chat_id is None:
        return []
    ring = key_ring()
    rows = await session.execute(
        select(
            TelegramUpdate.update_id,
            TelegramUpdate.received_at,
            type_coerce(TelegramUpdate.payload, LargeBinary),
        ).order_by(TelegramUpdate.update_id)
    )
    found: list[tuple[int, datetime, str]] = []
    for update_id, received_at, blob in rows:
        try:
            payload = ring.decrypt(bytes(blob), UPDATE_CONTEXT)
        except DecryptionError:
            continue
        if _chat_of(payload) == chat_id:
            found.append((update_id, received_at, payload))
    return found


def _chat_of(payload: str) -> int | None:
    """The chat an update came from: a message's, an edited message's, or that of the message
    whose button was pressed."""
    try:
        update = Update.model_validate_json(payload)
    except ValidationError:
        return None
    pressed = update.callback_query.message if update.callback_query is not None else None
    for message in (update.message, update.edited_message, pressed):
        if message is not None:
            return message.chat.id
    return None


def _row(table: Table, row: RowMapping) -> dict[str, Any]:
    """A row as JSON, without its `user_id`, which is the account's."""
    values: dict[str, Any] = {}
    for column in table.columns:
        if column.name == "user_id":
            continue
        value = row[column.name]
        if (table.name, column.name) in JSON_TEXT and isinstance(value, str):
            values[column.name] = json.loads(value)
        else:
            values[column.name] = _value(value)
    return values


def _value(value: object) -> object:
    """A column's value as JSON: as it is, or as text for dates, IDs, exact decimals and
    files."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value
