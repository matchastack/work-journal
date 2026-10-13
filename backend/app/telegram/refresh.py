"""`/refresh` in the chat (FR-CAP-7, FR-REV-6, FR-REM-6).

The command starts a refresh (`app/refresh.py`) and the bot says it's looking. The refresh's job
runs right after the answer, as an entry's jobs do (`RefreshAnswer.refresh`), and when the
proposals are saved, it sends `proposals_text`: how many changes wait for review, with a link to
the Inbox, or that there's nothing new. While a refresh runs, `/refresh` says so and hands its
job on again, in case it waits for a tick. A refresh doesn't start while proposed changes wait
for review: new ones would be proposed against a profile those may still change.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.refresh import Blocked, Proposals, start_refresh
from app.telegram.api import BotCall, send

LOOKING = (
    "Looking for changes to your profile in the facts since your last refresh. "
    "I'll message you when they're ready to review."
)
RUNNING = "A refresh is already running. I'll message you when its changes are ready to review."
NO_PROFILE = (
    "There's no profile to refresh yet. Load your master resume first, with wj db load-profile."
)
NOTHING_NEW = "Nothing new since your last refresh, so there's nothing to review."


@dataclass(frozen=True)
class RefreshAnswer:
    """The bot's answer to `/refresh`, and the refresh whose job can run right away, if any."""

    calls: tuple[BotCall, ...]
    refresh: uuid.UUID | None = None


async def refresh(
    session: AsyncSession, owner: uuid.UUID, chat_id: int, *, app_url: str | None
) -> RefreshAnswer:
    """Start a refresh for the owner, or say why it can't start."""
    started = await start_refresh(session, owner)
    if not isinstance(started, Blocked):
        return RefreshAnswer((send(chat_id, LOOKING),), started)
    if started.reason == "running":
        return RefreshAnswer((send(chat_id, RUNNING),), started.running)
    if started.reason == "waiting":
        return RefreshAnswer((send(chat_id, waiting_text(app_url)),))
    return RefreshAnswer((send(chat_id, NO_PROFILE),))


def inbox_link(app_url: str) -> str:
    """Where proposed changes are reviewed in the web app (T-040)."""
    return f"{app_url}/inbox"


def waiting_text(app_url: str | None) -> str:
    """Why a refresh can't start while proposed changes wait, with a link to them when the app's
    address is known."""
    where = "in the Inbox. " if app_url is None else f"in the Inbox:\n{inbox_link(app_url)}\n"
    return (
        f"Changes proposed earlier still wait for review {where}"
        "Decide on them, then send /refresh again."
    )


def proposals_text(proposals: Proposals, app_url: str) -> str:
    """What the bot says when a refresh's proposals are saved (FR-REM-6)."""
    if proposals.changes:
        return (
            f"{_count(proposals.changes, 'update')} proposed from "
            f"{_count(proposals.facts, 'fact')} since your last refresh. Review them:\n"
            f"{inbox_link(app_url)}"
        )
    if proposals.facts:
        verb = "adds" if proposals.facts == 1 else "add"
        return (
            f"Nothing to review: the {_count(proposals.facts, 'fact')} since your last refresh "
            f"{verb} nothing to your profile."
        )
    return NOTHING_NEW


def failure_text(reason: str) -> str:
    """What the bot says when a refresh fails."""
    return f"I couldn't propose changes because {reason}. Your facts wait for the next /refresh."


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
