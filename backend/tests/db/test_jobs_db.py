import pytest
from psycopg_pool import AsyncConnectionPool

from app.jobs import echo, jobs, psycopg_url

pytestmark = pytest.mark.anyio


async def test_the_worker_runs_a_job_from_postgres(database_url: str) -> None:
    pool = AsyncConnectionPool(conninfo=psycopg_url(database_url), open=False)
    await pool.open()
    try:
        async with jobs.open_async(pool):
            job_id = await echo.defer_async(message="hello")
            await jobs.run_worker_async(wait=False, install_signal_handlers=False)
            [job] = await jobs.job_manager.list_jobs_async(id=job_id)
    finally:
        await pool.close()
    assert job.status == "succeeded"
