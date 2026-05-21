"""Authentication endpoints: /me, etc."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.models import Actor, BoardMembership
from app.db.session import get_db_session
from app.schemas import ActorSummary, MembershipSummary, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MeResponse:
    """Return the authenticated actor and all board memberships."""
    # Re-query actor with nested selectinload: memberships → board
    # deps.py's current_actor only loads memberships without board relation,
    # so we need board.key here — do an endpoint-local refresh.
    stmt = (
        select(Actor)
        .where(Actor.id == actor.id)
        .options(selectinload(Actor.memberships).selectinload(BoardMembership.board))
    )
    result = await session.execute(stmt)
    actor_full = result.scalar_one()

    memberships = [
        MembershipSummary(
            board_id=m.board_id,
            board_key=m.board.key,
            role=m.role,
        )
        for m in actor_full.memberships
    ]
    return MeResponse(
        actor=ActorSummary(
            id=actor_full.id,
            kind=actor_full.kind,
            display_name=actor_full.display_name,
            agent_id=actor_full.agent_id,
            agent_role_hint=actor_full.agent_role_hint,
        ),
        memberships=memberships,
    )
