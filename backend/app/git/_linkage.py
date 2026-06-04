"""Internal linkage helpers shared by webhook.py and sync.py.

Both the GitHub webhook path and the local sync path need to look up a ticket
by key within a board, and both need the system actor ID for history writes.
Centralising here avoids duplication and ensures consistent query semantics
(board-scoped, soft-delete aware).

Underscore prefix = internal; callers outside app/git/ should not import this.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Board, Ticket


async def find_ticket_by_key(
    session: AsyncSession, key: str, board_id: Any
) -> Ticket | None:
    """Return the Ticket for ``key`` on ``board_id``, or None if absent/deleted."""
    return (
        await session.execute(
            select(Ticket).where(
                Ticket.key == key.upper(),
                Ticket.board_id == board_id,
                Ticket.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_system_actor_id(session: AsyncSession, board: Board) -> Any:
    """Return the actor ID used for system-generated history rows.

    Currently delegates to ``board.created_by`` (the board creator acts as the
    system actor — consistent with webhook.py).  G6 may introduce a dedicated
    system actor; swap the implementation here.
    """
    return board.created_by
