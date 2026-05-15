"""Redis pub-sub event bus for real-time ticket updates.

Event Bus Design:
- Publisher: async fire-and-forget; swallow exceptions (fail-soft)
- Subscriber: async iterator pattern for WebSocket gateway (PH-7)
- Channels: board:{board_id} (broad) + ticket:{ticket_id} (focused)
- Event ID: matches history.id for replay (PH-8)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import redis.asyncio as redis
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Standard event envelope for all pub-sub messages."""

    event_id: str  # matches TicketHistory.id for replay
    type: str  # event_type: created, updated, state_changed, claimed, released, etc.
    board_id: str
    ticket_id: str
    ticket_key: str
    actor_id: str | None
    payload: dict[str, Any]
    occurred_at: str  # ISO8601

    def to_json(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "type": self.type,
                "board_id": self.board_id,
                "ticket_id": self.ticket_id,
                "ticket_key": self.ticket_key,
                "actor_id": self.actor_id,
                "payload": self.payload,
                "occurred_at": self.occurred_at,
            },
            default=str,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> EventEnvelope:
        data = json.loads(raw)
        return cls(
            event_id=data["event_id"],
            type=data["type"],
            board_id=data["board_id"],
            ticket_id=data["ticket_id"],
            ticket_key=data["ticket_key"],
            actor_id=data.get("actor_id"),
            payload=data.get("payload", {}),
            occurred_at=data["occurred_at"],
        )


class EventBus:
    """Redis pub-sub event bus singleton.

    Usage:
        await EventBus.publish(envelope)
        async for envelope in EventBus.subscribe("board:abc"):
            ...
    """

    _redis: redis.Redis | None = None

    @classmethod
    async def _get_redis(cls) -> redis.Redis | None:
        if cls._redis is None:
            try:
                cls._redis = redis.from_url(get_settings().redis_url, decode_responses=True)
            except Exception as e:
                logger.warning("redis_connection_failed", error=str(e))
                return None
        return cls._redis

    @classmethod
    async def publish(cls, envelope: EventEnvelope) -> None:
        """Publish to both board and ticket channels. Fail-soft (logs only)."""
        try:
            r = await cls._get_redis()
            if r is None:
                return

            board_channel = f"board:{envelope.board_id}"
            ticket_channel = f"ticket:{envelope.ticket_id}"
            message = envelope.to_json()

            # Publish to both channels
            await r.publish(board_channel, message)
            await r.publish(ticket_channel, message)

            logger.debug(
                "event_published: %s %s (ticket_key=%s)",
                envelope.event_id,
                envelope.type,
                envelope.ticket_key,
            )
        except Exception as e:
            # Fail-soft: log and swallow
            logger.warning(
                "event_publish_failed: %s (event_id=%s, type=%s)",
                str(e),
                getattr(envelope, "event_id", None),
                getattr(envelope, "type", None),
            )

    @classmethod
    async def subscribe(cls, channel: str) -> AsyncIterator[EventEnvelope]:
        """Subscribe to a Redis channel and yield EventEnvelopes.

        Usage (WebSocket gateway, PH-7):
            async for envelope in EventBus.subscribe(f"board:{board_id}"):
                await websocket.send_text(envelope.to_json())
        """
        r = await cls._get_redis()
        if r is None:
            logger.error("redis_unavailable channel=%s", channel)
            return

        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(channel)
            logger.info("subscribed channel=%s", channel)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    envelope = EventEnvelope.from_json(message["data"])
                    yield envelope
                except Exception as e:
                    logger.warning("event_parse_failed error=%s", str(e))
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            logger.info("unsubscribed channel=%s", channel)

    @classmethod
    async def close(cls) -> None:
        """Close Redis connection (shutdown hook)."""
        if cls._redis is not None:
            await cls._redis.close()
            cls._redis = None
            logger.info("redis_closed")
