import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import make_engine

VERSION_SCHEMA = "alembic_meta"


def _run(connection: Connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {VERSION_SCHEMA}"))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table_schema=VERSION_SCHEMA,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    url = get_settings().migration_database_url
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is not set")
    engine = make_engine(url, disable_statement_cache=True, null_pool=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
asyncio.run(_run_async())
