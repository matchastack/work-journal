"""Job postings sent to the bot for tailoring (FR-CAP-9, T-078).

`/tailor` starts a posting. Text sent with it, and the owner's messages after it, join the
posting, kept encrypted on the chat's link (`TelegramLink.posting`), until `/done` sends it for
tailoring: it becomes a Tailor page draft, as a posting pasted there does (`create_draft`), marked
as sent to the bot, so that its jobs say in the chat when it's tailored or fails (`app/jobs.py`).
A posting's messages never become journal messages, and while it's open, they don't answer the
bot's questions either.

`/tailor` first closes an open journal entry, as `/done` does, and sends a posting already open
for tailoring before it starts the next. A posting the owner doesn't finish closes as an entry
does (FR-CAP-4): at their next message after the entry timeout, which is then journaled as usual,
or at the sweep of quiet entries (`close_quiet_postings`).

The bot never opens a link: a posting that's only links isn't tailored, and the bot asks for its
text. Nothing here logs a posting's text.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TailorDraftRow, TelegramLink
from app.tailor.drafts import POSTING_LIMIT, DraftNotFound, create_draft, load_draft
from app.tailor.view import TailorDraft
from app.telegram.api import BotCall, send

STARTED = (
    "Send me the job posting's text, in as many messages as it takes, then /done to tailor a "
    "resume to it."
)
STARTED_WITH_TEXT = "Got it. Send the rest of the posting, if there's more, then /done."
SENT = "Tailoring a resume to this posting. I'll tell you here when it's ready to check."
EMPTY = "There was no posting text to tailor. Send /tailor, then the posting."
LINKS_ONLY = (
    "I don't open links, so I can't read a posting from one. Copy the posting's text from the "
    "page, then send /tailor and paste it."
)
TOO_LONG = (
    f"That posting is longer than {POSTING_LIMIT:,} characters, so I left it. Send /tailor "
    "and paste just the job's part of the page."
)
_LINKS = re.compile(r"(?:\s*(?:https?://|www\.)\S+)+\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Closed:
    """What closing a posting did: the bot's replies, and the draft to tailor, if it made one."""

    calls: tuple[BotCall, ...] = ()
    draft: uuid.UUID | None = None


async def start(
    session: AsyncSession, owner: uuid.UUID, chat_id: int, text: str, sent_at: datetime
) -> Closed:
    """`/tailor`, with the text sent with it, if any: send a posting already open for tailoring,
    then start a new one."""
    link = await _locked(session, owner)
    if link is None:
        return Closed()
    previous = await _close(session, link) if link.posting_at is not None else Closed()
    link.posting, link.posting_at = text, sent_at
    reply = send(chat_id, STARTED_WITH_TEXT if text.strip() else STARTED)
    return Closed((*previous.calls, reply), previous.draft)


async def take_message(
    session: AsyncSession, owner: uuid.UUID, text: str, sent_at: datetime, timeout: timedelta
) -> tuple[bool, Closed]:
    """A message from the owner, sent at `sent_at`. While a posting is open, it joins the posting,
    unless the posting has been quiet for `timeout`: then the posting closes, and the message is
    the journal's. Returns whether the message joined a posting, and what closing one did."""
    link = await _locked(session, owner)
    if link is None or link.posting_at is None:
        return False, Closed()
    if sent_at - link.posting_at >= timeout:
        return False, await _close(session, link)
    link.posting = f"{link.posting}\n{text}" if link.posting else text
    link.posting_at = max(link.posting_at, sent_at)
    return True, Closed()


async def done(session: AsyncSession, owner: uuid.UUID) -> Closed | None:
    """`/done` while a posting is open: send it for tailoring. None when no posting is open."""
    link = await _locked(session, owner)
    if link is None or link.posting_at is None:
        return None
    return await _close(session, link)


