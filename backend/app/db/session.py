"""Database engine and session dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# PH-331: server-side backstop against a leaked transaction. If application code
# ever again opens a transaction and parks it (a long-lived session that SELECTs
# and then blocks — see the WebSocket leak in api/websocket.py), Postgres now kills
# that backend itself after 60s instead of holding its locks forever. Without this,
# one leaked session blocked an `alembic` DROP INDEX on `actors` indefinitely, and
# the queued DDL then blocked every later read of that table.
#
# This only fires on connections sitting IDLE INSIDE a transaction — it never
# interrupts a running query (that would be `statement_timeout`) nor an idle
# pooled connection with no open transaction, so healthy request and poller
# traffic is unaffected. `database_url` is always postgresql+asyncpg
# (core/config.py), so the asyncpg-specific `server_settings` is safe; tests build
# their own SQLite engine (tests/conftest.py) and never reach this module.
# Named (not inlined) so the guarantee is assertable from tests — see
# tests/test_ph331_ws_session_leak.py.
CONNECT_ARGS = {
    "server_settings": {
        "idle_in_transaction_session_timeout": "60000",  # ms
        # Makes a leaked/blocking backend identifiable in pg_stat_activity
        # instead of showing up as an anonymous asyncpg connection.
        "application_name": "project-hub-backend",
    }
}

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=CONNECT_ARGS,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
