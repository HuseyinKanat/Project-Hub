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
from typing import Any

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConnectionInfo:
    """Track WebSocket connection state and health."""

    connection_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    websocket: WebSocket = None
    session: AsyncSession = None
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

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections = {}
            cls._instance._cleanup_task = None
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
        session: AsyncSession,
        actor_id: str,
        channel: str,
    ) -> ConnectionInfo:
        """Register new WebSocket connection for monitoring."""
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

            # Close database session if still active - check if not already closed
            if conn_info.session:
                try:
                    # Check if session is still bound and active before closing
                    if not conn_info.session.is_active:
                        logger.debug("session_already_closed: connection_id=%s", connection_id)
                    else:
                        # Schedule session cleanup without awaiting
                        asyncio.create_task(conn_info.session.close())
                        logger.debug("session_closed: connection_id=%s", connection_id)
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

    async def _cleanup_stale_connections(self) -> None:
        """Background task to cleanup stale connections (>90s without ping)."""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                current_time = time.time()
                stale_connections = []

                for conn_id, conn_info in self._connections.items():
                    time_since_ping = current_time - conn_info.last_ping_at

                    if time_since_ping > 90:  # 90 seconds timeout
                        stale_connections.append((conn_id, conn_info, time_since_ping))

                # Close stale connections
                for conn_id, conn_info, time_since_ping in stale_connections:
                    try:
                        logger.info(
                            "closing_stale_connection: connection_id=%s last_ping=%.1fs_ago",
                            conn_id,
                            time_since_ping,
                        )

                        # Close WebSocket with code 1011 (server error due to stale connection)
                        await conn_info.websocket.close(
                            code=1011,
                            reason="Connection timeout - no ping received"
                        )

                        # Unregister will handle session cleanup
                        self.unregister_connection(conn_id)

                    except Exception as e:
                        logger.warning("stale_connection_cleanup_error: %s", str(e))

                if stale_connections:
                    logger.info("cleaned_up_stale_connections: count=%d", len(stale_connections))

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
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all connections
        for conn_id in list(self._connections.keys()):
            self.unregister_connection(conn_id)

        logger.info("websocket_manager_shutdown")


# Global instance
websocket_manager = WebSocketManager.get_instance()