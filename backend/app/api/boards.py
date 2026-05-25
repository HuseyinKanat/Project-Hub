"""Board REST endpoints."""

import uuid as _uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor, require_board_admin
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import (
    BoardListResponse,
    BoardResponse,
    BoardUpdate,
    MembershipCreate,
    MembershipListResponse,
    MembershipResponse,
    MembershipUpdate,
)
from app.services.boards import get_board, list_boards, update_board
from app.services.memberships import (
    add_member,
    list_members,
    remove_member,
    update_member_role,
)
from app.services.serializers import board_response, membership_response

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


# ---------------------------------------------------------------------------
# PH-39: Board membership endpoints
# ---------------------------------------------------------------------------


@router.get("/{board_id}/members", response_model=MembershipListResponse)
async def api_list_members(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipListResponse:
    """List all members of a board (any authenticated actor)."""
    board = await get_board(session, board_id)
    members = await list_members(session, board)
    return MembershipListResponse(members=[membership_response(m) for m in members])


@router.post(
    "/{board_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_add_member(
    board_id: str,
    payload: MembershipCreate,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipResponse:
    """Add an actor to a board (admin only).

    Returns 201 with the new membership.
    Raises 422 if the role is unknown, 409 if the actor is already a member.
    """
    board = await get_board(session, board_id)
    membership = await add_member(session, board, payload.actor_id, payload.role)
    await session.commit()
    return membership_response(membership)


@router.patch("/{board_id}/members/{actor_id}", response_model=MembershipResponse)
async def api_update_member(
    board_id: str,
    actor_id: _uuid.UUID,
    payload: MembershipUpdate,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipResponse:
    """Update the role of a board member (admin only).

    Raises 422 on unknown role, 409 on last-admin demotion, 404 if not a member.
    """
    board = await get_board(session, board_id)
    membership = await update_member_role(session, board, actor_id, payload.role)
    await session.commit()
    return membership_response(membership)


@router.delete(
    "/{board_id}/members/{actor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_remove_member(
    board_id: str,
    actor_id: _uuid.UUID,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Remove a member from a board (admin only).

    Raises 409 if this is the last admin, 404 if not a member.
    """
    board = await get_board(session, board_id)
    await remove_member(session, board, actor_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
