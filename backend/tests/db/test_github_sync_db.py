"""Linked repositories (FR-GH-2, FR-GH-7): each read saves its parts' facts as it goes, takes each
item once, even when two reads run at once, and records a part the model can't read. Against a
stand-in for GitHub, with the fake LLM client."""

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import APIConnectionError
from anthropic.types.beta import BetaMessage
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import FactRow, RepoItemRow, User
from app.db.profiles import save_version
from app.db.repo_links import (
    READ_LEASE,
    ItemKey,
    LinkedRepository,
    claim_read,
    get_link,
    item_counts,
    link_repository,
    list_links,
    read_keys,
    release_read,
    unlink,
)
from app.db.store import list_facts
from app.github.api import GitHubApi
from app.github.digest import Digest, build_digest, unread
from app.github.extraction import RepoExtractionReply, RepoTarget
from app.github.fake import TOKEN, FakeGitHubApi
from app.github.reader import ExpectedOwner, read_history
from app.github.sync import LinkedRead, ReadReport, plan_read, read_linked, read_parts
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog
from app.schema.profile import Profile

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
RECIPE_BOX = "casey-example/recipe-box"
RECIPE_BOX_ID = 31_000_001
OWNER = ExpectedOwner(github_id=4242)
STARTED = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
SHOPPING = "Added a weekly shopping list that groups ingredients by aisle."
IMPORT = "Imported recipes from a web page by its address."


@pytest.fixture(autouse=True)
def keys() -> Any:
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)


def github() -> FakeGitHubApi:
    payloads = FIXTURES / "github"
    fake = FakeGitHubApi(json.loads((payloads / "viewer.json").read_text(encoding="utf-8")))
    fake.add(json.loads((payloads / "recipe-box.json").read_text(encoding="utf-8")))
    return fake


async def planned(read: Collection[ItemKey] = (), *, window_items: int = 3) -> Digest:
    """The recipe box's unread items, in parts of `window_items`: with 3, the first part is
    `about`, `c1` and `c2`, and the second `pr1`, `c3` and `pr2`."""
    async with httpx2.AsyncClient(transport=github().transport) as http:
        history = await read_history(GitHubApi(SecretStr(TOKEN), http), RECIPE_BOX, owner=OWNER)
    return unread(build_digest(history), read, window_items=window_items)


def model(
    *answers: RepoExtractionReply | BetaMessage | Exception,
) -> tuple[LLMClient, FakeMessages]:
    fake = FakeMessages(
        *(
            reply(answer) if isinstance(answer, RepoExtractionReply) else answer
            for answer in answers
        )
    )
    return LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog()), fake


def found(*facts: tuple[str, str]) -> RepoExtractionReply:
    """The model's reply: each fact's statement and the label of the item it cites."""
    return RepoExtractionReply.model_validate(
        {
            "facts": [
                {"items": [label], "statement": statement, "kind": "accomplishment"}
                for statement, label in facts
            ]
        }
    )


async def new_user(session: AsyncSession) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    await save_version(session, user.id, PROFILE, author="owner")
    return user.id


async def linked(
    session: AsyncSession, user_id: uuid.UUID, target: RepoTarget | None = None
) -> LinkedRepository:
    link = await link_repository(
        session,
        user_id,
        github_id=RECIPE_BOX_ID,
        name=RECIPE_BOX,
        target=target or RepoTarget(project_id="recipe_box"),
        since=date(2023, 6, 1),
    )
    await session.commit()
    return link


async def read(
    session: AsyncSession,
    user_id: uuid.UUID,
    link: LinkedRepository,
    digest: Digest,
    llm: LLMClient,
    *,
    started_at: datetime = STARTED,
) -> ReadReport:
    return await read_parts(session, user_id, link, digest, PROFILE, llm, started_at=started_at)


async def test_a_read_saves_each_parts_facts_with_its_items(session: AsyncSession) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    llm, fake = model(found((IMPORT, "c2")), found((SHOPPING, "pr2")))
    digest = await planned()
    report = await read(session, user_id, link, digest, llm)
    assert len(fake.requests) == 2
    assert report.finished and report.stopped is None
    assert [len(part.facts) for part in report.parts] == [1, 1]
    facts = await list_facts(session, user_id, project_id="recipe_box")
    assert {fact.statement for fact in facts} == {IMPORT, SHOPPING}
    assert {fact.origin for fact in facts} == {"github"}
    keys = {(item.kind, item.ref) for item in digest.items}
    assert len(keys) == 6 and await read_keys(session, link.id) == keys
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.read_at == STARTED


