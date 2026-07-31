from __future__ import with_statement

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from attribution.db import Database, DatabaseSettings
from attribution.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", os.getenv(
    "ATTRIBUTION_DATABASE_URL", config.get_main_option("sqlalchemy.url")))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(text(
            "SELECT pg_advisory_lock(hashtext('store_replenishment_alembic'))"))
    try:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if postgres:
            connection.execute(text(
                "SELECT pg_advisory_unlock(hashtext('store_replenishment_alembic'))"))


async def run_async_migrations() -> None:
    database = Database(DatabaseSettings.from_env())
    async with database.engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await database.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
