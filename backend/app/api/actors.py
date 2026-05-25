"""Actor REST endpoints — PH-39.

GET /api/actors returns all is_active=true actors for the add-member dropdown.
No admin gate required; any authenticated actor may call this endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import ActorListResponse
from app.services.memberships import list_actors
from app.services.serializers import actor_summary

router = APIRouter(prefix="/api/actors", tags=["actors"])


@router.get("", response_model=ActorListResponse)
async def api_list_actors(
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ActorListResponse:
    """List all active actors.

    Used by the Members tab add-member dropdown.  Returns all is_active=true
    actors regardless of board membership — the frontend filters out actors
    already on the board client-side.
    """
    actors = await list_actors(session)
    return ActorListResponse(actors=[actor_summary(a) for a in actors])
