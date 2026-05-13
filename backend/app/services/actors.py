"""Actor lookup helpers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.core.security import verify_token
from app.db.models import Actor
from app.services.boards import parse_uuid


async def get_actor_from_token(session: AsyncSession, token: str) -> Actor | None:
    """Validate a raw bearer token and return the matching actor.

    This is used by WebSocket auth where we can't use HTTPBearer dependency.
    """
    result = await session.execute(
        select(Actor)
        .where(Actor.is_active.is_(True))
        .options(selectinload(Actor.memberships))
    )
    actors = list(result.scalars())
    for actor in actors:
        if verify_token(token, actor.token_hash):
            return actor
    return None


async def get_actor(session: AsyncSession, actor_id: str) -> Actor:
    actor_uuid = parse_uuid(actor_id)
    statement = select(Actor).options(selectinload(Actor.memberships))
    if actor_uuid is None:
        statement = statement.where(Actor.agent_id == actor_id)
    else:
        statement = statement.where(Actor.id == actor_uuid)

    actor = (await session.execute(statement)).scalar_one_or_none()
    if actor is None:
        raise NotFound("actor")
    return actor