async def test_a_second_read_with_nothing_new_makes_no_model_call(session: AsyncSession) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    llm, _ = model(found((IMPORT, "c2")), found((SHOPPING, "pr2")))
    await read(session, user_id, link, await planned(), llm)
    again = await planned(await read_keys(session, link.id))
    assert again.windows == ()
    idle, fake = model()
    later = STARTED + timedelta(days=7)
    report = await read(session, user_id, link, again, idle, started_at=later)
    assert fake.requests == []
    assert report.finished
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.read_at == later
    assert stored.start == later - timedelta(days=14), "it looks back 14 days"


async def test_a_part_the_model_declines_is_recorded_and_skipped(session: AsyncSession) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    llm, _ = model(refusal("cyber"), found((SHOPPING, "pr2")))
    report = await read(session, user_id, link, await planned(), llm)
    assert report.finished
    (failed,) = report.failed
    assert failed.index == 0 and failed.failed is not None and "declined" in failed.failed
    assert [fact.statement for fact in await list_facts(session, user_id)] == [SHOPPING]
    counts = (await item_counts(session, user_id))[link.id]
    assert (counts.read, counts.failed) == (6, 3)
    assert (await planned(await read_keys(session, link.id))).windows == (), "it isn't retried"


async def test_an_outage_stops_the_repository_until_the_next_read(session: AsyncSession) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    outage = APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    llm, fake = model(found((IMPORT, "c2")), outage)
    report = await read(session, user_id, link, await planned(), llm)
    assert len(fake.requests) == 2
    assert not report.finished and report.stopped is not None
    assert [fact.statement for fact in await list_facts(session, user_id)] == [IMPORT]
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.read_at is None
    assert stored.start == datetime(2023, 6, 1, tzinfo=UTC), "the next read starts where it did"
    rest = await planned(await read_keys(session, link.id))
    assert [item.label for item in rest.items] == ["pr1", "c3", "pr2"]


async def test_linking_again_carries_on_where_it_stopped(session: AsyncSession) -> None:
    """FR-GH-2: unlinking stops reading the repository and keeps its facts; linking it again,
    here to a new project, keeps what was read and when."""
    user_id = await new_user(session)
    link = await linked(session, user_id)
    llm, _ = model(found((IMPORT, "c2")), found((SHOPPING, "pr2")))
    await read(session, user_id, link, await planned(), llm)
    await unlink(session, link.id, now=STARTED + timedelta(days=1))
    await session.commit()
    assert await list_links(session, user_id) == []
    assert len(await list_facts(session, user_id)) == 2
    again = await linked(session, user_id, RepoTarget(new_project="Recipe box app"))
    assert again.id == link.id
    assert again.unlinked_at is None and again.read_at == STARTED
    assert again.target == RepoTarget(new_project="Recipe box app")
    assert len(await read_keys(session, link.id)) == 6
    assert [item.name for item in await list_links(session, user_id)] == [RECIPE_BOX]


