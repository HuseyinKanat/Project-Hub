"""WebSocket connection health monitoring and management (PH-41).

Handles:
- Connection lifecycle tracking
- Ping-pong heartbeat monitoring
- Automatic cleanup of stale connections
- Connection metrics and logging
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# How often the cleanup daemon wakes, and how long a connection may go without a
# ping before it is considered stale. Named because the sweep behavior is now
# directly testable (see sweep_stale_connections).
CLEANUP_INTERVAL_SECONDS = 30
STALE_CONNECTION_TIMEOUT_SECONDS = 90


@dataclass
class ConnectionInfo:
    """Track WebSocket connection state and health."""

    connection_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    websocket: WebSocket | None = None
    session: AsyncSession | None = None
    actor_id: str | None = None
    channel: str = ""
    connected_at: float = field(default_factory=time.time)
    last_ping_at: float = field(default_factory=time.time)
    last_pong_at: float = field(default_factory=time.time)
    ping_count: int = 0
    message_count: int = 0


class WebSocketManager:
    """Singleton WebSocket connection manager."""

    _instance = None
    _connections: dict[str, ConnectionInfo] = {}
    _cleanup_task: asyncio.Task | None = None
    # Strong references to fire-and-forget tasks (e.g. session close) so the
    # event loop's weak reference can't let them be GC'd mid-flight (S7502).
    _background_tasks: ClassVar[set[asyncio.Task[Any]]] = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections = {}
            cls._instance._cleanup_task = None
            # _background_tasks is a ClassVar (singleton-shared); no per-instance
            # reset needed — it stays a single strong-reference registry.
        return cls._instance

    @classmethod
    def get_instance(cls) -> WebSocketManager:
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_connection(
        self,
        websocket: WebSocket,
        actor_id: str,
        channel: str,
        session: AsyncSession | None = None,
    ) -> ConnectionInfo:
        """Register new WebSocket connection for monitoring.

        PH-331: ``session`` is now OPTIONAL and the endpoints pass nothing. A
        connection no longer owns a DB session — holding one across the socket's
        lifetime left a transaction open (see api/websocket.py). The parameter is
        retained (moved last, defaulting to None) so any caller that still supplies
        a session keeps its close-on-unregister behavior rather than silently
        leaking it.
        """
        conn_info = ConnectionInfo(
            websocket=websocket,
            session=session,
            actor_id=actor_id,
            channel=channel,
        )

        self._connections[conn_info.connection_id] = conn_info

        logger.info(
            "ws_connection_registered: connection_id=%s actor_id=%s channel=%s",
            conn_info.connection_id,
            actor_id,
            channel,
        )

        # Start cleanup task if not running
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_stale_connections())

        return conn_info

    def unregister_connection(self, connection_id: str) -> None:
        """Remove connection from monitoring."""
        if connection_id in self._connections:
            conn_info = self._connections.pop(connection_id)

            # Close the session only if a caller supplied one. PH-331: the endpoints
            # no longer do, so this is a compatibility path for any other caller.
            #
            # The old `if not session.is_active` guard was dropped: `is_active` is
            # NOT an "is open" flag — it is True both for a session with no
            # transaction AND for an already-closed one, and only False in
            # pending-rollback state. So it skipped the close in the one case that
            # most needed it and never actually detected "already closed".
            # `close()` is idempotent, so calling it unconditionally is correct.
            if conn_info.session is not None:
                try:
                    # Scheduled, not awaited — this method is sync. Retain a strong
                    # reference (S7502) so the task isn't GC'd mid-flight; discard on
                    # done. Caveat: during loop shutdown a scheduled task may never
                    # run, which is precisely why the endpoints now scope the session
                    # with `async with` instead of relying on this.
                    close_task = asyncio.create_task(conn_info.session.close())
                    self._background_tasks.add(close_task)
                    close_task.add_done_callback(self._background_tasks.discard)
                    logger.debug("session_close_scheduled: connection_id=%s", connection_id)
                except Exception as e:
                    logger.warning(
                        "session_close_error: connection_id=%s error=%s", connection_id, str(e)
                    )

            session_duration = time.time() - conn_info.connected_at
            logger.info(
                "ws_connection_unregistered: connection_id=%s duration=%.2fs messages=%d pings=%d",
                connection_id,
                session_duration,
                conn_info.message_count,
                conn_info.ping_count,
            )

    async def handle_ping(self, connection_id: str, websocket: WebSocket) -> bool:
        """Handle ping message and send pong response."""
        if connection_id not in self._connections:
            logger.warning("ping_from_unknown_connection: connection_id=%s", connection_id)
            return False

        conn_info = self._connections[connection_id]
        conn_info.last_ping_at = time.time()
        conn_info.ping_count += 1

        # Send pong response
        pong_message = {
            "type": "pong",
            "timestamp": conn_info.last_ping_at,
            "connection_id": connection_id,
        }

        try:
            await websocket.send_text(json.dumps(pong_message))
            conn_info.last_pong_at = time.time()

            # Log ping-pong latency for monitoring
            latency_ms = (conn_info.last_pong_at - conn_info.last_ping_at) * 1000
            logger.debug(
                "ping_pong_success: connection_id=%s latency=%.2fms",
                connection_id,
                latency_ms,
            )
            return True

        except Exception as e:
            logger.warning(
                "pong_send_failed: connection_id=%s error=%s",
                connection_id,
                str(e),
            )
            return False

    def update_message_count(self, connection_id: str) -> None:
        """Track message activity for connection health."""
        if connection_id in self._connections:
            self._connections[connection_id].message_count += 1

    async def sweep_stale_connections(self) -> int:
        """Run ONE cleanup pass; return how many stale connections were closed.

        Split out of the ``_cleanup_stale_connections`` daemon loop below so this
        behavior can be awaited directly. Awaiting the loop never returns (it is a
        ``while True``), so a caller that treats it as a one-shot — as
        ``test_stale_connection_cleanup`` did — hangs forever.

        The scan is materialized into ``stale`` BEFORE any close, because
        ``unregister_connection`` mutates ``_connections`` (mutating it while
        iterating ``.items()`` would raise RuntimeError).
        """
        current_time = time.time()
        stale = [
            (conn_id, conn_info, current_time - conn_info.last_ping_at)
            for conn_id, conn_info in self._connections.items()
            if current_time - conn_info.last_ping_at > STALE_CONNECTION_TIMEOUT_SECONDS
        ]

        for conn_id, conn_info, time_since_ping in stale:
            try:
                logger.info(
                    "closing_stale_connection: connection_id=%s last_ping=%.1fs_ago",
                    conn_id,
                    time_since_ping,
                )

                # Close WebSocket with code 1011 (server error due to stale connection)
                if conn_info.websocket is not None:
                    await conn_info.websocket.close(
                        code=1011,
                        reason="Connection timeout - no ping received"
                    )

                # Unregister will handle session cleanup
                self.unregister_connection(conn_id)

            except Exception as e:
                logger.warning("stale_connection_cleanup_error: %s", str(e))

        if stale:
            logger.info("cleaned_up_stale_connections: count=%d", len(stale))
        return len(stale)

    async def _cleanup_stale_connections(self) -> None:
        """Background daemon: sweep stale connections forever. Never returns.

        Call ``sweep_stale_connections()`` instead if you want a single pass.
        """
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                await self.sweep_stale_connections()
            except Exception as e:
                logger.error("cleanup_task_error: %s", str(e))
                await asyncio.sleep(5)  # Back off on error

    def get_connection_stats(self) -> dict[str, Any]:
        """Get current connection statistics for monitoring."""
        current_time = time.time()
        stats = {
            "active_connections": len(self._connections),
            "connections": [],
        }

        for conn_id, conn_info in self._connections.items():
            connection_stats = {
                "connection_id": conn_id,
                "actor_id": conn_info.actor_id,
                "channel": conn_info.channel,
                "duration": current_time - conn_info.connected_at,
                "last_ping_ago": current_time - conn_info.last_ping_at,
                "ping_count": conn_info.ping_count,
                "message_count": conn_info.message_count,
            }
            stats["connections"].append(connection_stats)

        return stats

    async def shutdown(self) -> None:
        """Shutdown manager and close all connections."""
        if self._cleanup_task:
            # gather(return_exceptions=True) absorbs the deliberately-cancelled
            # task's CancelledError without a broad `except CancelledError` that
            # would swallow our own cancellation.
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)

        # Close all connections
        for conn_id in list(self._connections.keys()):
            self.unregister_connection(conn_id)

        logger.info("websocket_manager_shutdown")


# Global instance
websocket_manager = WebSocketManager.get_instance()