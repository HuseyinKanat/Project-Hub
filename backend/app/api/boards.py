"""Board REST endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import BoardListResponse, BoardResponse
from app.services.boards import get_board, list_boards
from app.services.serializers import board_response

router = APIRouter(prefix="/api/boards", tags=["boards"])


@router.get("", response_model=BoardListResponse)
async def api_list_boards(
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardListResponse:
    boards = await list_boards(session)
    return BoardListResponse(boards=[board_response(board) for board in boards])


@router.get("/{board_id}", response_model=BoardResponse)
async def api_get_board(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardResponse:
    return board_response(await get_board(session, board_id))
