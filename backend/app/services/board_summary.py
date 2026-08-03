"""Per-board SINGLETON project-summary store (PH-338) — read + full-upsert service.

The Coordinator-authored "project overview" artifact behind the board's "Overview"
tab: fixed free-text sections (``purpose``/``status``/``progress``/``highlights``) plus
an ordered ``milestones`` JSON list. Exactly 0..1 per board — the
``uq_board_summary_board`` UNIQUE on ``board_id`` makes ``upsert_summary`` an UPDATE of
the existing row rather than an append (the ``ProjectPath`` upsert-on-unique-key
precedent, but singleton-keyed on ``board_id`` alone).

Two auth ladders, deliberately asymmetric (AC5):
  - READ (``get_summary``): unknown board -> 404 FIRST (``get_board``), non-member ->
    403 SECOND (``require_board_member``). Any board member may read — mirrors
    ``board_notes`` / the ``get_board_notes`` MCP tool, so every jarwis role can PULL.
  - WRITE (``upsert_summary`` / ``delete_summary``): unknown board -> 404 FIRST, then
    ``require_permission(..., "board.summary.write")`` -> 403. That single check rejects
    BOTH a non-member (have:[]) AND a member holding a read-only role
    (architect/implementer/reviewer/qa) — only pm/orchestrator/admin carry the cap.
    ⚠️ INERT until ``update_board_roles`` re-applies the role template to existing
    boards (the missing-role-key trap — PH-287 pattern); reads keep working regardless.

Services own their transaction boundary (``get_db_session`` does NOT commit) — upsert
and delete commit; get is read-only. ``milestones`` is stored ``model_dump(mode="json")``
so a ``due_date`` serializes to an ISO string that round-trips across both the Postgres
JSONB and SQLite JSON variants (a naive dump would hand SQLite a ``date`` object it
cannot JSON-encode — R4).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import schemas
from app.core.exceptions import NotFound
from app.core.permissions import require_board_member, require_permission
from app.db.models import Actor, BoardSummary
from app.services.boards import get_board

# The write capability gating PUT/DELETE + the MCP set tool. Held by pm/orchestrator
# (defaults.py) + admin ("*"); read-only roles are rejected. Exact-match cap
# (``_permission_matches`` first branch; ``*`` admin covers it).
_PERM_SUMMARY_WRITE = "board.summary.write"


def _to_schema(summary: BoardSummary, *, updated_by_name: str | None) -> schemas.BoardSummary:
    """Serialize a ``BoardSummary`` ORM row.

    ``updated_by_name`` is passed explicitly by the caller — resolved from the
    eager-loaded ``summary.updater`` on the read path, or the authenticated writer's
    ``display_name`` on the write path (no reload). A deleted/absent updater -> None
    (the UI shows "unknown"; never a 500). ``summary.milestones`` is a JSON list of
    dicts; each is validated into a ``Milestone`` explicitly (type-safe coercion —
    a stored ISO ``due_date`` string parses back to a ``date``).
    """
    return schemas.BoardSummary(
        board_id=summary.board_id,
        purpose=summary.purpose,
        status=summary.status,
        progress=summary.progress,
        highlights=summary.highlights,
        milestones=[schemas.Milestone.model_validate(m) for m in summary.milestones],
        updated_by=summary.updated_by,
        updated_by_name=updated_by_name,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )


async def get_summary(
    session: AsyncSession, *, actor: Actor, board_id: str
) -> schemas.BoardSummary | None:
    """Read a board's singleton summary, or ``None`` if it has none.

    ``None`` (summary-not-yet-created) is distinct from a 404 (unknown board): the REST
    layer maps ``None`` -> ``200 null`` (FE empty-state) and the unknown board -> 404.
    """
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_board_member(actor, board)  # non-member -> 403 SECOND
    result = await session.execute(
        select(BoardSummary)
        .where(BoardSummary.board_id == board.id)
        .options(selectinload(BoardSummary.updater))
    )
    summary = result.scalar_one_or_none()
    if summary is None:
        return None
    updater = summary.updater
    return _to_schema(
        summary, updated_by_name=updater.display_name if updater is not None else None
    )


async def upsert_summary(
    session: AsyncSession, *, actor: Actor, board_id: str, data: schemas.BoardSummaryUpsert
) -> schemas.BoardSummary:
    """Full-replace the board's singleton summary (create if absent, else update).

    The write gate (``board.summary.write``) rejects both a non-member and a member
    with a read-only role. ``milestones`` is stored ``mode="json"`` (date -> ISO str).
    The UNIQUE(board_id) makes a second call an UPDATE of the same row — no duplicate.
    """
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_permission(actor, board, _PERM_SUMMARY_WRITE)  # not-authorized -> 403 SECOND
    result = await session.execute(
        select(BoardSummary).where(BoardSummary.board_id == board.id)
    )
    summary = result.scalar_one_or_none()
    milestones = [m.model_dump(mode="json") for m in data.milestones]
    if summary is None:
        summary = BoardSummary(board_id=board.id)
        session.add(summary)
    summary.purpose = data.purpose
    summary.status = data.status
    summary.progress = data.progress
    summary.highlights = data.highlights
    summary.milestones = milestones
    summary.updated_by = actor.id
    await session.commit()
    # Refresh to pull server-side timestamps (created_at + the onupdate updated_at) for
    # the response; the writer's name is the authenticated actor (no updater reload).
    await session.refresh(summary)
    return _to_schema(summary, updated_by_name=actor.display_name)


async def delete_summary(session: AsyncSession, *, actor: Actor, board_id: str) -> None:
    """Delete the board's summary (write-gated). No summary -> 404 (idempotent-ish)."""
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_permission(actor, board, _PERM_SUMMARY_WRITE)  # not-authorized -> 403 SECOND
    result = await session.execute(
        select(BoardSummary).where(BoardSummary.board_id == board.id)
    )
    summary = result.scalar_one_or_none()
    if summary is None:
        raise NotFound("summary")
    await session.delete(summary)
    await session.commit()
