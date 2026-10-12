"""`/api/journal`: the signed-in owner's journal (T-041)."""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import StringConstraints

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import Db
from app.journal.document import Journal, JournalMessageView, load_journal
from app.journal.editing import (
    FactsCited,
    NotFound,
    OnlyMessage,
    delete_entry,
    delete_message,
    edit_message,
)
from app.schema.common import Model

router = APIRouter(prefix="/api/journal", tags=["journal"])

MESSAGE_LIMIT = 4096
"""The longest text a message may have, in characters: as long as a Telegram message."""


class MessageText(Model):
    text: Annotated[str, StringConstraints(min_length=1, max_length=MESSAGE_LIMIT)]


@router.get("")
async def journal(user: CurrentUser, db: Db, response: Response) -> Journal:
    """Every entry, oldest first, with its messages and facts (FR-JRN-5)."""
    response.headers.update(NO_STORE)
    return await load_journal(db, user.id)


@router.patch("/messages/{message_id}")
async def change_message(
    message_id: uuid.UUID, body: MessageText, user: CurrentUser, db: Db, response: Response
) -> JournalMessageView:
    """Replace a message's text, keeping the text it replaces (FR-JRN-6, FR-JRN-3)."""
    response.headers.update(NO_STORE)
    with _errors():
        message = await edit_message(db, user.id, message_id, body.text, now=datetime.now(UTC))
    await db.commit()
    return message


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_message(message_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    """Delete a message. An entry's only message can't be: delete the entry (409)."""
    with _errors():
        await delete_message(db, user.id, message_id)
    await db.commit()


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_entry(
    entry_id: uuid.UUID, user: CurrentUser, db: Db, confirm: bool = False
) -> None:
    """Delete an entry, its messages and its facts (FR-JRN-6). When the profile cites one of the
    facts, it takes `confirm=true` (409 without)."""
    with _errors():
        await delete_entry(db, user.id, entry_id, confirm=confirm)
    await db.commit()


@contextmanager
def _errors() -> Generator[None]:
    """Answer a correction that can't be made with its HTTP status."""
    try:
        yield
    except NotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except (OnlyMessage, FactsCited) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
