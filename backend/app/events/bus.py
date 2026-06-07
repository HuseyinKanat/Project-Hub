"""Event bus for real-time updates via Redis pub/sub."""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

import redis.asyncio as redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EventEnvelope:
    """Standardized event envelope."""

    event_id: str
    type: str  # e.g., "created", "updated", "deleted", "assigned"
    board_id: str
    ticket_id: str
    ticket_key: str
    actor_id: str | None
    payload: dict[str, Any]
    occurred_at: str

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
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
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
    """Redis pub-sub event bus singleton."""

    _redis: redis.Redis | None = None
    _initialized: bool = False

    @classmethod
    async def _get_redis(cls) -> redis.Redis | None:
        """Get or create Redis connection."""
        if cls._redis is None:
            try:
                # Create new Redis connection
                cls._redis = await redis.from_url(
                    get_settings().redis_url,
                    decode_responses=False,  # Handle bytes ourselves
                    socket_keepalive=True,
                    # Remove socket_keepalive_options as they're causing issues
                )
                # Test the connection
                await cls._redis.ping()
                logger.info("redis_connection_established")
            except Exception as e:
                logger.error("redis_connection_failed: %s", str(e))
                cls._redis = None
        return cls._redis

    @classmethod
    async def publish(cls, envelope: EventEnvelope) -> None:
        """Publish to both board and ticket channels."""
        try:
            r = await cls._get_redis()
            if r is None:
                logger.warning("redis_unavailable_for_publish")
                return

            board_channel = f"board:{envelope.board_id}"
            ticket_channel = f"ticket:{envelope.ticket_id}"
            message = envelope.to_json()

            # Convert string to bytes for publishing
            message_bytes = message.encode('utf-8')

            # Publish to both channels
            await r.publish(board_channel, message_bytes)
            await r.publish(ticket_channel, message_bytes)

            logger.debug(
                "event_published: %s %s (ticket_key=%s)",
                envelope.event_id,
                envelope.type,
                envelope.ticket_key,
            )
        except Exception as e:
            logger.warning(
                "event_publish_failed: %s (event_id=%s, type=%s)",
                str(e),
                getattr(envelope, "event_id", None),
                getattr(envelope, "type", None),
            )

    @classmethod
    async def subscribe(cls, channel: str) -> AsyncIterator[EventEnvelope]:
        """Subscribe to a Redis channel and yield EventEnvelopes.

        This generator will:
        1. Connect to Redis
        2. Subscribe to the channel
        3. Yield events as they arrive
        4. Handle reconnection with exponential backoff
        """
        retry_delays = [1, 2, 4, 8, 15]  # seconds
        retry_count = 0

        logger.info("subscribe_start channel=%s", channel)

        while True:
            pubsub = None
            try:
                # Get Redis connection
                r = await cls._get_redis()
                if r is None:
                    raise ConnectionError("Redis connection failed")

                # Create pubsub instance
                pubsub = r.pubsub()

                # Subscribe to channel
                await pubsub.subscribe(channel)
                logger.info("subscribed channel=%s retry_count=%d", channel, retry_count)

                # Confirm subscription is active
                logger.info("subscription_confirmed channel=%s", channel)

                # Reset retry count on successful connection
                retry_count = 0

                # Listen for messages
                while True:
                    try:
                        # Use get_message with timeout instead of listen()
                        # This allows us to check connection health periodically
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=30.0
                        )

                        if message is None:
                            # Timeout - send a ping to keep connection alive
                            logger.debug("pubsub_keepalive channel=%s", channel)
                            continue

                        if message["type"] == "message":
                            try:
                                # Parse the message
                                data = message["data"]
                                if isinstance(data, bytes):
                                    data = data.decode('utf-8')

                                envelope = EventEnvelope.from_json(data)
                                logger.debug(
                                    "envelope_yielding event_id=%s type=%s",
                                    envelope.event_id, envelope.type
                                )
                                yield envelope

                            except Exception as e:
                                logger.warning(
                                    "event_parse_failed error=%s data=%s",
                                    str(e), message.get("data")
                                )

                    except asyncio.TimeoutError:
                        # This is expected - just continue
                        continue

            except asyncio.CancelledError:
                # Cooperative cancellation: log, then re-raise so the cancel
                # propagates (the finally below still runs pubsub cleanup).
                logger.info("subscribe_cancelled channel=%s", channel)
                raise

            except Exception as e:
                logger.warning("redis_pubsub_error channel=%s error=%s", channel, str(e))

                # Cleanup pubsub if it exists
                if pubsub:
                    try:
                        await pubsub.unsubscribe(channel)
                        await pubsub.aclose()
                    except Exception as cleanup_e:
                        logger.warning("pubsub_cleanup_error: %s", str(cleanup_e))

                # Retry logic
                if retry_count >= len(retry_delays):
                    logger.error(
                        "redis_subscribe_max_retries channel=%s retries=%d",
                        channel, retry_count
                    )

                    # Yield system degradation event
                    degradation_envelope = EventEnvelope(
                        event_id="system-degradation",
                        type="system_degradation",
                        board_id="system",
                        ticket_id="system",
                        ticket_key="SYSTEM",
                        actor_id=None,
                        payload={
                            "message": "WebSocket service degraded - Redis unavailable",
                            "retry_count": retry_count,
                            "error": str(e)
                        },
                        occurred_at=datetime.utcnow().isoformat(),
                    )
                    yield degradation_envelope

                    # Long sleep before attempting again
                    await asyncio.sleep(30)
                    retry_count = 0  # Reset for next cycle
                    continue

                # Calculate delay with jitter
                delay = retry_delays[retry_count] + random.uniform(0, 1)
                retry_count += 1

                logger.warning(
                    "redis_subscribe_retry channel=%s attempt=%d delay=%.1fs",
                    channel, retry_count, delay
                )

                await asyncio.sleep(delay)

            finally:
                # Ensure pubsub cleanup
                if pubsub:
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass

    @classmethod
    async def startup(cls) -> None:
        """Initialize Redis connection."""
        if cls._initialized:
            logger.debug("redis_already_initialized")
            return

        try:
            # Force new connection
            cls._redis = None
            r = await cls._get_redis()
            if r is not None:
                cls._initialized = True
                logger.info("redis_startup: EventBus ready")
            else:
                logger.error("redis_startup: EventBus failed to connect")
        except Exception as e:
            logger.error("redis_startup_error: %s", str(e))

    @classmethod
    async def cleanup(cls) -> None:
        """Close Redis connection."""
        if cls._redis is not None:
            try:
                await cls._redis.aclose()
                logger.info("redis_closed")
            except Exception as e:
                logger.warning("redis_cleanup_error: %s", str(e))
            finally:
                cls._redis = None
                cls._initialized = False