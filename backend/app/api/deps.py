"""FastAPI dependencies."""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, PermissionDenied
from app.core.security import verify_token
from app.core.token_cache import verified_token_cache
from app.db.models import Actor, Board, BoardMembership
from app.db.session import get_db_session
from app.services.boards import get_board

bearer_scheme = HTTPBearer(auto_error=False)


async def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Actor:
    if credentials is None:
        raise PermissionDenied(required="authenticated_actor", have=[])

    token = credentials.credentials

    # Fast path (PH-319): a previously verified token skips the O(n) bcrypt scan.
    # The snapshot check below makes a rotated/revoked token fall through safely.
    cached = verified_token_cache.get(token)
    if cached is not None:
        actor = (
            await session.execute(
                select(Actor)
                .where(Actor.id == uuid.UUID(cached.actor_id), Actor.is_active.is_(True))
                .options(selectinload(Actor.memberships))
            )
        ).scalar_one_or_none()
        # token_hash must still match the snapshot; otherwise the token was
        # rotated/revoked (or the actor deactivated) -> drop entry, full scan.
        if actor is not None and actor.token_hash == cached.token_hash:
            return actor
        verified_token_cache.invalidate(token)

    # Slow path (unchanged): full scan. Cache the winning actor for next time.
    result = await session.execute(
        select(Actor)
        .where(Actor.is_active.is_(True))
        .options(selectinload(Actor.memberships))
    )
    actors = list(result.scalars())
    for actor in actors:
        if verify_token(token, actor.token_hash):
            verified_token_cache.put(token, str(actor.id), actor.token_hash)
            return actor

    raise PermissionDenied(required="valid_bearer_token", have=[])


async def get_board_by_key(
    board_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Board:
    board = (
        await session.execute(select(Board).where(Board.key == board_key.upper()))
    ).scalar_one_or_none()
    if board is None:
        raise NotFound("board")
    return board


async def require_board_admin(
    board_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Actor:
    """Dependency that ensures the current actor is an admin on ``board_id``.

    PH-39: members POST/PATCH/DELETE.  PH-223: sonarqube setup/sync.
    Resolves ``board_id`` the same way ``get_board`` does (KEY or UUID) so a
    board KEY is accepted -- the UI always sends the key (PH-233).  Resolution
    is load-bearing and ordered: unknown board -> 404 NotFound (via get_board)
    FIRST; a resolved board with no admin membership -> 403 PermissionDenied
    SECOND.  The two paths never bleed into each other.
    """
    board = await get_board(session, board_id)  # 404 on unknown board (NotFound)
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor.id,
                BoardMembership.role == "admin",
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise PermissionDenied(required="board.admin", have=[])

    return actor
