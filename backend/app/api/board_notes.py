"""Board-scoped notes / guardrails endpoints (PH-336) — CRUD, per-board.

A DEDICATED router (NOT added to ``boards.py``): editing ``boards.py`` would trip the
``.codemap`` ``boards.py -> components/sonarqube.md`` sync gate (exit-protocol §11.2),
dragging an unrelated notes bullet into the SonarQube page and coupling this feature to
that page's diff. Keeping the endpoints in their own module mirrors ``api/progress.py``'s
deliberate disjointness. Humans WRITE here; agents PULL read-only via the MCP
``get_board_notes`` tool.

Auth: unknown board -> 404 FIRST, non-member -> 403 SECOND (PH-327 board-scope), enforced
in ``services.board_notes``. A blank/whitespace-only ``body`` -> 422 at request parse
(``BoardNoteCreate`` validator) with NO partial row.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import BoardNote, BoardNoteCreate, BoardNoteListResponse
from app.services.board_notes import create_note, delete_note, list_notes

router = APIRouter(prefix="/api/boards", tags=["board-notes"])


@router.get("/{board_id}/notes", response_model=BoardNoteListResponse)
async def api_list_board_notes(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardNoteListResponse:
    """List a board's notes (newest-first). Unknown board -> 404, non-member -> 403."""
    return await list_notes(session, actor=actor, board_id=board_id)


@router.post(
    "/{board_id}/notes",
    response_model=BoardNote,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_board_note(
    board_id: str,
    payload: BoardNoteCreate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardNote:
    """Add a note authored by the caller. Blank body -> 422 (no partial row)."""
    return await create_note(session, actor=actor, board_id=board_id, body=payload.body)


@router.delete(
    "/{board_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_delete_board_note(
    board_id: str,
    note_id: UUID,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    """Delete one of the board's notes. A note of another board -> 404 (no cross-board)."""
    await delete_note(session, actor=actor, board_id=board_id, note_id=note_id)
