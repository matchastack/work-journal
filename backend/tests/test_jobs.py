import datetime
from collections.abc import Iterator

import procrastinate
import pytest
from procrastinate.jobs import Job
from procrastinate.testing import InMemoryConnector

from app.jobs import RETRY, echo, jobs, psycopg_url

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def queue() -> Iterator[InMemoryConnector]:
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        yield connector


async def test_a_job_runs_in_the_worker(queue: InMemoryConnector) -> None:
    job_id = await echo.defer_async(message="hello")
    await jobs.run_worker_async(wait=False, install_signal_handlers=False)
    assert queue.jobs[job_id]["status"] == "succeeded"


async def test_a_failing_job_is_retried_later() -> None:
    connector = InMemoryConnector()
    app = procrastinate.App(connector=connector)

    @app.task(name="always_fails", retry=RETRY)
    async def always_fails() -> None:
        raise RuntimeError("try again")

    job_id = await always_fails.defer_async()
    before = datetime.datetime.now(datetime.UTC)
    await app.run_worker_async(wait=False, install_signal_handlers=False)
    job = connector.jobs[job_id]
    assert (job["status"], job["attempts"]) == ("todo", 1)
    assert 3 <= (job["scheduled_at"] - before).total_seconds() <= 6


def retry_wait(attempts: int) -> int | None:
    """Seconds until the retry after a failed run, when `attempts` runs came before it."""
    job = Job(queue="default", lock=None, queueing_lock=None, task_name="echo", attempts=attempts)
    decision = RETRY.get_retry_decision(exception=RuntimeError(), job=job)
    if decision is None or decision.retry_at is None:
        return None
    return round((decision.retry_at - datetime.datetime.now(datetime.UTC)).total_seconds())


def test_retries_back_off_exponentially_then_stop() -> None:
    assert [retry_wait(attempts) for attempts in range(6)] == [4, 16, 64, 256, 1024, None]


def test_the_scheduled_tasks_and_when_they_run() -> None:
    """The heartbeat every 15 minutes, and the purge of raw updates daily (FR-JRN-2)."""
    scheduled = {
        periodic.task.name: periodic.cron
        for periodic in jobs.periodic_registry.periodic_tasks.values()
    }
    assert scheduled == {"heartbeat": "*/15 * * * *", "purge_telegram_updates": "17 3 * * *"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://app@db/app", "postgresql://app@db/app"),
        ("postgresql://app@db/app", "postgresql://app@db/app"),
    ],
)
def test_the_worker_connects_with_psycopg(url: str, expected: str) -> None:
    assert psycopg_url(url) == expected
