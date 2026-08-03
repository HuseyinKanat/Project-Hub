"""Epic-progress rollup endpoint (PH-335) — read-only, per-board.

A DEDICATED router (NOT added to ``boards.py``): editing ``boards.py`` would trip
the ``.codemap`` ``boards.py -> components/sonarqube.md`` sync gate (exit-protocol
§11.2), forcing an unrelated epic-progress bullet into the SonarQube page. Keeping
the endpoint in its own module keeps the diff out of ``.codemap`` and mirrors
P6a's deliberate ``api/board_notes.py`` disjointness. Read-only: no migration, no
new table — the rollup is derived from existing child-ticket state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import EpicProgressResponse
from app.services.progress import epic_progress

router = APIRouter(prefix="/api/boards", tags=["progress"])


@router.get("/{board_id}/epics/progress", response_model=EpicProgressResponse)
async def api_epic_progress(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EpicProgressResponse:
    """Per-epic progress rollup for a board.

    Unknown board -> 404 FIRST, non-member -> 403 SECOND (PH-327 board-scope).
    """
    return await epic_progress(session, actor=actor, board_id=board_id)
