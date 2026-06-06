"""Board queries and bootstrap helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.db.models import Board, BoardWorkflow, Workflow


def mask_webhook_secret(roles: dict[str, object]) -> dict[str, object]:
    """Mask webhook_secret and refresh_secret in roles dict for API responses."""
    if not isinstance(roles, dict):
        return roles
    result = dict(roles)
    if result.get("webhook_secret"):
        result["webhook_secret"] = "*****"
    if result.get("refresh_secret"):
        result["refresh_secret"] = "*****"
    return result


def parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


async def list_boards(session: AsyncSession) -> list[Board]:
    result = await session.execute(
        select(Board)
        .options(
            selectinload(Board.workflow),
            selectinload(Board.repository),
            selectinload(Board.sonarqube_metric),  # PH-193: eager-load health for board_response
        )
        .order_by(Board.key)
    )
    return list(result.scalars())


async def get_board(session: AsyncSession, board_id: str) -> Board:
    board_uuid = parse_uuid(board_id)
    statement = select(Board).options(
        selectinload(Board.workflow),
        selectinload(Board.repository),  # PH-150: eager-load repo for board_response
        selectinload(Board.sonarqube_metric),  # PH-193: eager-load health for board_response
    )
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


async def get_active_workflow(session: AsyncSession, board_id: UUID) -> Workflow:
    """Get the active workflow for a board.

    Falls back to board.workflow_id for backward compatibility
    until the migration to many-to-many is complete.
    """
    # Try to get active workflow from junction table
    board_workflow = (
        await session.execute(
            select(BoardWorkflow)
            .options(selectinload(BoardWorkflow.workflow))
            .where(
                BoardWorkflow.board_id == board_id,
                BoardWorkflow.is_active.is_(True)
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if board_workflow:
        return board_workflow.workflow

    # Fallback to the legacy workflow_id column
    board = await get_board(session, str(board_id))
    return board.workflow


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
