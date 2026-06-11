"""Event dispatch helpers for service layer integration.

Hook into ticket lifecycle services to publish events after DB commits.
Fail-soft: publish failures are logged but don't break main flow.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from app.db.models import Actor, Ticket, TicketHistory
from app.events.bus import EventBus, EventEnvelope


def _actor_id(actor: Actor | None) -> str | None:
    return str(actor.id) if actor else None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


async def publish_ticket_event(
    history: TicketHistory,
    ticket: Ticket,
    actor: Actor | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    """Publish event envelope after history row is written.

    This should be called after `await session.commit()` so history.id is valid.

    Args:
        history: The TicketHistory row just written (provides event_id, type, timestamps)
        ticket: The affected ticket (provides board_id, ticket context)
        actor: Optional actor who triggered the change
        extra_payload: Additional fields to include in payload (e.g., state change old/new)
    """
    payload: dict[str, Any] = {
        "field": history.field,
        "old_value": history.old_value,
        "new_value": history.new_value,
    }
    if extra_payload:
        payload.update(extra_payload)

    envelope = EventEnvelope(
        event_id=str(history.id),
        type=history.event_type,
        board_id=str(ticket.board_id),
        ticket_id=str(ticket.id),
        ticket_key=ticket.key,
        actor_id=_actor_id(actor),
        payload=payload,
        occurred_at=history.created_at.isoformat() if history.created_at else _now_iso(),
    )

    await EventBus.publish(envelope)


async def publish_ticket_lifecycle(
    ticket: Ticket,
    event_type: str,
    actor: Actor | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Publish event when no history row exists (rare edge cases).

    Prefer publish_ticket_event when history exists (replay consistency).
    """
    from uuid import uuid4

    envelope = EventEnvelope(
        event_id=str(uuid4()),
        type=event_type,
        board_id=str(ticket.board_id),
        ticket_id=str(ticket.id),
        ticket_key=ticket.key,
        actor_id=_actor_id(actor),
        payload=payload or {},
        occurred_at=_now_iso(),
    )

    await EventBus.publish(envelope)
