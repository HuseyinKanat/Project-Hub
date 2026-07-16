"""Per-owner project-path registry service (PH-322).

set / get / list over the ``project_paths`` table, keyed on the owner resolved from
the CALLING token (never a payload owner). ``set`` upserts on ``(owner_slug,
board_id)``; an empty ``local_path`` REMOVES the row (remove semantics — no DELETE
endpoint). All three gate on board membership; set/get additionally require a
resolved owner (422 owner_unresolved).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import OwnerUnresolved, ProfileFieldInvalid
from app.core.logging import get_logger
from app.core.permissions import require_board_member
from app.db.models import Actor, Board, BoardMembership, ProjectPath
from app.services.owners import resolve_owner_slug

logger = get_logger(__name__)

# The ``local_path`` column is VARCHAR(255); a longer value is a clean 422, never a
# DB-level 500. (Empty is NOT rejected here — it is the remove sentinel.)
_LOCAL_PATH_MAX = 255


async def _get_row(
    session: AsyncSession, owner_slug: str, board_id: object
) -> ProjectPath | None:
    return (
        await session.execute(
            select(ProjectPath).where(
                ProjectPath.owner_slug == owner_slug,
                ProjectPath.board_id == board_id,
            )
        )
    ).scalar_one_or_none()


async def get_project_path(
    session: AsyncSession, actor: Actor, board: Board
) -> tuple[str, ProjectPath | None]:
    """Return ``(owner, row-or-None)`` for the caller's own path on ``board``.

    Raises 422 ``OwnerUnresolved`` if the caller has no owner, 403 if not a member.
    """

    owner = resolve_owner_slug(actor)
    if owner is None:
        raise OwnerUnresolved()
    require_board_member(actor, board)
    row = await _get_row(session, owner, board.id)
    return owner, row


async def set_project_path(
    session: AsyncSession, actor: Actor, board: Board, local_path: str
) -> tuple[str, ProjectPath | None]:
    """Upsert (or remove) the caller's own path on ``board``.

    Returns ``(owner, row)`` — ``row`` is ``None`` when ``local_path`` was empty (the
    ``(owner, board)`` row is deleted; remove semantics). Order of guards (each
    rejecting BEFORE any write): unresolved owner → 422; non-member → 403; a
    non-empty ``local_path`` > 255 chars → 422 ``ProfileFieldInvalid(local_path)``.
    ``local_path`` is stored VERBATIM (opaque, never validated as a real path).
    """

    owner = resolve_owner_slug(actor)
    if owner is None:
        raise OwnerUnresolved()
    require_board_member(actor, board)

    existing = await _get_row(session, owner, board.id)

    if local_path == "":
        # Remove semantics — empty value deletes the row (reads back null).
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        logger.info("project_path_set owner=%s board=%s removed", owner, board.key)
        return owner, None

    if len(local_path) > _LOCAL_PATH_MAX:
        raise ProfileFieldInvalid(
            f"local_path exceeds the maximum length of {_LOCAL_PATH_MAX} characters",
            field="local_path",
        )

    if existing is not None:
        existing.local_path = local_path
        row = existing
    else:
        row = ProjectPath(owner_slug=owner, board_id=board.id, local_path=local_path)
        session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info(
        "project_path_set owner=%s board=%s path_len=%d", owner, board.key, len(local_path)
    )
    return owner, row


async def list_project_paths(
    session: AsyncSession, actor: Actor, board: Board
) -> list[tuple[str, ProjectPath | None]]:
    """Return ``(owner, row-or-None)`` for each DISTINCT owner among ``board``'s members.

    Membership READ gate only (403 for a non-member) — the caller's OWN owner need
    not resolve. One batched ``project_paths`` query for the board (no N+1), sorted by
    owner for a stable shape. An owner with no path row yields ``(owner, None)``.
    """

    require_board_member(actor, board)

    memberships = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.board_id == board.id)
            .options(selectinload(BoardMembership.actor))
        )
    ).scalars().all()

    rows = (
        await session.execute(
            select(ProjectPath).where(ProjectPath.board_id == board.id)
        )
    ).scalars().all()
    path_by_owner = {row.owner_slug: row for row in rows}

    seen: dict[str, ProjectPath | None] = {}
    for membership in memberships:
        owner = resolve_owner_slug(membership.actor)
        if owner and owner not in seen:
            seen[owner] = path_by_owner.get(owner)
    return [(owner, seen[owner]) for owner in sorted(seen)]
