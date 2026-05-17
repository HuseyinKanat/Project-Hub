"""Tests for CLI commands — update_board_roles using real in-memory DB session."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import update_board_roles
from app.db.models import Board, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


async def _make_board_with_roles(session: AsyncSession, roles: object) -> Board:
    """Insert a Workflow + Board with given roles, return the Board."""
    workflow = Workflow(
        name="CLI Test Workflow",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()

    board = Board(
        key="CLT",
        name="CLI Test Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=roles,
    )
    session.add(board)
    await session.commit()
    return board


@pytest.mark.asyncio
async def test_update_board_roles_dirty_becomes_default(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A board with non-default roles is updated to DEFAULT_WEB_ROLES in the real DB."""
    board = await _make_board_with_roles(db_session, {"foo": "bar"})
    board_id = board.id

    await update_board_roles(db_session)

    # Reload from DB to verify flag_modified + commit took effect.
    reloaded = (
        await db_session.execute(select(Board).where(Board.id == board_id))
    ).scalar_one()
    assert reloaded.roles == DEFAULT_WEB_ROLES

    captured = capsys.readouterr()
    assert "Updated 1 board(s), 0 unchanged." in captured.out


@pytest.mark.asyncio
async def test_update_board_roles_idempotent(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First call updates dirty board; second call on already-correct board is unchanged."""
    await _make_board_with_roles(db_session, {"dirty": True})

    # First call: dirty → updated=1
    await update_board_roles(db_session)
    out1 = capsys.readouterr().out
    assert "Updated 1 board(s), 0 unchanged." in out1

    # Second call: board is now DEFAULT_WEB_ROLES → updated=0, unchanged=1
    await update_board_roles(db_session)
    out2 = capsys.readouterr().out
    assert "Updated 0 board(s), 1 unchanged." in out2


@pytest.mark.asyncio
async def test_update_board_roles_no_boards(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty DB (no boards) → Updated 0 board(s), 0 unchanged."""
    await update_board_roles(db_session)

    captured = capsys.readouterr()
    assert "Updated 0 board(s), 0 unchanged." in captured.out
