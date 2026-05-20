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

    # Debug logging for authentication issues
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    token_preview = token[:8] + "..." if len(token) > 8 else token
    logger.debug(
        "token_lookup: token_preview=%s active_actors=%d",
        token_preview,
        len(actors)
    )

    for actor in actors:
        try:
            if verify_token(token, actor.token_hash):
                logger.debug(
                    "token_match_success: actor=%s token_preview=%s",
                    actor.display_name,
                    token_preview
                )
                return actor
        except Exception as e:
            # Log verification errors without exposing sensitive data
            logger.warning(
                "token_verification_error: actor=%s error=%s token_preview=%s",
                actor.display_name,
                str(e),
                token_preview
            )

    # Log when no actor matches the token
    logger.warning(
        "no_actor_match: token_preview=%s checked_actors=%d",
        token_preview,
        len(actors)
    )
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
