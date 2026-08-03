"""Board-scoped project-summary endpoints (PH-338) — singleton read + upsert + delete.

A DEDICATED router (NOT added to ``boards.py``): editing ``boards.py`` would trip the
``.codemap`` ``boards.py -> components/sonarqube.md`` sync gate (exit-protocol §11.2)
and couple this feature to that page's diff. Mirrors ``api/board_notes.py`` /
``api/progress.py`` deliberate disjointness.

Read (GET) is membership-gated (any board member). Write (PUT/DELETE) is gated on the
``board.summary.write`` capability (pm/orchestrator/admin). Auth order, enforced in
``services.board_summary``: unknown board -> 404 FIRST, unauthorized -> 403 SECOND. A
GET of a board that has NO summary yet -> ``200 + null`` (FE empty-state — distinct
from the unknown-board 404). An invalid milestone (bad ``status`` / blank ``title``)
-> 422 at request parse (``BoardSummaryUpsert``), with NO partial write.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import BoardSummary, BoardSummaryUpsert
from app.services.board_summary import delete_summary, get_summary, upsert_summary

router = APIRouter(prefix="/api/boards", tags=["board-summary"])


@router.get("/{board_id}/summary", response_model=BoardSummary | None)
async def api_get_board_summary(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardSummary | None:
    """Read the board's singleton summary.

    Unknown board -> 404, non-member -> 403, no summary yet -> 200 + ``null``.
    """
    return await get_summary(session, actor=actor, board_id=board_id)


@router.put("/{board_id}/summary", response_model=BoardSummary)
async def api_upsert_board_summary(
    board_id: str,
    payload: BoardSummaryUpsert,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardSummary:
    """Full-upsert the board's summary (create if absent, else update).

    Unauthorized (non-member OR read-only role) -> 403; invalid milestone -> 422.
    """
    return await upsert_summary(session, actor=actor, board_id=board_id, data=payload)


@router.delete("/{board_id}/summary", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_board_summary(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    """Delete the board's summary. Unauthorized -> 403; no summary -> 404."""
    await delete_summary(session, actor=actor, board_id=board_id)
