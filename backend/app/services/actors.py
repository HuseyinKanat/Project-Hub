"""Actor lookup helpers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.db.models import Actor
from app.services.boards import parse_uuid


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
