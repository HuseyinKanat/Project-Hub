"""Cross-board search endpoint (PH-275, epic PH-271 child 4/7).

GLOBAL router (no board scope) serving the unified search surface for PH-278.
Permission lives in the SERVICE layer (``tag.read`` global gate, mirroring
``api/graph.py`` + ``api/concept_tags.py``); the router only wires
``current_actor`` + the ``?q`` / ``?tags`` query params. Read-only — no
migration. A SEPARATE router (own prefix + own response model) rather than
bolting onto graph.py, keeping both STABLE contracts clean.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import SearchResponse
from app.services.search import search
from app.services.serializers import concept_tag_summary, ticket_search_hit

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def api_search(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: str | None = None,
    tags: str | None = None,
) -> SearchResponse:
    tickets, concept_tags = await search(session, actor, q=q, tags=tags)
    return SearchResponse(
        tickets=[ticket_search_hit(t) for t in tickets],
        concept_tags=[concept_tag_summary(tag) for tag in concept_tags],
    )
