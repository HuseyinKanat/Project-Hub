"""Test MCP workflow management tools."""

import pytest
from sqlalchemy import select

from app.db.models import Board, Workflow, BoardWorkflow
from app.mcp.server import _dispatch_tool


@pytest.mark.asyncio
async def test_create_workflow(db_session, seed):
    """Test creating a workflow via MCP."""
    payload = {
        "workflow": {
            "name": "Test Workflow",
            "states": [
                {"name": "todo", "label": "To Do"},
                {"name": "done", "label": "Done"},
            ],
            "transitions": [
                {"from": "todo", "to": "done", "allowed_roles": ["dev"]},
            ],
            "is_default": False,
        }
    }

    result = await _dispatch_tool("create_workflow", payload, seed.admin, db_session)

    assert "id" in result
    assert result["name"] == "Test Workflow"
    assert len(result["states"]) == 2
    assert len(result["transitions"]) == 1
    assert result["is_default"] is False


@pytest.mark.asyncio
async def test_list_workflows(db_session, seed):
    """Test listing workflows via MCP."""
    # Create a test workflow
    workflow = Workflow(
        name="Test List Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {"board_id": None}  # List all workflows

    result = await _dispatch_tool("list_workflows", payload, seed.admin, db_session)

    assert isinstance(result, list)
    assert len(result) >= 1
    workflow_names = [w["name"] for w in result]
    assert "Test List Workflow" in workflow_names


@pytest.mark.asyncio
async def test_update_workflow(db_session, seed):
    """Test updating a workflow via MCP."""
    # Create a workflow first
    workflow = Workflow(
        name="Original Name",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "fields": {
            "name": "Updated Name",
            "states": [
                {"name": "todo", "label": "To Do"},
                {"name": "done", "label": "Done"},
            ],
        },
    }

    result = await _dispatch_tool("update_workflow", payload, seed.admin, db_session)

    assert result["name"] == "Updated Name"
    assert len(result["states"]) == 2


@pytest.mark.asyncio
async def test_add_transition(db_session, seed):
    """Test adding a transition to a workflow via MCP."""
    workflow = Workflow(
        name="Test Workflow",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "done", "label": "Done"},
        ],
        transitions=[],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["dev", "pm"],
        "field_gates": {"required_fields": ["description"]},
    }

    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    transition = result["transitions"][0]
    assert transition["from"] == "todo"
    assert transition["to"] == "done"
    assert transition["allowed_roles"] == ["dev", "pm"]
    assert transition["field_gates"]["required_fields"] == ["description"]


@pytest.mark.asyncio
async def test_delete_transition(db_session, seed):
    """Test deleting a transition from a workflow via MCP."""
    workflow = Workflow(
        name="Test Workflow",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "done", "label": "Done"},
        ],
        transitions=[
            {"from": "todo", "to": "done", "allowed_roles": ["dev"]},
        ],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
    }

    result = await _dispatch_tool("delete_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 0


@pytest.mark.asyncio
async def test_set_field_gates(db_session, seed):
    """Test setting field gates for a transition via MCP."""
    workflow = Workflow(
        name="Test Workflow",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "done", "label": "Done"},
        ],
        transitions=[
            {"from": "todo", "to": "done", "allowed_roles": ["dev"]},
        ],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "field_gates": {
            "required_fields": ["description", "acceptance_criteria"],
            "exempt_ticket_types": ["hotfix"],
        },
    }

    result = await _dispatch_tool("set_field_gates", payload, seed.admin, db_session)

    transition = result["transitions"][0]
    assert transition["field_gates"]["required_fields"] == ["description", "acceptance_criteria"]
    assert transition["field_gates"]["exempt_ticket_types"] == ["hotfix"]


@pytest.mark.asyncio
async def test_activate_workflow(db_session, seed):
    """Test activating a workflow for a board via MCP."""
    workflow = Workflow(
        name="Test Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
    )
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "board_id": str(seed.board.id),
        "workflow_id": str(workflow.id),
    }

    result = await _dispatch_tool("activate_workflow", payload, seed.admin, db_session)

    assert result["status"] == "activated"

    # Verify the workflow is now active for the board
    board_workflow = (
        await db_session.execute(
            select(BoardWorkflow).where(
                BoardWorkflow.board_id == seed.board.id,
                BoardWorkflow.workflow_id == workflow.id,
                BoardWorkflow.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    assert board_workflow is not None


@pytest.mark.asyncio
async def test_deactivate_workflow(db_session, seed):
    """Test deactivating a workflow for a board via MCP."""
    # Create and activate a workflow first
    workflow = Workflow(
        name="Test Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
    )
    db_session.add(workflow)
    await db_session.flush()

    board_workflow = BoardWorkflow(
        board_id=seed.board.id,
        workflow_id=workflow.id,
        is_active=True,
    )
    db_session.add(board_workflow)
    await db_session.flush()

    payload = {"board_id": str(seed.board.id)}

    result = await _dispatch_tool("deactivate_workflow", payload, seed.admin, db_session)

    assert result["status"] == "deactivated"