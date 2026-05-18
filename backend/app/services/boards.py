"""Board queries and bootstrap helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.db.models import Board, Workflow


def mask_webhook_secret(roles: dict[str, object]) -> dict[str, object]:
    """Mask webhook_secret in roles dict for API responses."""
    if not isinstance(roles, dict):
        return roles
    result = dict(roles)
    if result.get("webhook_secret"):
        result["webhook_secret"] = "*****"
    return result


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


async def update_board(
    session: AsyncSession,
    board: Board,
    name: str | None = None,
    description: str | None = None,
    project_type: str | None = None,
    roles: dict[str, object] | None = None,
) -> Board:
    """Update board fields. Only updates provided fields."""
    if name is not None:
        board.name = name
    if description is not None:
        board.description = description
    if project_type is not None:
        board.project_type = project_type
    if roles is not None:
        # Merge roles dict to preserve existing role definitions
        if isinstance(board.roles, dict):
            board.roles = {**board.roles, **roles}
        else:
            board.roles = roles

    await session.flush()
    return board
