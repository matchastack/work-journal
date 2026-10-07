import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.crypto import KeyRing, new_key, use_key_ring
from app.db.models import TelegramUpdate
from app.jobs import database, echo, jobs, purge_telegram_updates

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("database_settings")
async def test_the_worker_runs_a_job_from_postgres() -> None:
    """The app opens its own pool from DATABASE_URL, as the tick does. (Procrastinate keeps a pool
    it's given even after closing, which would leave the shared app on a closed pool.)"""
    async with jobs.open_async():
        job_id = await echo.defer_async(message="hello")
        await jobs.run_worker_async(wait=False, install_signal_handlers=False)
        [job] = await jobs.job_manager.list_jobs_async(id=job_id)
    assert job.status == "succeeded"


@pytest.fixture
def database_settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Jobs read DATABASE_URL from the settings, as the worker does."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    use_key_ring(KeyRing.parse(new_key("k1")))
    yield
    use_key_ring(None)
    get_settings.cache_clear()


@pytest.mark.usefixtures("database_settings")
async def test_the_daily_purge_deletes_updates_kept_for_more_than_a_week() -> None:
    now = datetime(2026, 10, 12, 3, 17, tzinfo=UTC)
    update_id = random.SystemRandom().randrange(10**9, 10**12)
    async with database() as session:
        received_at = now - timedelta(days=7, minutes=1)
        session.add(TelegramUpdate(update_id=update_id, payload="{}", received_at=received_at))
    await purge_telegram_updates(timestamp=int(now.timestamp()))
    async with database() as session:
        left = select(func.count()).where(TelegramUpdate.update_id == update_id)
        assert await session.scalar(left) == 0
