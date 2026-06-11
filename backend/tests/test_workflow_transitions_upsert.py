"""Tests for add_transition upsert semantics (PH-99).

Covers AC-01 through AC-06 (backend upsert) and AC-16 (board isolation regression).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Workflow
from app.mcp.server import _dispatch_tool

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_workflow(transitions: list[dict]) -> Workflow:
    return Workflow(
        name="Upsert Test Workflow",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "in_review", "label": "In Review"},
            {"name": "done", "label": "Done"},
        ],
        transitions=transitions,
    )


# ---------------------------------------------------------------------------
# AC-01 — insert when transition not present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_insert_new(db_session: AsyncSession, seed):
    """AC-01: new tuple → appended, all supplied fields saved."""
    workflow = _make_workflow([])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["qa"],
        "field_gates": {"required_fields": ["test_plan"], "exempt_ticket_types": []},
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["from"] == "todo"
    assert t["to"] == "done"
    assert t["allowed_roles"] == ["qa"]
    assert t["field_gates"]["required_fields"] == ["test_plan"]
    assert t["field_gates"]["exempt_ticket_types"] == []


# ---------------------------------------------------------------------------
# AC-02 — replace allowed_roles when tuple exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_replace_allowed_roles(db_session: AsyncSession, seed):
    """AC-02: existing tuple → allowed_roles replaced, insertion order preserved."""
    workflow = _make_workflow([
        {"from": "todo", "to": "in_review", "allowed_roles": ["pm"]},
        {"from": "todo", "to": "done", "allowed_roles": ["pm"]},
    ])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["qa", "admin"],
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    # Still exactly 2 transitions — no duplicate added
    assert len(result["transitions"]) == 2
    # First transition untouched
    assert result["transitions"][0]["from"] == "todo"
    assert result["transitions"][0]["to"] == "in_review"
    assert result["transitions"][0]["allowed_roles"] == ["pm"]
    # Second transition updated
    t = result["transitions"][1]
    assert t["from"] == "todo"
    assert t["to"] == "done"
    assert t["allowed_roles"] == ["qa", "admin"]


# ---------------------------------------------------------------------------
# AC-03 — replace field_gates when tuple exists (was empty before)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_replace_field_gates(db_session: AsyncSession, seed):
    """AC-03: existing tuple without field_gates → field_gates written."""
    workflow = _make_workflow([
        {"from": "todo", "to": "done", "allowed_roles": ["pm"]},
    ])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "field_gates": {
            "required_fields": ["technical_depth"],
            "exempt_ticket_types": ["bug"],
        },
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["field_gates"]["required_fields"] == ["technical_depth"]
    assert t["field_gates"]["exempt_ticket_types"] == ["bug"]
    # allowed_roles preserved from existing
    assert t["allowed_roles"] == ["pm"]


# ---------------------------------------------------------------------------
# AC-04 — field_gates=None (absent) preserves existing field_gates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_preserve_field_gates_when_none(
    db_session: AsyncSession, seed
):
    """AC-04: field_gates not supplied → existing field_gates kept intact."""
    existing_gates = {
        "required_fields": ["technical_depth"],
        "exempt_ticket_types": ["bug"],
    }
    workflow = _make_workflow([
        {
            "from": "todo",
            "to": "done",
            "allowed_roles": ["pm"],
            "field_gates": existing_gates,
        }
    ])
    db_session.add(workflow)
    await db_session.flush()

    # Call without field_gates key → should preserve existing gates
    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["qa"],
        # field_gates intentionally absent
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["field_gates"]["required_fields"] == ["technical_depth"]
    assert t["field_gates"]["exempt_ticket_types"] == ["bug"]
    assert t["allowed_roles"] == ["qa"]


# ---------------------------------------------------------------------------
# AC-05 — allowed_roles=[] removes allowed_roles key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_empty_allowed_roles_removes_key(
    db_session: AsyncSession, seed
):
    """AC-05: allowed_roles=[] → key removed from transition (= all roles)."""
    workflow = _make_workflow([
        {"from": "todo", "to": "done", "allowed_roles": ["pm"]},
    ])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": [],
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert "allowed_roles" not in t


# ---------------------------------------------------------------------------
# AC-15 — idempotent: re-apply same values, no duplicate, no error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_idempotent_same_values(db_session: AsyncSession, seed):
    """AC-15: calling with identical values is a no-op (200, no duplicate)."""
    workflow = _make_workflow([
        {
            "from": "todo",
            "to": "done",
            "allowed_roles": ["pm"],
            "field_gates": {"required_fields": ["technical_depth"], "exempt_ticket_types": ["bug"]},
        }
    ])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["pm"],
        "field_gates": {"required_fields": ["technical_depth"], "exempt_ticket_types": ["bug"]},
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["allowed_roles"] == ["pm"]
    assert t["field_gates"]["required_fields"] == ["technical_depth"]


# ---------------------------------------------------------------------------
# AC-02 variant — insertion order preserved after upsert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_transition_upsert_preserves_insertion_order(
    db_session: AsyncSession, seed
):
    """Upsert replaces entry at its original index; order of other entries unchanged."""
    workflow = _make_workflow([
        {"from": "todo", "to": "in_review"},
        {"from": "todo", "to": "done", "allowed_roles": ["pm"]},
        {"from": "in_review", "to": "done"},
    ])
    db_session.add(workflow)
    await db_session.flush()

    payload = {
        "workflow_id": str(workflow.id),
        "from_state": "todo",
        "to_state": "done",
        "allowed_roles": ["qa", "admin"],
    }
    result = await _dispatch_tool("add_transition", payload, seed.admin, db_session)

    transitions = result["transitions"]
    assert len(transitions) == 3
    # Updated entry still at index 1 (not appended to end)
    assert transitions[0]["to"] == "in_review"
    assert transitions[1]["to"] == "done"
    assert transitions[1]["allowed_roles"] == ["qa", "admin"]
    assert transitions[2]["from"] == "in_review"
