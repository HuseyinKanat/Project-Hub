"""Cross-board search endpoint (PH-275; re-pointed to labels in PH-281).

GLOBAL router (no board scope) serving the unified search surface for
PH-278/PH-280. Permission lives in the SERVICE layer (global ``ticket.read``
gate, PH-281); the router only wires ``current_actor`` + the ``?q`` / ``?labels``
query params. Read-only — no migration.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import SearchResponse
from app.services.search import search
from app.services.serializers import ticket_search_hit

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def api_search(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: str | None = None,
    labels: str | None = None,
) -> SearchResponse:
    tickets, label_hits = await search(session, actor, q=q, labels=labels)
    return SearchResponse(
        tickets=[ticket_search_hit(t) for t in tickets],
        labels=label_hits,
    )
