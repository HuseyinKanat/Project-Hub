"""Board queries and bootstrap helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.db.models import Board, Workflow


def parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


async def list_boards(session: AsyncSession) -> list[Board]:
    result = await session.execute(
        select(Board).options(selectinload(Board.workflow)).order_by(Board.key)
    )
    return list(result.scalars())


async def get_board(session: AsyncSession, board_id: str) -> Board:
    board_uuid = parse_uuid(board_id)
    statement = select(Board).options(selectinload(Board.workflow))
    if board_uuid is None:
        statement = statement.where(Board.key == board_id.upper())
    else:
        statement = statement.where(Board.id == board_uuid)

    board = (await session.execute(statement)).scalar_one_or_none()
    if board is None:
        raise NotFound("board")
    return board


async def get_default_workflow(session: AsyncSession) -> Workflow:
    workflow = (
        await session.execute(select(Workflow).where(Workflow.is_default.is_(True)).limit(1))
    ).scalar_one_or_none()
    if workflow is None:
        raise NotFound("default workflow")
    return workflow
