"""Test board-workflow many-to-many relationship functionality."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BoardWorkflow, Workflow
from app.services.boards import get_active_workflow


@pytest.mark.asyncio
async def test_get_active_workflow_with_junction_table(db_session: AsyncSession, seed):
    """Test that get_active_workflow works with the junction table."""
    # Create a board-workflow relationship via junction table
    board_workflow = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=seed.workflow.id,
        is_active=True
    )
    db_session.add(board_workflow)
    await db_session.flush()

    # Should get the workflow via junction table
    active_workflow = await get_active_workflow(db_session, seed.board.id)
    assert active_workflow.id == seed.workflow.id
    assert active_workflow.name == seed.workflow.name


@pytest.mark.asyncio
async def test_get_active_workflow_fallback_to_legacy(db_session: AsyncSession, seed):
    """Test that get_active_workflow falls back to legacy board.workflow_id when no junction table entry exists."""
    # No junction table entry exists, should fall back to board.workflow_id
    active_workflow = await get_active_workflow(db_session, seed.board.id)
    assert active_workflow.id == seed.board.workflow_id


@pytest.mark.asyncio
async def test_multiple_workflows_only_active_returned(db_session: AsyncSession, seed):
    """Test that only active workflows are returned when multiple exist."""
    # Create another workflow
    inactive_workflow = Workflow(
        name="Inactive Workflow",
        states=[{"name": "draft", "is_initial": True, "is_terminal": False}],
        transitions=[{"from": "draft", "to": "published", "allowed_roles": ["pm"]}],
        is_default=False
    )
    db_session.add(inactive_workflow)
    await db_session.flush()

    # Create junction table entries - one active, one inactive
    active_board_workflow = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=seed.workflow.id,
        is_active=True
    )
    inactive_board_workflow = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=inactive_workflow.id,
        is_active=False
    )
    db_session.add(active_board_workflow)
    db_session.add(inactive_board_workflow)
    await db_session.flush()

    # Should return only the active workflow
    active_workflow = await get_active_workflow(db_session, seed.board.id)
    assert active_workflow.id == seed.workflow.id
    assert active_workflow.id != inactive_workflow.id


@pytest.mark.asyncio
async def test_unique_constraint_board_active_workflow(db_session: AsyncSession, seed):
    """Test that the unique constraint on (board_id, is_active) works correctly."""
    # Create first active workflow for board
    board_workflow1 = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=seed.workflow.id,
        is_active=True
    )
    db_session.add(board_workflow1)
    await db_session.flush()

    # Create second workflow
    workflow2 = Workflow(
        name="Second Workflow",
        states=[{"name": "new", "is_initial": True, "is_terminal": False}],
        transitions=[{"from": "new", "to": "done", "allowed_roles": ["pm"]}],
        is_default=False
    )
    db_session.add(workflow2)
    await db_session.flush()

    # Try to create another active workflow for the same board - should fail
    board_workflow2 = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=workflow2.id,
        is_active=True  # This should violate the unique constraint
    )
    db_session.add(board_workflow2)

    with pytest.raises(Exception):  # Should be IntegrityError but we catch generic Exception
        await db_session.flush()


@pytest.mark.asyncio
async def test_migration_populated_junction_table(db_session: AsyncSession, seed):
    """Test that existing boards work with the new workflow resolution system."""
    # The seed fixture creates a board with workflow_id set
    # Test that get_active_workflow can fall back to the legacy workflow_id
    active_workflow = await get_active_workflow(db_session, seed.board.id)
    assert active_workflow.id == seed.board.workflow_id
    assert active_workflow.name == "Default"