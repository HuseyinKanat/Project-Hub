"""PH-331: a WebSocket connection must not own a DB session.

Regression guard for an idle-in-transaction leak. The board/ticket WS endpoints
used to open a session with a bare ``SessionLocal()`` (no ``async with``) and park
it on the ConnectionInfo until disconnect. Nothing ever queried it again — the
post-registration tasks only touch Redis and the socket — but SQLAlchemy autobegins
a transaction on the first SELECT, so every established connection pinned one
Postgres backend in ``idle in transaction`` for the life of the socket.

Measured impact before the fix (8 concurrent connections, live stack):
``LOCK TABLE actors IN ACCESS EXCLUSIVE MODE`` failed with "canceling statement due
to lock timeout" — i.e. an ``alembic upgrade head`` would hang forever behind it,
printing "Running upgrade ..." and nothing else. After the fix the same lock is
acquired immediately and ``idle in transaction`` stays at 0.

These are pure unit tests (no DB, no socket) so they stay fast and cannot hang —
deliberately NOT added to tests/api/test_websocket_stability.py, whose live-socket
fixtures already hang on this codebase.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.websocket_manager import WebSocketManager


@pytest.fixture
def manager() -> Iterator[WebSocketManager]:
    """A manager with an isolated connection registry (it is a singleton).

    ``register_connection`` spawns the stale-connection cleanup task, so every test
    here must run inside a loop; the task is cancelled on teardown so it does not
    leak into sibling tests.
    """
    mgr = WebSocketManager.get_instance()
    mgr._connections = {}
    yield mgr
    if mgr._cleanup_task is not None:
        mgr._cleanup_task.cancel()
        mgr._cleanup_task = None
    mgr._connections = {}


@pytest.mark.asyncio
async def test_register_connection_without_a_session(manager: WebSocketManager) -> None:
    """The endpoints register with NO session — the core of the fix.

    ``session`` must stay optional. If it ever becomes required again, the endpoints
    would have to hold one across the connection, which is exactly the leak.
    """
    conn = manager.register_connection(
        websocket=MagicMock(),
        actor_id="actor-1",
        channel="board:abc",
    )
    assert conn.session is None
    assert manager._connections[conn.connection_id] is conn


@pytest.mark.asyncio
async def test_unregister_without_session_is_clean(manager: WebSocketManager) -> None:
    """Tearing down a session-less connection must not raise or schedule a close."""
    conn = manager.register_connection(
        websocket=MagicMock(),
        actor_id="actor-1",
        channel="board:abc",
    )
    manager.unregister_connection(conn.connection_id)
    assert conn.connection_id not in manager._connections


@pytest.mark.asyncio
async def test_unregister_still_closes_a_supplied_session(
    manager: WebSocketManager,
) -> None:
    """A caller that DOES pass a session still gets it closed (compat path).

    Also pins the dropped ``if not session.is_active`` guard: ``is_active`` is True
    both for a session with no transaction AND for a closed one (it is only False in
    pending-rollback), so it skipped the close in the case that needed it most.
    A MagicMock's ``is_active`` is truthy-but-arbitrary — the close must happen
    regardless of its value.
    """
    session = AsyncMock()
    session.is_active = False  # the value that used to SKIP the close
    conn = manager.register_connection(
        websocket=MagicMock(),
        actor_id="actor-1",
        channel="board:abc",
        session=session,
    )

    manager.unregister_connection(conn.connection_id)

    # close() is scheduled as a task, not awaited (unregister is sync) — let the
    # loop run it before asserting.
    for task in list(manager._background_tasks):
        await task
    session.close.assert_awaited_once()


def test_engine_has_idle_in_transaction_timeout() -> None:
    """Server-side backstop: Postgres reaps a leaked transaction on its own.

    Defense in depth for the class of bug above — if application code ever again
    parks an open transaction, the backend is killed after 60s instead of holding
    its locks (and blocking DDL) indefinitely.
    """
    from app.db.session import CONNECT_ARGS

    server_settings = CONNECT_ARGS["server_settings"]
    assert server_settings["idle_in_transaction_session_timeout"] == "60000"
    assert server_settings["application_name"] == "project-hub-backend"
