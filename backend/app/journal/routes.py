"""`/api/journal`: the signed-in owner's journal (T-041)."""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import StringConstraints

from app.auth.routes import NO_STORE
from app.auth.sessions import CurrentUser
from app.deps import Db
from app.journal.document import Journal, JournalFact, JournalMessageView, load_journal
from app.journal.editing import (
    FactEdit,
    FactsCited,
    NotFound,
    OnlyMessage,
    UnknownLink,
    delete_entry,
    delete_fact,
    delete_message,
    edit_fact,
    edit_message,
)
from app.journal.export import JournalExport, export_journal, to_markdown
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


@router.put("/facts/{fact_id}")
async def change_fact(
    fact_id: str, body: FactEdit, user: CurrentUser, db: Db, response: Response
) -> JournalFact:
    """Change a fact's fields (FR-EXT-3). Its role and project must be in the profile, unless it
    already links to them (422)."""
    response.headers.update(NO_STORE)
    with _errors():
        fact = await edit_fact(db, user.id, fact_id, body)
    await db.commit()
    return fact


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_fact(fact_id: str, user: CurrentUser, db: Db, confirm: bool = False) -> None:
    """Delete a fact (FR-EXT-3). When the profile cites it, it takes `confirm=true` (409
    without)."""
    with _errors():
        await delete_fact(db, user.id, fact_id, confirm=confirm)
    await db.commit()


@router.get("/export.json")
async def export_json(user: CurrentUser, db: Db, response: Response) -> JournalExport:
    """Every entry and every fact, as a JSON file (FR-JRN-7)."""
    now = datetime.now(UTC)
    response.headers.update(NO_STORE | _attachment(f"work-journal-{now:%Y-%m-%d}.json"))
    return await export_journal(db, user.id, now=now)


@router.get(
    "/export.md",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}},
)
async def export_markdown(user: CurrentUser, db: Db, tz: str = "UTC") -> Response:
    """Every entry and every fact, as a Markdown file (FR-JRN-7), with times in the time zone
    `tz`, such as `Asia/Singapore`."""
    zone = _zone(tz)
    now = datetime.now(UTC)
    export = await export_journal(db, user.id, now=now)
    name = f"work-journal-{now.astimezone(zone):%Y-%m-%d}.md"
    return Response(
        to_markdown(export, zone), media_type="text/markdown", headers=NO_STORE | _attachment(name)
    )


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"there's no time zone {name!r}"
        ) from None


@contextmanager
def _errors() -> Generator[None]:
    """Answer a correction that can't be made with its HTTP status."""
    try:
        yield
    except NotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except (OnlyMessage, FactsCited) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except UnknownLink as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
