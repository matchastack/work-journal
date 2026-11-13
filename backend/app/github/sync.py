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

`read_linked` reads every linked repository before a refresh proposes (FR-GH-8): it claims each
one, so a repository another read is working on is skipped (`claim_read`), and reads at most
`max_parts` parts, all at once, so a refresh on the server fits its time. Nothing it meets fails
it: GitHub's and the model's errors are counted, and the parts saved stay saved.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repo_links import (
    ItemKey,
    LinkedRepository,
    claim_read,
    finish_read,
    list_links,
    read_keys,
    release_read,
    save_part,
)
from app.github.api import GitHubApi, GitHubApiError, Repository
from app.github.digest import Digest, build_digest, unread
from app.github.extraction import RepoExtractionReply, ask_for_repo_facts, check_repo_facts
from app.github.reader import ExpectedOwner, check_owner, read_history
from app.llm.client import LLMCallError, LLMClient, LLMError, LLMInvalidOutput, LLMRefusal
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


logger = logging.getLogger(__name__)

AT_ONCE = 4
"""How many parts `read_linked` has the model read at the same time."""


@dataclass(frozen=True)
class LinkedRead:
    """What a read of every linked repository did, in counts, so the bot can say so without a
    repository's name (FR-GH-9, FR-REM-6)."""

    repositories: int = 0
    """The linked repositories."""
    parts: int = 0
    """Parts read: their facts saved, or recorded as parts the model couldn't read."""
    facts: int = 0
    left: int = 0
    """Parts left for the next refresh or `wj github sync`."""
    failed: int = 0
    """Of the parts read, those the model couldn't read. They aren't read again."""
    skipped: int = 0
    """Repositories another read was working on."""
    unreadable: int = 0
    """Repositories that couldn't be read this time: GitHub or the model failed, or the project
    they're linked to isn't in the profile."""
    stopped: bool = False
    """Whether the read stopped on an error before it was done, so its counts are unknown."""


@dataclass
class _Planned:
    link: LinkedRepository
    digest: Digest
    started_at: datetime
    whole: bool = True
    """Whether every part was saved, so the read counts as finished."""


async def read_linked(
    session: AsyncSession,
    user_id: uuid.UUID,
    api: GitHubApi,
    profile: Profile,
    client: LLMClient,
    *,
    owner: ExpectedOwner,
    max_parts: int | None = None,
    at_once: int = AT_ONCE,
) -> LinkedRead:
    """Read what's new in each of the user's linked repositories and save its facts, as the module
    docstring describes: at most `max_parts` parts in all (None: every part), `at_once` at a
    time. The session commits as it goes."""
    links = await list_links(session, user_id)
    planned: list[_Planned] = []
    claimed: list[uuid.UUID] = []
    skipped = unreadable = 0
    try:
        for link in links:
            try:
                link.target.project_name(profile)
            except ValueError:
                unreadable += 1
                continue
            started_at = datetime.now(UTC)
            if not await claim_read(session, link.id, now=started_at):
                skipped += 1
                continue
            claimed.append(link.id)
            read = await read_keys(session, link.id)
            # Nothing waits on the database while GitHub or the model is read.
            await session.commit()
            try:
                digest = await plan_read(api, link.name, owner=owner, start=link.start, read=read)
            except GitHubApiError as error:
                logger.warning("a linked repository couldn't be read: %s", type(error).__name__)
                unreadable += 1
                continue
            planned.append(_Planned(link, digest, started_at))
        parts = [(plan, index) for plan in planned for index in range(len(plan.digest.windows))]
        chosen = parts if max_parts is None else parts[:max_parts]
        for plan, _ in parts[len(chosen) :]:
            plan.whole = False
        limit = asyncio.Semaphore(at_once)

        async def ask(plan: _Planned, index: int) -> RepoExtractionReply | LLMError:
            async with limit:
                try:
                    return await asyncio.to_thread(
                        ask_for_repo_facts,
                        plan.digest,
                        index,
                        profile,
                        client,
                        target=plan.link.target,
                    )
                except LLMError as error:
                    return error

        answers = await asyncio.gather(*(ask(plan, index) for plan, index in chosen))
        read = facts = failed = 0
        stopped: set[uuid.UUID] = set()
        for (plan, index), answer in zip(chosen, answers, strict=True):
            items = plan.digest.windows[index]
            if isinstance(answer, LLMRefusal | LLMInvalidOutput):
                saved = await save_part(session, user_id, plan.link.id, items, (), failed=True)
                failed += saved
            elif isinstance(answer, LLMError):
                logger.warning("a linked repository's part wasn't read: %s", type(answer).__name__)
                stopped.add(plan.link.id)
                saved = False
            else:
                checked = check_repo_facts(
                    answer,
                    plan.digest,
                    index,
                    profile,
                    target=plan.link.target,
                    part=uuid.uuid4().hex,
                )
                saved = await save_part(session, user_id, plan.link.id, items, checked.facts)
                facts += len(checked.facts) if saved else 0
            read += saved
            plan.whole &= saved
        for plan in planned:
            if plan.whole:
                await finish_read(session, plan.link.id, plan.started_at)
    finally:
        for link_id in claimed:
            await release_read(session, link_id)
    return LinkedRead(
        repositories=len(links),
        parts=read,
        facts=facts,
        left=len(parts) - read,
        failed=failed,
        skipped=skipped,
        unreadable=unreadable + len(stopped),
    )
