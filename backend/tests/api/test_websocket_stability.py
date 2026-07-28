"""WebSocket stability tests (PH-41).

Tests for:
- Database session lifecycle management
- Ping-pong heartbeat mechanism
- Redis retry logic and graceful degradation
- Connection health monitoring
- Structured error responses
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket
import redis.exceptions

from app.core.websocket_manager import websocket_manager
from app.events.bus import EventBus, EventEnvelope


class TestWebSocketStability:
    """Test WebSocket stability improvements from PH-41."""

    @pytest.fixture
    def mock_session(self):
        """Mock database session."""
        session = AsyncMock()
        session.close = AsyncMock()
        return session

    @pytest.fixture
    def mock_actor(self):
        """Mock authenticated actor."""
        actor = MagicMock()
        actor.id = "test-actor-id"
        return actor

    @pytest.fixture
    def mock_board(self):
        """Mock board for access control."""
        board = MagicMock()
        board.id = "test-board-id"
        return board

    @pytest.fixture
    def sample_event_envelope(self):
        """Sample event envelope for testing."""
        return EventEnvelope(
            event_id="test-event-1",
            type="ticket_updated",
            board_id="test-board-id",
            ticket_id="test-ticket-id",
            ticket_key="TEST-1",
            actor_id="test-actor-id",
            payload={"field": "status", "value": "in_progress"},
            occurred_at="2026-05-20T19:00:00Z"
        )

    async def test_websocket_manager_connection_lifecycle(self, mock_session, mock_actor):
        """Test WebSocket manager connection registration and cleanup."""
        # Create mock websocket
        mock_websocket = MagicMock(spec=WebSocket)

        # Register connection
        conn_info = websocket_manager.register_connection(
            websocket=mock_websocket,
            session=mock_session,
            actor_id=str(mock_actor.id),
            channel="board:test-board-id"
        )

        # Verify connection is registered
        assert conn_info.connection_id in websocket_manager._connections
        assert websocket_manager._connections[conn_info.connection_id].actor_id == str(mock_actor.id)
        assert websocket_manager._connections[conn_info.connection_id].channel == "board:test-board-id"

        # Test ping handling
        with patch.object(mock_websocket, 'send_text', new_callable=AsyncMock) as mock_send:
            success = await websocket_manager.handle_ping(conn_info.connection_id, mock_websocket)

            assert success is True
            mock_send.assert_called_once()

            # Verify pong message format
            call_args = mock_send.call_args[0][0]
            pong_data = json.loads(call_args)
            assert pong_data["type"] == "pong"
            assert pong_data["connection_id"] == conn_info.connection_id
            assert "timestamp" in pong_data

        # Test connection cleanup
        websocket_manager.unregister_connection(conn_info.connection_id)
        assert conn_info.connection_id not in websocket_manager._connections
        mock_session.close.assert_called_once()

    async def test_ping_pong_mechanism(self):
        """Test ping-pong heartbeat mechanism."""
        from app.api.websocket import _handle_client_message

        mock_websocket = MagicMock(spec=WebSocket)
        connection_id = "test-connection-id"

        # Mock websocket manager
        with patch.object(websocket_manager, 'handle_ping', new_callable=AsyncMock) as mock_handle_ping:
            mock_handle_ping.return_value = True

            # Test valid ping message
            ping_message = json.dumps({"type": "ping"})
            await _handle_client_message(connection_id, mock_websocket, ping_message)

            mock_handle_ping.assert_called_once_with(connection_id, mock_websocket)

    async def test_redis_retry_logic(self):
        """Test EventBus Redis retry logic and exponential backoff."""
        channel = "board:test-board"

        # Mock Redis failures then success
        with patch.object(EventBus, '_get_redis') as mock_get_redis:
            # First few attempts fail
            mock_get_redis.side_effect = [
                None,  # First attempt fails
                None,  # Second attempt fails
                MagicMock()  # Third attempt succeeds
            ]

            # Mock successful pubsub after retries
            mock_redis = MagicMock()
            mock_pubsub = AsyncMock()
            mock_pubsub.listen.return_value = [
                {"type": "message", "data": json.dumps({
                    "event_id": "test-1",
                    "type": "test_event",
                    "board_id": "test-board",
                    "ticket_id": "test-ticket",
                    "ticket_key": "TEST-1",
                    "actor_id": "test-actor",
                    "payload": {},
                    "occurred_at": "2026-05-20T19:00:00Z"
                })}
            ]
            mock_redis.pubsub.return_value = mock_pubsub

            # Use a timeout to prevent infinite loop in tests
            with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                async def collect_events():
                    events = []
                    async for envelope in EventBus.subscribe(channel):
                        events.append(envelope)
                        if len(events) >= 1:  # Collect one event then stop
                            break
                    return events

                # This should retry and eventually succeed
                events = await collect_events()

                # Should have received degradation message and then real event
                assert len(events) >= 1
                # First event might be degradation message. Wording is asserted
                # against bus.py's actual payload — PH-44 reworded it and these
                # tests were never updated (they expected the pre-PH-44 string).
                if events[0].type == "system_degradation":
                    assert "WebSocket service degraded" in events[0].payload["message"]

    async def test_graceful_degradation_on_max_retries(self):
        """Test graceful degradation when max Redis retries exceeded."""
        channel = "board:test-board"

        # Mock Redis always failing
        with patch.object(EventBus, '_get_redis', side_effect=redis.exceptions.ConnectionError("Redis unavailable")):
            with patch('asyncio.sleep', new_callable=AsyncMock):
                # Collect first degradation message
                async def collect_degradation():
                    async for envelope in EventBus.subscribe(channel):
                        return envelope

                degradation_envelope = await collect_degradation()

                assert degradation_envelope.type == "system_degradation"
                # Asserted against bus.py's actual payload. Two drifts were fixed
                # here: PH-44 reworded `message`, and the old expectation of a
                # `reason` key was pure fiction — the envelope emits
                # message/retry_count/error and never had `reason`, so this
                # assertion could only ever KeyError.
                assert (
                    degradation_envelope.payload["message"]
                    == "WebSocket service degraded - Redis unavailable"
                )
                assert "retry_count" in degradation_envelope.payload
                assert "Redis unavailable" in degradation_envelope.payload["error"]

    async def test_stale_connection_cleanup(self, mock_session, mock_actor):
        """Test automatic cleanup of stale connections."""
        mock_websocket = AsyncMock(spec=WebSocket)
        mock_websocket.close = AsyncMock()

        # Register connection
        conn_info = websocket_manager.register_connection(
            websocket=mock_websocket,
            session=mock_session,
            actor_id=str(mock_actor.id),
            channel="board:test-board"
        )

        # Simulate stale connection (>90s without ping)
        import time
        websocket_manager._connections[conn_info.connection_id].last_ping_at = time.time() - 100

        # Manually trigger ONE cleanup pass. This used to await
        # _cleanup_stale_connections(), which is a `while True` daemon loop that
        # never returns — the test hung forever and took the whole suite with it.
        swept = await websocket_manager.sweep_stale_connections()
        assert swept == 1

        # Verify stale connection was closed
        mock_websocket.close.assert_called_once_with(
            code=1011,
            reason="Connection timeout - no ping received"
        )

        # Verify connection was unregistered
        assert conn_info.connection_id not in websocket_manager._connections

    async def test_connection_stats(self, mock_session, mock_actor):
        """Test connection statistics for monitoring."""
        mock_websocket = MagicMock(spec=WebSocket)

        # Register multiple connections
        conn1 = websocket_manager.register_connection(
            websocket=mock_websocket,
            session=mock_session,
            actor_id=str(mock_actor.id) + "1",
            channel="board:test-board-1"
        )

        conn2 = websocket_manager.register_connection(
            websocket=mock_websocket,
            session=mock_session,
            actor_id=str(mock_actor.id) + "2",
            channel="board:test-board-2"
        )

        # Get stats
        stats = websocket_manager.get_connection_stats()

        assert stats["active_connections"] == 2
        assert len(stats["connections"]) == 2

        # Verify connection details
        conn_stats = {c["connection_id"]: c for c in stats["connections"]}
        assert conn1.connection_id in conn_stats
        assert conn2.connection_id in conn_stats
        assert conn_stats[conn1.connection_id]["actor_id"] == str(mock_actor.id) + "1"
        assert conn_stats[conn2.connection_id]["actor_id"] == str(mock_actor.id) + "2"

        # Cleanup
        websocket_manager.unregister_connection(conn1.connection_id)
        websocket_manager.unregister_connection(conn2.connection_id)

    async def test_structured_error_responses(self, mock_session):
        """Test structured error responses for authentication failures."""
        from app.api.websocket import _authenticate_websocket

        mock_websocket = AsyncMock(spec=WebSocket)
        mock_websocket.query_params = {"token": "invalid-token"}

        # Mock authentication failure. Patch target is the name bound INSIDE
        # app.api.websocket — that module does `from app.services.actors import
        # get_actor_from_token`, so patching the source module rebinds a name the
        # endpoint never reads and the mock silently does nothing (which is why
        # `close` was called 0 times here).
        with patch(
            'app.api.websocket.get_actor_from_token',
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(Exception):  # Should raise WebSocketDisconnect
                await _authenticate_websocket(mock_websocket, mock_session)

            # Should have called close with policy violation
            mock_websocket.close.assert_called_once_with(
                code=1008,
                reason="Invalid token"
            )

    async def test_websocket_releases_db_session_before_long_lived_phase(
        self, mock_actor, mock_board
    ):
        """PH-331: the endpoint must RELEASE its session before serving the socket.

        This INVERTS what this test used to assert ("session remains active
        throughout the WebSocket connection"). Holding it was the bug: nothing
        queried the session after authorization, but its autobegun transaction kept
        a Postgres backend `idle in transaction` for the socket's lifetime —
        exhausting the connection pool and blocking DDL (an alembic DROP INDEX on
        `actors` hung behind it indefinitely).

        Patch targets matter here. `get_actor_from_token` and `SessionLocal` are
        bound at module scope in app.api.websocket, so they must be patched THERE;
        `get_board` / `require_permission` are imported inside the function body, so
        patching their source module works. The original test patched the source
        module for `get_actor_from_token`, which silently did nothing.
        """
        from app.api.websocket import websocket_board_endpoint

        mock_websocket = AsyncMock(spec=WebSocket)
        mock_websocket.accept = AsyncMock()
        mock_websocket.query_params = {"token": "valid-token"}

        session = AsyncMock()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch('app.api.websocket.SessionLocal', return_value=session_cm), \
             patch('app.api.websocket.get_actor_from_token',
                   new_callable=AsyncMock, return_value=mock_actor), \
             patch('app.services.boards.get_board',
                   new_callable=AsyncMock, return_value=mock_board), \
             patch('app.core.permissions.require_permission'), \
             patch('app.api.websocket._run_connection_tasks',
                   new_callable=AsyncMock) as mock_run_tasks, \
             patch.object(websocket_manager, 'register_connection',
                          wraps=websocket_manager.register_connection) as spy_register:

            await websocket_board_endpoint(mock_websocket, "test-board-id")

            # The session context exited — i.e. the session was closed/rolled back —
            # and it did so BEFORE the long-lived phase began.
            session_cm.__aexit__.assert_awaited_once()
            assert mock_run_tasks.await_count == 1

            # And the connection was registered WITHOUT a session, so nothing can
            # hold a transaction open for the life of the socket.
            assert spy_register.call_args.kwargs.get("session") is None

    async def test_message_count_tracking(self, mock_session, mock_actor):
        """Test message count tracking for connection health."""
        mock_websocket = MagicMock(spec=WebSocket)

        conn_info = websocket_manager.register_connection(
            websocket=mock_websocket,
            session=mock_session,
            actor_id=str(mock_actor.id),
            channel="board:test-board"
        )

        initial_count = conn_info.message_count

        # Update message count
        websocket_manager.update_message_count(conn_info.connection_id)
        websocket_manager.update_message_count(conn_info.connection_id)

        # Verify count increased
        assert websocket_manager._connections[conn_info.connection_id].message_count == initial_count + 2

        # Cleanup
        websocket_manager.unregister_connection(conn_info.connection_id)

    async def test_concurrent_websocket_connections(self, mock_session, mock_actor):
        """Test multiple concurrent WebSocket connections don't interfere."""
        connections = []

        # Create multiple connections
        for i in range(3):
            mock_websocket = MagicMock(spec=WebSocket)
            mock_websocket.send_text = AsyncMock()

            conn_info = websocket_manager.register_connection(
                websocket=mock_websocket,
                session=mock_session,
                actor_id=f"{mock_actor.id}-{i}",
                channel=f"board:test-board-{i}"
            )
            connections.append(conn_info)

        # Verify all connections are independent
        assert len(websocket_manager._connections) >= 3

        # Test ping handling for each
        for conn in connections:
            success = await websocket_manager.handle_ping(conn.connection_id, conn.websocket)
            assert success is True

        # Cleanup
        for conn in connections:
            websocket_manager.unregister_connection(conn.connection_id)

    def test_websocket_manager_singleton(self):
        """Test WebSocket manager is properly implemented as singleton."""
        manager1 = websocket_manager
        manager2 = websocket_manager.get_instance()

        assert manager1 is manager2
        assert id(manager1) == id(manager2)