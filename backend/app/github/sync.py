"""Reading what's new in a linked repository, and saving its facts a part at a time (FR-GH-2,
FR-GH-3, FR-GH-7).

A read lists the history from where the link says to start (`LinkedRepository.start`), leaves
out the items earlier reads took (`unread`), and has the model read the rest one part at a time
(`app/github/extraction.py`). Each part's facts are saved with the keys of its items, in one
transaction, as it goes (`save_part`):

- Each item is read once. When another read took one of a part's items first, such as a refresh
  on the server while `wj github sync` runs, the part saves nothing and the read doesn't count
  as finished, so the next one starts from where this one did and misses nothing.
- A part the model declines or can't answer is recorded (its items are kept as read and failed)
  and skipped.
- An outage of the model stops the repository; the next read carries on from there. GitHub's
  errors are raised before any part is read (`plan_read`), so the caller can go on to the next
  repository.

A read that took every part records when it began (`finish_read`), so the next looks back 14 days
from then. Nothing here logs a repository's name or text (FR-GH-9).
"""

import asyncio
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repo_links import ItemKey, LinkedRepository, finish_read, save_part
from app.github.api import GitHubApi, Repository
from app.github.digest import Digest, build_digest, unread
from app.github.extraction import ask_for_repo_facts, check_repo_facts
from app.github.reader import ExpectedOwner, check_owner, read_history
from app.llm.client import LLMCallError, LLMClient, LLMInvalidOutput, LLMRefusal
from app.schema.fact import Fact
from app.schema.profile import Profile


@dataclass(frozen=True)
class PartRead:
    index: int
    """The part of the digest, from 0."""
    facts: tuple[Fact, ...] = ()
    """The facts saved from it."""
    left_out: int = 0
    """Facts the checks left out (`app/github/extraction.py`)."""
    failed: str | None = None
    """Why the model gave no facts for it. It never holds the history's text."""
    taken: bool = False
    """Whether another read took one of its items first, so it saved nothing."""


@dataclass(frozen=True)
class ReadReport:
    parts: tuple[PartRead, ...] = ()
    stopped: str | None = None
    """Why the read stopped before its last part, such as the model being unreachable."""
    finished: bool = False
    """Whether it took every part, so the next read looks back from when it began."""

    @property
    def facts(self) -> int:
        return sum(len(part.facts) for part in self.parts)

    @property
    def failed(self) -> tuple[PartRead, ...]:
        return tuple(part for part in self.parts if part.failed is not None)


async def find_repository(api: GitHubApi, name: str, *, owner: ExpectedOwner) -> Repository:
    """The repository, once the token is known to be the owner's (FR-GH-1)."""
    check_owner(await api.viewer(), owner)
    return await api.repository(name)


async def plan_read(
    api: GitHubApi,
    name: str,
    *,
    owner: ExpectedOwner,
    start: datetime | None,
    read: Collection[ItemKey],
) -> Digest:
    """The items in the repository's history since `start` that no earlier read took, in parts.
    The description, languages and README are read only until a read has taken them."""
    history = await read_history(
        api, name, owner=owner, since=start, about=("about", "") not in read
    )
    return unread(build_digest(history), read)


async def read_parts(
    session: AsyncSession,
    user_id: uuid.UUID,
    link: LinkedRepository,
    digest: Digest,
    profile: Profile,
    client: LLMClient,
    *,
    started_at: datetime,
    on_part: Callable[[PartRead], None] = lambda part: None,
) -> ReadReport:
    """Read the digest's parts one at a time and save each one's facts, as the module docstring
    describes. `started_at` is when the read began, before GitHub was asked; `on_part` hears of
    each part as it's saved."""
    parts: list[PartRead] = []
    stopped: str | None = None
    for index, items in enumerate(digest.windows):
        try:
            reply = await asyncio.to_thread(
                ask_for_repo_facts, digest, index, profile, client, target=link.target
            )
        except (LLMRefusal, LLMInvalidOutput) as error:
            saved = await save_part(session, user_id, link.id, items, (), failed=True)
            part = PartRead(index, failed=str(error), taken=not saved)
        except LLMCallError as error:
            stopped = str(error)
            break
        else:
            checked = check_repo_facts(
                reply, digest, index, profile, target=link.target, part=uuid.uuid4().hex
            )
            saved = await save_part(session, user_id, link.id, items, checked.facts)
            part = PartRead(
                index,
                facts=checked.facts if saved else (),
                left_out=len(checked.dropped),
                taken=not saved,
            )
        parts.append(part)
        on_part(part)
    finished = stopped is None and not any(part.taken for part in parts)
    if finished:
        await finish_read(session, link.id, started_at)
    return ReadReport(tuple(parts), stopped, finished)
