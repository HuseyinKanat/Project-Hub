"""Audit history helpers."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TicketHistory


async def write_history(
    session: AsyncSession,
    *,
    ticket_id: UUID,
    actor_id: UUID,
    event_type: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    metadata: dict[str, Any] | None = None,
) -> TicketHistory:
    history = TicketHistory(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=event_type,
        field=field,
        old_value=old_value,
        new_value=new_value,
        event_metadata=metadata or {},
    )
    session.add(history)
    return history
