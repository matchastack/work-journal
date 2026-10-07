"""What happens to a journal entry once it closes (FR-CAP-5, FR-CAP-8, FR-EXT-1).

The `extract_entry` job (`app/jobs.py`) processes each closed entry once:

1. Triage (`app/triage.py`) labels the entry's text: an entry that isn't about work gives no
   facts.
2. Extraction (`app/extraction.py`) finds the facts in an entry about work. They're checked
   against the entry's text and saved encrypted, with the entry's ID.
3. The reply lists the saved facts, up to three, with a link to the entry in the web app.

The entry is marked processed in the transaction that saves its facts, and the job sends the
reply before that transaction commits. So a failure anywhere rolls everything back for the retry,
and an entry is processed once. The reply quotes the facts as saved: nothing in it is newly
generated. Neither the entry's text nor its facts are logged.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntry, JournalMessage, TelegramLink
from app.db.profiles import latest_version
from app.db.store import save_facts
from app.extraction import extract_facts
from app.llm.client import LLMClient
from app.schema.fact import Fact
from app.schema.profile import Basics, Profile
from app.triage import Label, triage

SHOWN_FACTS = 3
"""The reply lists at most this many facts, then says how many more were saved."""
NO_PROFILE = Profile(basics=Basics(name="No profile loaded"))
"""Used when the owner hasn't loaded a profile yet: facts then link to no role or project.
Extraction sends only the profile's roles, projects and skills, never its basics."""


@dataclass(frozen=True)
class Processed:
    """A processed entry, and what the bot tells its owner."""

    chat_id: int | None
    """The owner's linked chat, if there is one."""
    reply: str


async def process_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    client: LLMClient,
    *,
    now: datetime,
    app_url: str,
) -> Processed | None:
    """Triage the closed entry, save the facts in it, and mark it processed. Returns the reply,
    or None when there's nothing to say: the entry is open, already processed or failed, or
    empty."""
    entry = await session.scalar(
        select(JournalEntry).where(JournalEntry.id == entry_id).with_for_update()
    )
    if entry is None or entry.closed_at is None:
        return None
    if entry.processed_at is not None or entry.failed_at is not None:
        return None
    note = await entry_text(session, entry_id)
    if not note:
        entry.triage, entry.processed_at = "other", now
        return None
    label = await asyncio.to_thread(triage, note, client)
    facts: tuple[Fact, ...] = ()
    left_out = 0
    if label == "work":
        version = await latest_version(session, entry.user_id)
        extraction = await asyncio.to_thread(
            extract_facts,
            note,
            version.profile if version else NO_PROFILE,
            client,
            written=entry.opened_at.date(),
            entry_id=str(entry.id),
        )
        await save_facts(session, entry.user_id, extraction.facts)
        facts, left_out = extraction.facts, len(extraction.dropped)
    entry.triage, entry.processed_at = label, now
    chat_id = await chat_of(session, entry.user_id)
    return Processed(chat_id, reply_text(label, facts, left_out, entry_link(app_url, entry_id)))


async def entry_text(session: AsyncSession, entry_id: uuid.UUID) -> str:
    """The entry's messages from its owner, oldest first and as last edited, one paragraph
    each."""
    texts = await session.scalars(
        select(JournalMessage.text)
        .where(JournalMessage.entry_id == entry_id, JournalMessage.sender == "owner")
        .order_by(JournalMessage.sent_at, JournalMessage.id)
    )
    return "\n\n".join(text.strip() for text in texts if text.strip())


async def chat_of(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    return await session.scalar(select(TelegramLink.chat_id).where(TelegramLink.user_id == user_id))


def entry_link(app_url: str, entry_id: uuid.UUID) -> str:
    """Where the entry is in the web app's journal (T-041)."""
    return f"{app_url}/journal#{entry_id}"


def reply_text(label: Label, facts: Sequence[Fact], left_out: int, link: str) -> str:
    """The bot's reply to a processed entry: the facts it saved, as saved (FR-CAP-5)."""
    if label == "other":
        return "Noted. This doesn't look like work, so I saved no facts from it."
    if facts:
        lines = [f"Saved {_count(len(facts), 'fact')} from this entry:"]
        lines += [f"- {fact.statement}" for fact in facts[:SHOWN_FACTS]]
        if len(facts) > SHOWN_FACTS:
            lines.append(f"...and {len(facts) - SHOWN_FACTS} more.")
    else:
        lines = ["I didn't find a fact to save in this entry."]
    if left_out:
        lines.append(f"I left out {_count(left_out, 'fact')} with numbers your note doesn't give.")
    lines += ["", "See them in your journal:" if facts else "It's in your journal:", link]
    return "\n".join(lines)


def failure_text(reason: str, link: str) -> str:
    """What the bot says when it finally can't process an entry."""
    return (
        f"I couldn't read this entry because {reason}, so I saved no facts from it. "
        f"It's still in your journal:\n{link}"
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
