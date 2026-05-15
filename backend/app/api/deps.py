"""FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, PermissionDenied
from app.core.security import verify_token
from app.db.models import Actor, Board
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
