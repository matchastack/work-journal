"""Profile versions and change sets in the database (FR-PRF-2, FR-PRF-3, NFR-DATA-1)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.profiles import (
    VersionError,
    close_change_set,
    decide_op,
    get_change_set,
    get_version,
    latest_version,
    list_change_sets,
    list_versions,
    load_master_profile,
    propose_change_set,
    restore_version,
    save_version,
)
from app.schema.changes import ChangeSet, EditBullet, ReviewedOp, SetBulletStatus
from app.schema.profile import Profile

pytestmark = pytest.mark.anyio

PROFILE = Profile.model_validate_json(
    (Path(__file__).parents[1] / "fixtures" / "profile.json").read_text(encoding="utf-8")
)


async def new_user(session: AsyncSession) -> uuid.UUID:
    user = User()
    session.add(user)
    await session.flush()
    return user.id


def renamed(profile: Profile, name: str) -> Profile:
    return profile.model_copy(update={"basics": profile.basics.model_copy(update={"name": name})})


async def test_the_imported_profile_is_version_1(session: AsyncSession) -> None:
    user = await new_user(session)
    version = await load_master_profile(session, user, PROFILE)
    assert (version.number, version.parent, version.author) == (1, None, "owner")
    assert version.profile == PROFILE
    latest = await latest_version(session, user)
    assert latest is not None
    assert latest.profile == PROFILE
    assert latest.created_at.tzinfo is not None


async def test_the_profile_is_loaded_only_once(session: AsyncSession) -> None:
    user = await new_user(session)
    await load_master_profile(session, user, PROFILE)
    with pytest.raises(VersionError, match="already loaded"):
        await load_master_profile(session, user, PROFILE)


async def test_each_save_is_a_new_version_made_from_the_latest(session: AsyncSession) -> None:
    user = await new_user(session)
    await save_version(session, user, PROFILE, author="owner")
    second = await save_version(session, user, renamed(PROFILE, "Casey M. Morgan"), author="ai")
    assert (second.number, second.parent, second.author) == (2, 1, "ai")
    assert [info.number for info in await list_versions(session, user)] == [2, 1]
    first = await get_version(session, user, 1)
    assert first.profile.basics.name == "Casey Morgan"


async def test_restoring_a_version_saves_it_as_the_newest(session: AsyncSession) -> None:
    user = await new_user(session)
    await save_version(session, user, PROFILE, author="owner")
    await save_version(session, user, renamed(PROFILE, "Casey M. Morgan"), author="ai")
    restored = await restore_version(session, user, 1)
    assert (restored.number, restored.parent, restored.restored_from) == (3, 2, 1)
    assert restored.profile == PROFILE
    assert [info.number for info in await list_versions(session, user)] == [3, 2, 1]


async def test_a_missing_version_is_an_error(session: AsyncSession) -> None:
    user = await new_user(session)
    assert await latest_version(session, user) is None
    with pytest.raises(VersionError, match="no profile version 4"):
        await restore_version(session, user, 4)


async def test_each_user_has_their_own_versions(session: AsyncSession) -> None:
    first, second = await new_user(session), await new_user(session)
    await save_version(session, first, PROFILE, author="owner")
    await save_version(session, first, PROFILE, author="owner")
    version = await save_version(session, second, PROFILE, author="owner")
    assert version.number == 1
    with pytest.raises(VersionError):
        await get_version(session, second, 2)


async def test_deleting_a_user_deletes_their_history(session: AsyncSession) -> None:
    """History goes only when the owner asks, e.g. by deleting their account."""
    user = await new_user(session)
    await save_version(session, user, PROFILE, author="owner")
    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user})
    assert await latest_version(session, user) is None


def change_set() -> ChangeSet:
    return ChangeSet(
        author="ai",
        summary="From the week's journal",
        base_version=1,
        ops=(
            ReviewedOp(
                op=EditBullet(bullet_id="nw_queue", text="Run Python services", fact_ids=("f1",)),
                verifier_report={"numbers": "ok"},
            ),
            ReviewedOp(
                op=SetBulletStatus(bullet_id="ct_datasets", status="benched", reason="Too old")
            ),
        ),
    )


async def test_a_change_set_keeps_its_operations_in_order(session: AsyncSession) -> None:
    user = await new_user(session)
    change_set_id = await propose_change_set(session, user, change_set())
    saved = await get_change_set(session, user, change_set_id)
    assert saved.change_set == change_set()
    assert saved.decided_at is None
    assert [s.id for s in await list_change_sets(session, user)] == [change_set_id]


async def test_the_owner_decides_each_operation(session: AsyncSession) -> None:
    """FR-REV-2: accept, edit (verified again) or reject with a reason."""
    user = await new_user(session)
    change_set_id = await propose_change_set(session, user, change_set())
    edited = EditBullet(bullet_id="nw_queue", text="Build Python services", fact_ids=("f1",))
    await decide_op(
        session, user, change_set_id, 0, "accepted", op=edited, verifier_report={"numbers": "ok"}
    )
    await decide_op(session, user, change_set_id, 1, "rejected", reason="Still relevant")
    ops = (await get_change_set(session, user, change_set_id)).change_set.ops
    assert [(op.status, op.reason) for op in ops] == [
        ("accepted", None),
        ("rejected", "Still relevant"),
    ]
    assert ops[0].op == edited
    with pytest.raises(VersionError, match="no operation 2"):
        await decide_op(session, user, change_set_id, 2, "accepted")


async def test_a_new_version_records_its_change_set(session: AsyncSession) -> None:
    user = await new_user(session)
    await save_version(session, user, PROFILE, author="owner")
    change_set_id = await propose_change_set(session, user, change_set())
    version = await save_version(
        session, user, renamed(PROFILE, "Casey M. Morgan"), author="ai", change_set_id=change_set_id
    )
    await close_change_set(session, user, change_set_id, "applied")
    saved = await get_change_set(session, user, change_set_id)
    assert (saved.change_set.status, version.change_set_id) == ("applied", change_set_id)
    assert saved.decided_at is not None
    assert await list_change_sets(session, user) == []
    assert len(await list_change_sets(session, user, None)) == 1


async def test_change_sets_belong_to_their_user(session: AsyncSession) -> None:
    owner, other = await new_user(session), await new_user(session)
    change_set_id = await propose_change_set(session, owner, change_set())
    with pytest.raises(VersionError):
        await get_change_set(session, other, change_set_id)
    with pytest.raises(VersionError):
        await close_change_set(session, other, change_set_id, "rejected")
    with pytest.raises(VersionError):
        await decide_op(session, other, change_set_id, 0, "accepted")
