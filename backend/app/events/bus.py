"""Redis pub-sub event bus for real-time ticket updates.

Event Bus Design:
- Publisher: async fire-and-forget; swallow exceptions (fail-soft)
- Subscriber: async iterator pattern for WebSocket gateway (PH-7)
- Channels: board:{board_id} (broad) + ticket:{ticket_id} (focused)
- Event ID: matches history.id for replay (PH-8)
"""

from __future__ import annotations

import asyncio
import json
import random
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
                cls._redis = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
                # Test the connection
                await cls._redis.ping()
            except Exception as e:
                logger.warning("redis_connection_failed", error=str(e))
                cls._redis = None
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
        """Subscribe to a Redis channel and yield EventEnvelopes with retry logic.

        Usage (WebSocket gateway, PH-7):
            async for envelope in EventBus.subscribe(f"board:{board_id}"):
                await websocket.send_text(envelope.to_json())

        Features:
        - Exponential backoff retry: 1s, 2s, 4s, 8s, 15s max
        - Graceful degradation when Redis unavailable
        - Comprehensive error logging for debugging
        """
        retry_delays = [1, 2, 4, 8, 15]  # seconds, exponential backoff with 15s max
        retry_count = 0

        logger.info("subscribe_start channel=%s", channel)

        while True:
            try:
                r = await cls._get_redis()
                if r is None:
                    logger.error("redis_unavailable channel=%s", channel)
                    raise ConnectionError("Redis connection failed")

                pubsub = r.pubsub()
                try:
                    await pubsub.subscribe(channel)
                    logger.info("subscribed channel=%s retry_count=%d", channel, retry_count)
                    retry_count = 0  # Reset on successful connection

                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            envelope = EventEnvelope.from_json(message["data"])
                            yield envelope
                        except Exception as e:
                            logger.warning("event_parse_failed error=%s", str(e))

                except Exception as e:
                    logger.warning("redis_pubsub_error channel=%s error=%s", channel, str(e))
                    raise
                finally:
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.close()
                    except Exception as e:
                        logger.warning("pubsub_cleanup_error: %s", str(e))

            except Exception as e:
                if retry_count >= len(retry_delays):
                    # Max retries exceeded - enter graceful degradation
                    logger.error(
                        "redis_subscribe_failed_max_retries: channel=%s retries=%d error=%s",
                        channel,
                        retry_count,
                        str(e)
                    )

                    # Yield graceful degradation message
                    degradation_envelope = EventEnvelope(
                        event_id="degradation",
                        type="system_degradation",
                        board_id=channel.split(":")[-1] if ":" in channel else "unknown",
                        ticket_id="",
                        ticket_key="",
                        actor_id=None,
                        payload={
                            "message": "Real-time updates temporarily unavailable",
                            "reason": "Redis connection failed",
                            "retry_count": retry_count,
                            "error": str(e)
                        },
                        occurred_at="",  # Will be set by frontend
                    )
                    yield degradation_envelope

                    # Long sleep before attempting again
                    await asyncio.sleep(30)
                    retry_count = 0  # Reset for next cycle
                    continue

                # Calculate delay with jitter to prevent thundering herd
                delay = retry_delays[retry_count] + random.uniform(0, 1)
                retry_count += 1

                logger.warning(
                    "redis_subscribe_retry: channel=%s attempt=%d delay=%.1fs error=%s",
                    channel,
                    retry_count,
                    delay,
                    str(e)
                )

                await asyncio.sleep(delay)

    @classmethod
    async def close(cls) -> None:
        """Close Redis connection (shutdown hook)."""
        if cls._redis is not None:
            await cls._redis.close()
            cls._redis = None
            logger.info("redis_closed")
