"""Board membership service — PH-39.

All mutations are additions only; no schema changes needed.

Concurrency note on last-admin guard: we use a simple count-then-act
approach (not SELECT FOR UPDATE).  In the pilot there is effectively one
admin user, so the TOCTOU window is acceptable.  A follow-up ticket can
add row-level locking if needed.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import DuplicateMembershipError, LastAdminError, NotFound, UnknownRoleError
from app.db.models import Actor, Board, BoardMembership

# ---------------------------------------------------------------------------
# Role-name normalisation (handles both nested {roles: {...}} and flat shapes)
# ---------------------------------------------------------------------------


def _board_role_keys(board: Board) -> set[str]:
    """Return the set of role names defined in board.roles.

    Handles both shapes observed in the codebase:
    - nested: {"roles": {"admin": {...}, "pm": {...}}}
    - flat:   {"admin": {...}, "pm": {...}}
    """
    roles_dict: dict[str, Any] = {}
    if isinstance(board.roles, dict):
        nested = board.roles.get("roles")
        if isinstance(nested, dict):
            roles_dict = nested
        else:
            roles_dict = {k: v for k, v in board.roles.items() if k != "webhook_secret"}
    return set(roles_dict.keys())


def _is_admin_role(board: Board, role: str) -> bool:
    """True if the given role name corresponds to the 'admin' role.

    Currently a direct string equality check against the conventional name;
    could be permission-based in the future.
    """
    return role == "admin"


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def list_members(
    session: AsyncSession, board: Board
) -> list[BoardMembership]:
    """Return all memberships for a board with actor eagerly loaded.

    Ordered by created_at ASC (stable for snapshot tests — AC14).
    """
    result = await session.execute(
        select(BoardMembership)
        .where(BoardMembership.board_id == board.id)
        .options(selectinload(BoardMembership.actor))
        .order_by(BoardMembership.created_at.asc())
    )
    return list(result.scalars())


async def _admin_count(session: AsyncSession, board_id: _uuid.UUID) -> int:
    """Count the number of admin memberships on this board."""
    result = await session.execute(
        select(BoardMembership).where(
            BoardMembership.board_id == board_id,
            BoardMembership.role == "admin",
        )
    )
    return len(list(result.scalars()))


async def list_actors(session: AsyncSession) -> list[Actor]:
    """Return all active actors (for the add-member dropdown)."""
    result = await session.execute(
        select(Actor).where(Actor.is_active.is_(True)).order_by(Actor.display_name)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def add_member(
    session: AsyncSession, board: Board, actor_id: _uuid.UUID, role: str
) -> BoardMembership:
    """Add an actor to the board with the given role.

    Raises:
        NotFound: actor with ``actor_id`` does not exist.
        UnknownRoleError: ``role`` is not in ``board.roles``.
        DuplicateMembershipError: ``(board, actor)`` pair already exists.
    """
    # Validate role name
    known_roles = _board_role_keys(board)
    if role not in known_roles:
        raise UnknownRoleError(role)

    # Confirm actor exists and is active
    actor = (
        await session.execute(
            select(Actor).where(Actor.id == actor_id, Actor.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if actor is None:
        raise NotFound("actor")

    membership = BoardMembership(
        board_id=board.id,
        actor_id=actor_id,
        role=role,
    )
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise DuplicateMembershipError() from err

    # Reload with actor relationship
    refreshed = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.id == membership.id)
            .options(selectinload(BoardMembership.actor))
        )
    ).scalar_one()
    return refreshed


async def update_member_role(
    session: AsyncSession, board: Board, actor_id: _uuid.UUID, new_role: str
) -> BoardMembership:
    """Update the role of an existing membership.

    Raises:
        NotFound: membership does not exist.
        UnknownRoleError: ``new_role`` is not in ``board.roles``.
        LastAdminError: operation would demote the last admin.
    """
    # Validate role name
    known_roles = _board_role_keys(board)
    if new_role not in known_roles:
        raise UnknownRoleError(new_role)

    membership = (
        await session.execute(
            select(BoardMembership)
            .where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor_id,
            )
            .options(selectinload(BoardMembership.actor))
        )
    ).scalar_one_or_none()

    if membership is None:
        raise NotFound("membership")

    # Last-admin guard: if this member is currently an admin and we're
    # changing to a non-admin role, check there is at least one other admin.
    if _is_admin_role(board, membership.role) and not _is_admin_role(board, new_role):
        count = await _admin_count(session, board.id)
        if count <= 1:
            raise LastAdminError()

    membership.role = new_role
    await session.flush()

    # Expire and re-fetch so updated_at and actor relationship are fresh
    await session.refresh(membership)
    refreshed = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.id == membership.id)
            .options(selectinload(BoardMembership.actor))
        )
    ).scalar_one()
    return refreshed


async def remove_member(
    session: AsyncSession, board: Board, actor_id: _uuid.UUID
) -> None:
    """Remove an actor's membership from the board.

    Raises:
        NotFound: membership does not exist.
        LastAdminError: operation would remove the last admin.
    """
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor_id,
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise NotFound("membership")

    # Last-admin guard
    if _is_admin_role(board, membership.role):
        count = await _admin_count(session, board.id)
        if count <= 1:
            raise LastAdminError()

    await session.delete(membership)
    await session.flush()
