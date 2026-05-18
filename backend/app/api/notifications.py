"""Notification REST endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import NotificationListResponse, NotificationResponse
from app.services.notifications import (
    list_notifications,
    mark_all_read,
    mark_notification_read,
    unread_count,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _serialize(notif: object) -> NotificationResponse:
    return NotificationResponse(
        id=notif.id,  # type: ignore[attr-defined]
        ticket_id=notif.ticket_id,  # type: ignore[attr-defined]
        ticket_key=notif.ticket.key,  # type: ignore[attr-defined]
        event_type=notif.event_type,  # type: ignore[attr-defined]
        message=notif.message,  # type: ignore[attr-defined]
        is_read=notif.is_read,  # type: ignore[attr-defined]
        created_at=notif.created_at,  # type: ignore[attr-defined]
    )


@router.get("", response_model=NotificationListResponse)
async def api_list_notifications(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    unread_only: bool = False,
    limit: int = 50,
) -> NotificationListResponse:
    notifs = await list_notifications(session, actor.id, unread_only=unread_only, limit=limit)
    count = await unread_count(session, actor.id)
    return NotificationListResponse(
        notifications=[_serialize(n) for n in notifs],
        unread_count=count,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def api_mark_read(
    notification_id: UUID,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> NotificationResponse:
    await mark_notification_read(session, notification_id, actor.id)
    await session.commit()
    all_notifs = await list_notifications(session, actor.id, limit=200)
    notif = next((n for n in all_notifs if n.id == notification_id), None)
    if notif is None:
        from app.core.exceptions import NotFound
        raise NotFound("notification")
    return _serialize(notif)


@router.post("/read-all")
async def api_mark_all_read(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, int]:
    count = await mark_all_read(session, actor.id)
    await session.commit()
    return {"marked_read": count}