async def test_the_month_a_link_starts_from_is_kept_once_a_read_finished(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    relinked = await link_repository(
        session,
        user_id,
        github_id=RECIPE_BOX_ID,
        name=RECIPE_BOX,
        target=RepoTarget(project_id="recipe_box"),
        since=date(2023, 8, 1),
    )
    assert relinked.since == date(2023, 8, 1), "no read has finished, so it starts later now"
    llm, _ = model(found((SHOPPING, "pr2")))
    await read(session, user_id, relinked, await planned(window_items=30), llm)
    after = await link_repository(
        session,
        user_id,
        github_id=RECIPE_BOX_ID,
        name=RECIPE_BOX,
        target=RepoTarget(project_id="recipe_box"),
        since=date(2020, 1, 1),
    )
    assert after.since == date(2023, 8, 1) and after.id == link.id


async def test_a_whole_read_logs_no_repository_name_path_text_or_token(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-GH-9."""
    user_id = await new_user(session)
    link = await linked(session, user_id)
    llm, _ = model(found((IMPORT, "c2")), found((SHOPPING, "pr2")))
    with caplog.at_level(logging.DEBUG):
        async with httpx2.AsyncClient(transport=github().transport) as http:
            api = GitHubApi(SecretStr(TOKEN), http)
            digest = await plan_read(api, RECIPE_BOX, owner=OWNER, start=link.start, read=())
        await read(session, user_id, link, unread(digest, (), window_items=3), llm)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("recipe-box", "casey-example", TOKEN, SHOPPING, IMPORT, "shopping list"):
        assert secret not in logged


async def test_two_reads_at_once_never_save_an_item_twice(database_url: str) -> None:
    """FR-GH-7: two reads of the same items, such as a refresh on the server and `wj github
    sync`, both ask the model, but only the first to save keeps its facts. The other saves
    nothing and doesn't count as finished."""
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user_id = await new_user(session)
            await session.commit()
            link = await linked(session, user_id)
        digest = await planned(window_items=30)
        both_asked = threading.Barrier(2)

        def answer(request: dict[str, Any]) -> BetaMessage:
            both_asked.wait(timeout=10)
            return reply(found((SHOPPING, "pr2")))

        async def one_read() -> ReadReport:
            fake = FakeMessages(answer=answer)
            llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await read(session, user_id, link, digest, llm)

        first, second = await asyncio.gather(one_read(), one_read())
        assert sorted([first.finished, second.finished]) == [False, True]
        assert sorted([first.facts, second.facts]) == [0, 1]
        async with AsyncSession(engine) as session:
            saved = await session.scalar(
                select(func.count()).select_from(FactRow).where(FactRow.user_id == user_id)
            )
            items = await session.scalar(
                select(func.count()).select_from(RepoItemRow).where(RepoItemRow.user_id == user_id)
            )
            assert (saved, items) == (1, len(digest.items))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
    finally:
        await engine.dispose()


# --- Reading every linked repository before a refresh (T-071) ---


async def read_all(
    session: AsyncSession,
    user_id: uuid.UUID,
    llm: LLMClient,
    *,
    max_parts: int | None = None,
    fake: FakeGitHubApi | None = None,
) -> LinkedRead:
    async with httpx2.AsyncClient(transport=(fake or github()).transport) as http:
        api = GitHubApi(SecretStr(TOKEN), http)
        return await read_linked(
            session, user_id, api, PROFILE, llm, owner=OWNER, max_parts=max_parts
        )


async def test_a_refresh_reads_at_most_its_parts_and_leaves_the_rest(
    session: AsyncSession,
) -> None:
    """FR-GH-8: the server reads a limited number of parts, so the refresh fits its time."""
    user_id = await new_user(session)
    await linked(session, user_id)
    llm, fake = model(found((IMPORT, "pr1")))
    read = await read_all(session, user_id, llm, max_parts=1)
    assert len(fake.requests) == 1
    assert read == LinkedRead(repositories=1, parts=1, facts=1, left=0)
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.read_at is not None, "one part held everything new"
    assert stored.start == stored.read_at - timedelta(days=14)


async def test_parts_beyond_the_limit_wait_for_the_next_read(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parts are read all at once, so the replies come in any order: they're all alike."""
    user_id = await new_user(session)
    await linked(session, user_id)
    # Each item in a part of its own: the about item, c1, c2, pr1, c3 and pr2.
    monkeypatch.setattr("app.github.sync.unread", _one_item_parts)
    llm, fake = model(found(), found(), found())
    read = await read_all(session, user_id, llm, max_parts=3)
    assert len(fake.requests) == 3
    assert (read.parts, read.left, read.facts) == (3, 3, 0)
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.read_at is None, "the read didn't take everything"
    assert len(await read_keys(session, stored.id)) == 3


def _one_item_parts(digest: Digest, read: Collection[ItemKey]) -> Digest:
    return unread(digest, read, window_items=1)


async def test_a_repository_another_read_is_working_on_is_skipped(
    session: AsyncSession,
) -> None:
    user_id = await new_user(session)
    link = await linked(session, user_id)
    assert await claim_read(session, link.id, now=datetime.now(UTC))
    llm, fake = model()
    read = await read_all(session, user_id, llm)
    assert fake.requests == []
    assert read == LinkedRead(repositories=1, skipped=1)
    await release_read(session, link.id)
    assert await claim_read(session, link.id, now=datetime.now(UTC)), "free again"
    stale = datetime.now(UTC) + READ_LEASE + timedelta(minutes=1)
    assert await claim_read(session, link.id, now=stale), "a read that stopped long ago"


async def test_github_and_model_errors_never_stop_the_read(session: AsyncSession) -> None:
    """FR-GH-8: a repository that can't be read is counted, and the parts saved stay saved."""
    user_id = await new_user(session)
    await linked(session, user_id)
    down = github()
    down.status = 503
    llm, fake = model()
    read = await read_all(session, user_id, llm, fake=down)
    assert (read.unreadable, read.parts, fake.requests) == (1, 0, [])
    outage = APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    llm, _ = model(outage)
    read = await read_all(session, user_id, llm)
    assert (read.unreadable, read.parts, read.left) == (1, 0, 1)
    stored = await get_link(session, user_id, github_id=RECIPE_BOX_ID)
    assert stored is not None and stored.reading_since is None, "the read let it go"
