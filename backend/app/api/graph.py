"""Cross-board concept-graph endpoint (PH-274 / PH-279; labels in PH-281).

GLOBAL router (no board scope) serving the bipartite topology for the ``/space``
view (PH-277/PH-280). Permission lives in the SERVICE layer (global
``ticket.read`` gate, PH-281); the router only wires ``current_actor`` + optional
``?board`` / ``?label`` / ``?scope`` query filters. Read-only — no migration.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import GraphResponse
from app.services.graph import build_graph

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("", response_model=GraphResponse)
async def api_get_graph(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    board: str | None = None,
    label: str | None = None,
    scope: str = "global",
) -> GraphResponse:
    # PH-279: ?scope=global|board. scope=board without ?board (or an invalid
    # scope literal) is a client error → 422 (build_graph raises ValueError).
    try:
        nodes, edges = await build_graph(
            session, actor, board=board, label=label, scope=scope  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return GraphResponse(nodes=nodes, edges=edges)
