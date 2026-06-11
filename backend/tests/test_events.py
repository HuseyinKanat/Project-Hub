"""Tests for Redis pub-sub event bus (PH-6)."""

import pytest

from app.events.bus import EventBus, EventEnvelope


def test_event_envelope_serialization():
    """EventEnvelope round-trip JSON serialization."""
    env = EventEnvelope(
        event_id="ev-123",
        type="created",
        board_id="b-1",
        ticket_id="t-1",
        ticket_key="PH-1",
        actor_id="a-1",
        payload={"key": "value", "nested": {"x": 1}},
        occurred_at="2024-01-15T10:00:00+00:00",
    )
    raw = env.to_json()
    assert isinstance(raw, str)
    parsed = EventEnvelope.from_json(raw)
    assert parsed.event_id == env.event_id
    assert parsed.type == env.type
    assert parsed.board_id == env.board_id
    assert parsed.ticket_key == env.ticket_key
    assert parsed.payload == env.payload


@pytest.mark.anyio
async def test_event_bus_publish_swallows_exception_when_redis_unavailable():
    """EventBus.publish should fail-soft (swallow exceptions) when Redis unavailable."""
    env = EventEnvelope(
        event_id="ev-456",
        type="state_changed",
        board_id="b-1",
        ticket_id="t-1",
        ticket_key="PH-2",
        actor_id=None,
        payload={"old": "backlog", "new": "to_do"},
        occurred_at="2024-01-15T10:00:00+00:00",
    )
    # Should not raise even if Redis is unavailable
    await EventBus.publish(env)
