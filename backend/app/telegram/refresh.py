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

from app.github.sync import LinkedRead
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


def proposals_text(proposals: Proposals, app_url: str, *, github: LinkedRead | None = None) -> str:
    """What the bot says when a refresh's proposals are saved (FR-REM-6), with what it read from
    the linked repositories first, if it read them (FR-GH-8)."""
    read = "" if github is None else github_text(github)
    if proposals.changes:
        return (
            f"{_count(proposals.changes, 'update')} proposed from "
            f"{_count(proposals.facts, 'fact')} since your last refresh. Review them:\n"
            f"{inbox_link(app_url)}"
        ) + (f"\n\n{read}" if read else "")
    if proposals.facts:
        verb = "adds" if proposals.facts == 1 else "add"
        text = (
            f"Nothing to review: the {_count(proposals.facts, 'fact')} since your last refresh "
            f"{verb} nothing to your profile."
        )
    else:
        text = NOTHING_NEW
    return text + (f"\n\n{read}" if read else "")


def github_text(read: LinkedRead) -> str:
    """What a read of the linked repositories did, in counts: never a repository's name or text
    (FR-GH-9). Empty when no repository is linked."""
    if read.stopped:
        return (
            "Your linked GitHub repositories couldn't be read this time. The next /refresh, or "
            "`wj github sync`, tries again."
        )
    if not read.repositories:
        return ""
    parts = [
        f"From GitHub: read {_count(read.parts, 'part')} of your linked repositories, with "
        f"{_count(read.facts, 'new fact')}."
        if read.parts
        else "From GitHub: nothing new read from your linked repositories."
    ]
    if read.left:
        wait = "waits" if read.left == 1 else "wait"
        parts.append(
            f"{_count(read.left, 'part')} {wait} for the next /refresh or `wj github sync`."
        )
    if read.failed:
        were, it = ("was", "it") if read.failed == 1 else ("were", "them")
        parts.append(
            f"{_count(read.failed, 'part')} {were} left out: the model couldn't read {it}."
        )
    if read.unreadable:
        parts.append(f"{_repositories(read.unreadable)} couldn't be read this time.")
    if read.skipped:
        was, it = ("was", "it") if read.skipped == 1 else ("were", "them")
        parts.append(f"{_repositories(read.skipped)} {was} being read already, so I left {it}.")
    return " ".join(parts)


def failure_text(reason: str) -> str:
    """What the bot says when a refresh fails."""
    return f"I couldn't propose changes because {reason}. Your facts wait for the next /refresh."


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _repositories(number: int) -> str:
    return "1 repository" if number == 1 else f"{number} repositories"
