"""Cross-board concept-graph endpoint (PH-274, epic PH-271 child 3/7).

GLOBAL router (no board scope) serving the bipartite topology for the ``/space``
view (PH-277). Permission lives in the SERVICE layer (``tag.read`` global gate,
mirroring ``api/concept_tags.py``); the router only wires ``current_actor`` +
optional ``?board`` / ``?tag`` query filters. Read-only — no migration.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
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
    tag: str | None = None,
) -> GraphResponse:
    nodes, edges = await build_graph(session, actor, board=board, tag=tag)
    return GraphResponse(nodes=nodes, edges=edges)
