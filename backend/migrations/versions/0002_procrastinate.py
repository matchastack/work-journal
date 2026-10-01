"""Procrastinate's job queue

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 16:00:00
"""

from collections.abc import Sequence
from importlib.resources import files

from alembic import op
from sqlalchemy.util import await_

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THROUGH = "03.04.00_50_post_add_retry_failed_job_procedure.sql"
"""The last of Procrastinate's SQL migrations applied here, which gives Procrastinate 3.10's
schema. For a newer Procrastinate, add a revision that applies the files after this one."""

DROP = r"""
DROP TABLE IF EXISTS procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs,
    procrastinate_workers CASCADE;
DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT oid::regprocedure AS signature FROM pg_proc
        WHERE proname LIKE 'procrastinate\_%' AND pronamespace = 'public'::regnamespace
    LOOP
        EXECUTE 'DROP FUNCTION ' || item.signature || ' CASCADE';
    END LOOP;
    FOR item IN
        SELECT typname FROM pg_type
        WHERE typname LIKE 'procrastinate\_%' AND typnamespace = 'public'::regnamespace
            AND typtype IN ('e', 'c')
    LOOP
        EXECUTE 'DROP TYPE ' || quote_ident(item.typname) || ' CASCADE';
    END LOOP;
END $$;
"""


def upgrade() -> None:
    folder = files("procrastinate.sql") / "migrations"
    names = sorted(item.name for item in folder.iterdir() if item.name.endswith(".sql"))
    # Some files add an enum value that a later file uses, which PostgreSQL allows only once the
    # value is committed, so each file runs in its own transaction.
    with op.get_context().autocommit_block():
        for name in names:
            if name <= THROUGH:
                _execute_script((folder / name).read_text(encoding="utf-8"))


def downgrade() -> None:
    _execute_script(DROP)


def _execute_script(sql: str) -> None:
    """Run SQL with several statements, which asyncpg's prepared statements can't hold."""
    driver = op.get_bind().connection.driver_connection
    if driver is None:
        raise RuntimeError("this migration needs a live asyncpg connection")
    await_(driver.execute(sql))
