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
from app.db.models import Actor, Board, BoardMembership
from app.db.session import get_db_session

bearer_scheme = HTTPBearer(auto_error=False)


async def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Actor:
    if credentials is None:
        raise PermissionDenied(required="authenticated_actor", have=[])

    result = await session.execute(
        select(Actor)
        .where(Actor.is_active.is_(True))
        .options(selectinload(Actor.memberships))
    )
    actors = list(result.scalars())
    for actor in actors:
        if verify_token(credentials.credentials, actor.token_hash):
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

    PH-39: used by POST/PATCH/DELETE /api/boards/{board_id}/members endpoints.
    Raises 403 PermissionDenied if the actor has no admin membership on this board.
    """
    try:
        board_uuid = uuid.UUID(board_id)
    except ValueError as err:
        # board_id is not a UUID (shouldn't happen given router path but be safe)
        raise PermissionDenied(required="board.admin", have=[]) from err

    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board_uuid,
                BoardMembership.actor_id == actor.id,
                BoardMembership.role == "admin",
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise PermissionDenied(required="board.admin", have=[])

    return actor
