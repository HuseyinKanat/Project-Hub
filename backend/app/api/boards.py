"""Board REST endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import BoardListResponse, BoardResponse, BoardUpdate
from app.services.boards import get_board, list_boards, update_board
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


@router.patch("/{board_id}", response_model=BoardResponse)
async def api_update_board(
    board_id: str,
    payload: BoardUpdate,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardResponse:
    board = await get_board(session, board_id)
    await update_board(
        session,
        board,
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        roles=payload.roles,
    )
    await session.commit()
    # Re-fetch to ensure relationships are loaded for serialization
    updated_board = await get_board(session, str(board.id))
    return board_response(updated_board)
