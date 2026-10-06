"""One tick of background work, for hosting without an always-on worker (requirements §13).

Vercel's cron calls `/internal/tick` once a day (`backend/vercel.json`), the most its free plan
allows, and `wj tick` does the same by hand. Each tick does what `wj worker` does without
stopping, in bounded time:

1. It defers the scheduled tasks that are due. The database keeps a task from being deferred
   twice for the same time, so ticks may overlap or repeat.
2. It retries the jobs whose worker stopped without finishing them, such as a tick cut short.
3. It runs queued jobs for up to `budget_s`. A job that has started by then gets `JOB_GRACE_S`
   more to finish; one that still hasn't is aborted and retried by a later tick.
"""

import asyncio
import contextlib
import time

import procrastinate
from procrastinate.periodic import PeriodicDeferrer

from app.jobs import jobs

TICK_BUDGET_S = 20.0
"""How long a tick starts new jobs."""
JOB_GRACE_S = 30.0
"""How long a job that started within the budget may still run. With the budget, this stays
under the function's 60 s limit (`backend/vercel.json`)."""
MISSED_SCHEDULE_S = 25 * 3600
"""A scheduled task due within the last 25 hours still runs: the tick comes once a day, at any
time within its hour, so each daily task runs once a day."""

_running = asyncio.Lock()


async def tick(
    app: procrastinate.App = jobs, *, budget_s: float = TICK_BUDGET_S, now: float | None = None
) -> int | None:
    """Run one tick, and return how many stalled jobs it retried. Returns None, doing nothing,
    while another tick runs in this process."""
    if _running.locked():
        return None
    async with _running, app.open_async():
        await _defer_due_tasks(app, time.time() if now is None else now)
        stalled = list(await app.job_manager.get_stalled_jobs())
        for job in stalled:
            await app.job_manager.retry_job(job)
        worker = asyncio.create_task(
            app.run_worker_async(
                wait=False,
                listen_notify=False,
                install_signal_handlers=False,
                shutdown_graceful_timeout=JOB_GRACE_S,
            )
        )
        done, _ = await asyncio.wait({worker}, timeout=budget_s)
        if not done:
            worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    return len(stalled)


async def _defer_due_tasks(app: procrastinate.App, at: float) -> None:
    """Defer the scheduled tasks due by `at`, as `wj worker` does on its own schedule."""
    deferrer = PeriodicDeferrer(registry=app.periodic_registry, max_delay=MISSED_SCHEDULE_S)
    # Procrastinate's periodic types are partly untyped.
    due = deferrer.get_previous_tasks(at=at)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    await deferrer.defer_jobs(due)  # pyright: ignore[reportUnknownMemberType]
