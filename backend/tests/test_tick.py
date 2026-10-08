"""One tick of background work, with an in-memory job queue."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import procrastinate
import pytest
from fastapi.testclient import TestClient
from procrastinate import periodic
from procrastinate.testing import InMemoryConnector
from pydantic import SecretStr

from app.config import Settings
from app.jobs import echo, jobs
from app.main import create_app
from app.tick import _running, extract_now, tick

SECRET = "tick-secret"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def queue() -> InMemoryConnector:
    return InMemoryConnector()


@pytest.fixture
def app(queue: InMemoryConnector) -> procrastinate.App:
    return procrastinate.App(connector=queue)


@pytest.mark.anyio
async def test_a_tick_runs_the_queued_jobs(app: procrastinate.App) -> None:
    ran: list[int] = []

    @app.task(name="note")
    async def note(number: int) -> None:
        ran.append(number)

    await note.defer_async(number=1)
    await note.defer_async(number=2)
    assert await tick(app) == 0
    assert ran == [1, 2]


@pytest.mark.anyio
async def test_daily_ticks_run_a_daily_task_once_a_day(app: procrastinate.App) -> None:
    """Vercel's cron ticks once a day, at any time within its hour, never at the task's own time.
    A daily task due earlier in the day still runs, once, even when a tick comes twice."""
    ran: list[int] = []

    @app.periodic(cron="17 3 * * *")
    @app.task(name="daily")
    async def daily(timestamp: int) -> None:
        ran.append(timestamp)

    due = datetime(2026, 10, 12, 3, 17, tzinfo=UTC).timestamp()
    hour, day = 3600, 24 * 3600
    for at in (due + 16 * hour, due + 16 * hour + 59 * 60, due + day + 15 * hour + 30 * 60):
        await tick(app, now=at)
    assert ran == [int(due), int(due + day)]


@pytest.mark.anyio
async def test_a_tick_defers_scheduled_tasks_only_as_of_its_own_time(
    app: procrastinate.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Procrastinate's worker defers scheduled tasks by the real clock. In a tick it mustn't:
    a run due just before the real time would come on top of the ones due by `now`."""
    ran: list[int] = []

    @app.periodic(cron="17 3 * * *")
    @app.task(name="daily")
    async def daily(timestamp: int) -> None:
        ran.append(timestamp)

    real = datetime(2026, 10, 8, 3, 21, tzinfo=UTC).timestamp()
    monkeypatch.setattr(periodic, "time", SimpleNamespace(time=lambda: real))
    due = datetime(2026, 10, 12, 3, 17, tzinfo=UTC).timestamp()
    await tick(app, now=due + 3600)
    assert ran == [int(due)]
    assert app.periodic_registry.periodic_tasks, "the schedule is back after the tick"


@pytest.mark.anyio
async def test_a_job_started_in_time_finishes_and_the_rest_wait(
    app: procrastinate.App, queue: InMemoryConnector
) -> None:
    ran: list[int] = []

    @app.task(name="slow")
    async def slow(number: int) -> None:
        await asyncio.sleep(0.3)
        ran.append(number)

    await slow.defer_async(number=1)
    await slow.defer_async(number=2)
    await tick(app, budget_s=0.1)
    assert ran == [1]
    assert [job["status"] for job in queue.jobs.values()] == ["succeeded", "todo"]
    await tick(app, budget_s=0.1)
    assert ran == [1, 2]


@pytest.mark.anyio
async def test_a_job_whose_worker_stopped_is_run_again(
    app: procrastinate.App, queue: InMemoryConnector
) -> None:
    """A tick cut short leaves its job running for a worker that's gone."""
    ran: list[int] = []

    @app.task(name="note")
    async def note(number: int) -> None:
        ran.append(number)

    job_id = await note.defer_async(number=7)
    queue.jobs[job_id].update(status="doing", worker_id=999)
    assert await tick(app) == 1
    assert ran == [7]
    assert queue.jobs[job_id]["status"] == "succeeded"


@pytest.mark.anyio
async def test_a_ticks_first_step_queues_jobs_it_then_runs(app: procrastinate.App) -> None:
    """`first` runs with the queue open, before anything else, so its jobs run in the same tick."""
    ran: list[str] = []

    @app.task(name="extract")
    async def extract(entry: str) -> None:
        ran.append(entry)

    async def first() -> None:
        await extract.defer_async(entry="e1")

    assert await tick(app, first=first) == 0
    assert ran == ["e1"]


@pytest.mark.anyio
async def test_while_a_tick_runs_another_does_nothing(app: procrastinate.App) -> None:
    """Not even its first step: the ticks in a process share the queue's connection."""
    started: list[bool] = []

    async def first() -> None:
        started.append(True)

    async with _running:
        assert await tick(app, first=first) is None
    assert started == []


@pytest.mark.anyio
async def test_extracting_a_closed_entry_right_away_never_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """It runs after the webhook has answered, as a shortcut: if it fails, the scheduled sweep
    queues the entry instead. The log names the error's type, not its message."""

    async def unreachable(entry_id: uuid.UUID) -> bool:
        raise RuntimeError("could not connect: SELECT ... WHERE id = 'secret'")

    monkeypatch.setattr("app.tick.queue_extraction", unreachable)
    with jobs.replace_connector(InMemoryConnector()):
        await extract_now(uuid.uuid4())
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


# --- /internal/tick ------------------------------------------------------------------------------


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[InMemoryConnector]:
    """No settings from the environment, and the app's job queue in memory."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        yield connector


def call_tick(
    authorization: str | None, secret: str | None = SECRET, method: str = "GET"
) -> tuple[int, object]:
    """Call the tick as Vercel's cron does: GET, with CRON_SECRET as a bearer token."""
    settings = Settings(cron_secret=SecretStr(secret) if secret else None)
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(create_app(settings)) as client:
        response = client.request(method, "/internal/tick", headers=headers)
    return response.status_code, response.json()


@pytest.mark.parametrize(
    ("authorization", "secret"),
    [(None, SECRET), ("Bearer wrong", SECRET), (SECRET, SECRET), ("Bearer ", None)],
)
def test_only_the_cron_may_tick(
    isolated: InMemoryConnector, authorization: str | None, secret: str | None
) -> None:
    status, _ = call_tick(authorization, secret)
    assert status == 401


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_the_cron_ticks_with_its_secret(isolated: InMemoryConnector, method: str) -> None:
    """Vercel's cron calls with GET; POST runs a tick by hand."""
    job_id = asyncio.run(_defer_echo())
    assert call_tick(f"Bearer {SECRET}", method=method) == (200, {"retried": 0})
    assert isolated.jobs[job_id]["status"] == "succeeded"


async def _defer_echo() -> int:
    async with jobs.open_async():
        return await echo.defer_async(message="hello")
