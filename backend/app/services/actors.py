"""Actor lookup helpers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import resolve_actor_by_token
from app.core.exceptions import NotFound
from app.core.logging import get_logger
from app.db.models import Actor
from app.services.boards import parse_uuid

logger = get_logger(__name__)


async def get_actor_from_token(session: AsyncSession, token: str) -> Actor | None:
    """Validate a raw bearer token and return the matching active actor.

    Used by WebSocket auth and the attachment-content route, where the HTTPBearer
    dependency isn't available. Delegates to the ONE shared resolver
    (``app.api.deps.resolve_actor_by_token`` -- PH-321), so this path gets the same
    L1 verified-token cache + O(1) indexed lookup + single-flight as ``current_actor``.
    It NO LONGER runs the O(n) bcrypt scan over every active actor that blocked the
    event loop on every board-open WebSocket handshake (PROD 3-6s board loads). ``None``
    is preserved for a non-matching token; callers map it to a 1008 close / 403.
    """
    actor = await resolve_actor_by_token(session, token)

    token_preview = token[:8] + "..." if len(token) > 8 else token
    if actor is None:
        logger.warning("no_actor_match: token_preview=%s", token_preview)
    else:
        logger.debug(
            "token_match_success: actor=%s token_preview=%s",
            actor.display_name,
            token_preview,
        )
    return actor


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
