"""Run Alembic migrations against DATABASE_URL, or a URL or connection a caller provides."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from logging.config import fileConfig
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.crypto import EncryptedText
from app.db.engine import MIGRATION_LOCK, async_url
from app.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        secret = get_settings().database_url
        if secret is None:
            raise RuntimeError("set DATABASE_URL to run migrations")
        url = secret.get_secret_value()
    return async_url(url)


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Compare only the app's own tables, not Procrastinate's (migration 0002)."""
    return type_ != "table" or name in Base.metadata.tables


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Write encrypted columns into new migrations as what the database stores: bytes."""
    if type_ == "type" and isinstance(obj, EncryptedText):
        return "sa.LargeBinary()"
    return False


@contextmanager
def migration_lock(connection: Connection) -> Iterator[None]:
    """Hold the migrations' advisory lock for the whole run. A run that starts while another is
    migrating waits here, then finds the database at that one's revision. The lock belongs to the
    connection, not a transaction, because some migrations commit as they go (0002)."""
    own_transaction = not connection.in_transaction()
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK})
    if own_transaction:
        connection.commit()
    try:
        yield
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
        if own_transaction:
            connection.commit()


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        include_name=include_name,
        render_item=render_item,
    )
    with migration_lock(connection), context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    asyncio.run(run_async_migrations())