async def close_quiet_postings(
    session: AsyncSession, now: datetime, timeout: timedelta
) -> list[Closed]:
    """Close every posting that has been quiet for `timeout` by `now`, for the sweep of quiet
    entries. Returns what closing each one did."""
    links = await session.scalars(
        select(TelegramLink)
        .where(TelegramLink.posting_at <= now - timeout)
        .with_for_update(skip_locked=True)
    )
    return [await _close(session, link) for link in links.all()]


async def sent_to_bot(session: AsyncSession, draft_id: uuid.UUID) -> tuple[uuid.UUID, int] | None:
    """The owner and the chat of a draft whose posting was sent to the bot, while the chat is
    linked; None for any other draft."""
    row = await session.execute(
        select(TailorDraftRow.user_id, TelegramLink.chat_id)
        .join(TelegramLink, TelegramLink.user_id == TailorDraftRow.user_id)
        .where(TailorDraftRow.id == draft_id, TailorDraftRow.from_bot)
    )
    found = row.one_or_none()
    return None if found is None else (found.user_id, found.chat_id)


async def tailored(
    session: AsyncSession, draft_id: uuid.UUID, app_url: str | None
) -> tuple[int, str] | None:
    """The chat to tell, and what to say, once a draft whose posting was sent to the bot is
    tailored; None for any other draft, or one that isn't ready."""
    sent = await sent_to_bot(session, draft_id)
    if sent is None:
        return None
    user_id, chat_id = sent
    try:
        draft = await load_draft(session, user_id, draft_id)
    except DraftNotFound:
        return None
    if draft.status != "ready":
        return None
    return chat_id, tailored_text(draft, app_url)


def tailored_text(draft: TailorDraft, app_url: str | None) -> str:
    """What the bot says once a posting it was sent is tailored: what the resume covers, the new
    phrasings it offers, the skills to confirm before the download (R3), and where to check it."""
    posting = draft.posting
    title = "the posting" if posting is None else posting.title
    at = f" at {posting.company}" if posting is not None and posting.company else ""
    parts = [f"Your resume for {title}{at} is ready to check."]
    if draft.coverage is not None:
        covered = len(draft.coverage.covered)
        terms = covered + len(draft.coverage.missing)
        parts.append(f"It shows {covered} of the posting's {terms} terms.")
    phrasings = sum(1 for words in draft.bullets if words.phrasing and not words.approved)
    if phrasings:
        parts.append(f"It offers {_count(phrasings, 'new phrasing')} in the posting's words.")
    skills = sum(1 for skill in draft.skills if skill.decision is None)
    if skills:
        parts.append(f"Confirm or drop {_count(skills, 'skill')} before you download it.")
    return " ".join(parts) + _where(draft.id, app_url)


def failed_text(draft_id: uuid.UUID, reason: str, app_url: str | None) -> str:
    """What the bot says when a posting it was sent couldn't be tailored. `reason` is the draft's
    error, a sentence the Tailor page shows too."""
    return f"{reason} You can try again on the Tailor page." + _where(draft_id, app_url)


def draft_link(app_url: str, draft_id: uuid.UUID) -> str:
    """The draft on the Tailor page."""
    return f"{app_url}/tailor?draft={draft_id}"


async def _locked(session: AsyncSession, owner: uuid.UUID) -> TelegramLink | None:
    return await session.scalar(
        select(TelegramLink).where(TelegramLink.user_id == owner).with_for_update()
    )


async def _close(session: AsyncSession, link: TelegramLink) -> Closed:
    """Send the link's open posting for tailoring, if it can be, and end it."""
    text = (link.posting or "").strip()
    link.posting, link.posting_at = None, None
    chat_id = link.chat_id
    if not text:
        return Closed((send(chat_id, EMPTY),))
    if _LINKS.fullmatch(text):
        return Closed((send(chat_id, LINKS_ONLY),))
    if len(text) > POSTING_LIMIT:
        return Closed((send(chat_id, TOO_LONG),))
    draft = await create_draft(session, link.user_id, text, from_bot=True)
    return Closed((send(chat_id, SENT),), draft)


def _where(draft_id: uuid.UUID, app_url: str | None) -> str:
    return "" if app_url is None else f"\n{draft_link(app_url, draft_id)}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
