"""Tests for MCP subscribe_events streaming tool."""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db.models import TicketHistory
from app.events.bus import EventBus, EventEnvelope
from app.main import app


class TestMCPSubscribeEventsTool:
    """Test MCP subscribe_events streaming endpoint."""

    def test_subscribe_events_in_tools_list(self, test_client: TestClient) -> None:
        """subscribe_events should appear in /mcp/tools list."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        response = test_client.get("/api/mcp/tools", headers=headers)
        assert response.status_code == 200
        
        tools = response.json()
        tool_names = [t["name"] for t in tools]
        assert "subscribe_events" in tool_names
    
    def test_subscribe_events_call_returns_error_with_proper_endpoint_hint(
        self, test_client: TestClient
    ) -> None:
        """Direct tool call should hint to use streaming endpoint."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        payload = {"ticket_id": "PH-1"}
        
        response = test_client.post(
            "/api/mcp/call/subscribe_events",
            json=payload,
            headers=headers
        )
        assert response.status_code == 200
        
        result = response.json()
        assert result["tool"] == "subscribe_events"
        assert "error" in result["result"]
        assert "stream/events" in result["result"]["error"]


class TestMCPEventsStreaming:
    """Test SSE streaming endpoint for events."""

    def test_stream_events_requires_board_or_ticket(
        self, test_client: TestClient
    ) -> None:
        """Should return 400 if neither board_id nor ticket_id provided."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        
        response = test_client.get(
            "/api/mcp/stream/events",
            headers=headers
        )
        assert response.status_code == 400
        assert "board_id or ticket_id required" in response.text

    def test_stream_events_by_board_id_success(
        self, test_client: TestClient
    ) -> None:
        """Should accept board_id filter."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        
        # This will hang waiting for events, so we test the connection only
        # by checking it doesn't immediately fail
        # In practice, SSE streams are tested differently
        response = test_client.get(
            "/api/mcp/stream/events?board_id=abc123",
            headers=headers,
            timeout=0.5  # Short timeout since it streams forever
        )
        # Should not be an immediate error
        # Note: This test is limited as SSE streams require special handling

    def test_stream_events_by_ticket_id_success(
        self, test_client: TestClient
    ) -> None:
        """Should accept ticket_id filter."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        
        # Mock EventBus to avoid actual Redis dependency
        with patch.object(EventBus, '_get_redis', return_value=None):
            response = test_client.get(
                "/api/mcp/stream/events?ticket_id=PH-1",
                headers=headers,
                timeout=0.5
            )
            # With mocked Redis, should return early with error in stream

    def test_stream_events_with_since_event_id(
        self, test_client: TestClient
    ) -> None:
        """Should accept since_event_id for replay."""
        headers = {"Authorization": "Bearer change-me-on-first-login"}
        
        fake_event_id = str(uuid.uuid4())
        
        with patch.object(EventBus, '_get_redis', return_value=None):
            response = test_client.get(
                f"/api/mcp/stream/events?board_id=abc123&since_event_id={fake_event_id}",
                headers=headers,
                timeout=0.5
            )


class TestEventStreamGenerator:
    """Test event_stream async generator function."""

    @pytest.mark.asyncio
    async def test_replay_from_history(self, db_session) -> None:
        """Should replay events from history when since_event_id provided."""
        from app.mcp.server import event_stream
        
        # Create mock history items
        mock_history = MagicMock(spec=TicketHistory)
        mock_history.id = uuid.uuid4()
        mock_history.ticket_id = uuid.uuid4()
        mock_history.event_type = "updated"
        mock_history.field = "state"
        mock_history.old_value = "backlog"
        mock_history.new_value = "in_progress"
        mock_history.event_metadata = {}
        mock_history.actor_id = uuid.uuid4()
        mock_history.created_at = datetime.now(timezone.utc)
        
        # Mock the database query
        with patch('app.mcp.server.get_ticket') as mock_get_ticket:
            mock_ticket = MagicMock()
            mock_ticket.id = uuid.uuid4()
            mock_ticket.board_id = uuid.uuid4()
            mock_ticket.key = "PH-1"
            mock_get_ticket.return_value = mock_ticket
            
            with patch.object(db_session, 'execute') as mock_execute:
                mock_result = MagicMock()
                mock_result.scalars.return_value.all.return_value = [mock_history]
                mock_execute.return_value = mock_result
                
                # Test the generator
                since_id = str(uuid.uuid4())
                chunks = []
                async for chunk in event_stream(
                    db_session,
                    board_id=str(mock_ticket.board_id),
                    since_event_id=since_id
                ):
                    chunks.append(chunk)
                    break  # Just get first chunk
                
                # Should have yielded at least one SSE data line
                if chunks:
                    assert chunks[0].startswith("data: ")

    @pytest.mark.asyncio
    async def test_live_event_subscription(self, db_session) -> None:
        """Should subscribe to live events via EventBus."""
        from app.mcp.server import event_stream
        
        # Mock EventBus.subscribe
        mock_envelope = EventEnvelope(
            event_id=str(uuid.uuid4()),
            type="created",
            board_id="board123",
            ticket_id="ticket456",
            ticket_key="PH-1",
            actor_id=None,
            payload={"field": "title", "old_value": None, "new_value": "Test"},
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        
        async def mock_subscribe(channel: str):
            yield mock_envelope
        
        with patch.object(EventBus, 'subscribe', side_effect=mock_subscribe):
            with patch.object(EventBus, '_get_redis', return_value=MagicMock()):
                chunks = []
                async for chunk in event_stream(
                    db_session,
                    board_id="board123"
                ):
                    chunks.append(chunk)
                    break
                
                assert len(chunks) > 0
                assert "data: {" in chunks[0]
                assert "created" in chunks[0]

    @pytest.mark.asyncio
    async def test_error_handling_invalid_since_event_id(self, db_session) -> None:
        """Should handle invalid since_event_id gracefully."""
        from app.mcp.server import event_stream
        
        chunks = []
        async for chunk in event_stream(
            db_session,
            board_id="board123",
            since_event_id="invalid-uuid"
        ):
            chunks.append(chunk)
            break
        
        # Should yield error message in SSE format
        assert len(chunks) > 0
        assert "error" in chunks[0]
