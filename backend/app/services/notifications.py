"""Notification service: create, list, and mark as read."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import BoardMembership, Notification, Ticket
from app.services.background_tasks import run_in_background, send_notification_emails_background
from app.services.email_templates import comment_added_email_template, state_change_email_template
from app.services.user_preferences import get_actors_with_email_notifications


async def create_notifications(
    session: AsyncSession,
    *,
    ticket: Ticket,
    event_type: str,
    message: str,
    actor_ids: list[uuid.UUID],
) -> list[Notification]:
    """Create a notification for each actor in actor_ids (dedup, skip None)."""
    unique_ids = list({a for a in actor_ids if a is not None})
    notifications: list[Notification] = []
    for actor_id in unique_ids:
        notif = Notification(
            actor_id=actor_id,
            ticket_id=ticket.id,
            event_type=event_type,
            message=message,
            is_read=False,
        )
        session.add(notif)
        notifications.append(notif)
    await session.flush()
    return notifications


async def _notification_recipients(
    session: AsyncSession,
    ticket: Ticket,
    exclude_actor_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Return reporter + assignee + board admin/pm members, excluding the acting actor."""
    ids: set[uuid.UUID] = set()
    if ticket.reporter_id:
        ids.add(ticket.reporter_id)
    if ticket.assignee_id:
        ids.add(ticket.assignee_id)
    # Include board members with admin or pm role so they always get notified
    result = await session.execute(
        select(BoardMembership.actor_id).where(
            BoardMembership.board_id == ticket.board_id,
            BoardMembership.role.in_(["admin", "pm"]),
        )
    )
    for row in result.scalars():
        ids.add(row)
    if exclude_actor_id:
        ids.discard(exclude_actor_id)
    return list(ids)


async def notify_state_changed(
    session: AsyncSession,
    *,
    ticket: Ticket,
    actor_id: uuid.UUID,
    old_state: str,
    new_state: str,
    actor_name: str = "Unknown",
) -> None:
    """Create notifications for state transitions."""
    if old_state == new_state:
        return
    recipients = await _notification_recipients(session, ticket, exclude_actor_id=actor_id)
    if not recipients:
        return
    message = f"{ticket.key} durumu {old_state} → {new_state} olarak değişti."
    await create_notifications(
        session,
        ticket=ticket,
        event_type="state_changed",
        message=message,
        actor_ids=recipients,
    )

    # Send email notifications in background
    email_recipients = await get_actors_with_email_notifications(
        session, recipients, notification_type="state_changes"
    )
    if email_recipients:
        subject, body = state_change_email_template(ticket, old_state, new_state, actor_name)
        run_in_background(
            lambda: send_notification_emails_background(email_recipients, subject, body),
            name=f"email_state_change_{ticket.key}"
        )


async def notify_comment_added(
    session: AsyncSession,
    *,
    ticket: Ticket,
    actor_id: uuid.UUID,
    author_name: str,
    comment_text: str = "",
) -> None:
    """Create notifications when a comment is added."""
    recipients = await _notification_recipients(session, ticket, exclude_actor_id=actor_id)
    if not recipients:
        return
    message = f"{author_name} {ticket.key} ticketina yorum ekledi."
    await create_notifications(
        session,
        ticket=ticket,
        event_type="comment_added",
        message=message,
        actor_ids=recipients,
    )

    # Send email notifications in background
    email_recipients = await get_actors_with_email_notifications(
        session, recipients, notification_type="comments"
    )
    if email_recipients:
        subject, body = comment_added_email_template(ticket, author_name, comment_text)
        run_in_background(
            lambda: send_notification_emails_background(email_recipients, subject, body),
            name=f"email_comment_{ticket.key}"
        )


async def list_notifications(
    session: AsyncSession,
    actor_id: uuid.UUID,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.actor_id == actor_id)
        .options(selectinload(Notification.ticket))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    result = await session.execute(stmt)
    return list(result.scalars())


async def mark_notification_read(
    session: AsyncSession,
    notification_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> bool:
    """Mark a single notification as read. Returns True if updated."""
    result = await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.actor_id == actor_id)
        .values(is_read=True)
    )
    await session.flush()
    rowcount: int | None = getattr(result, "rowcount", None)
    return (rowcount or 0) > 0


async def mark_all_read(session: AsyncSession, actor_id: uuid.UUID) -> int:
    """Mark all notifications as read for an actor. Returns count updated."""
    result = await session.execute(
        update(Notification)
        .where(Notification.actor_id == actor_id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await session.flush()
    rowcount: int | None = getattr(result, "rowcount", None)
    return rowcount or 0


async def unread_count(session: AsyncSession, actor_id: uuid.UUID) -> int:
    """Return count of unread notifications for an actor."""
    result = await session.execute(
        select(Notification)
        .where(Notification.actor_id == actor_id, Notification.is_read.is_(False))
    )
    return len(result.scalars().all())
