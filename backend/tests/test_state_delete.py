"""PH-106 — delete_state service + MCP tool: 8 AC scenarios.

Tests verify:
1. Success: no cascade (3-state workflow, no transitions)
2. Success with cascade: 2 transitions referencing the deleted state are removed
3. Guard tickets_exist: state has 1 live ticket → StateDeletionBlocked
4. Guard last_state: workflow.states.length == 1 → StateDeletionBlocked
5. Guard not_found: state_name not in workflow.states → NotFound
6. PH-97 clone-guard: shared workflow + board_id → cloned first, original unchanged
7. MCP dispatch (success): _dispatch_tool happy path returns expected dict
8. MCP dispatch (guard error surface): guard fires → ProjectHubError raised (isError=true)
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound, StateDeletionBlocked
from app.db.models.core import Board, BoardMembership, BoardWorkflow, Ticket, Workflow
from app.mcp.server import TOOLS, _dispatch_tool
from app.services.defaults import DEFAULT_WEB_ROLES
from app.services.workflows import delete_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_workflow_with_states(
    session: AsyncSession,
    *,
    name: str,
    states: list[dict],
    transitions: list[dict] | None = None,
    is_default: bool = False,
) -> Workflow:
    wf = Workflow(
        name=name,
        states=states,
        transitions=transitions or [],
        is_default=is_default,
    )
    session.add(wf)
    await session.flush()
    return wf


async def _make_board_with_workflow(
    session: AsyncSession,
    *,
    key: str,
    workflow: Workflow,
    admin_id,
) -> Board:
    board = Board(
        key=key,
        name=f"{key} Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin_id,
    )
    session.add(board)
    await session.flush()

    bw = BoardWorkflow(board_id=board.id, workflow_id=workflow.id, is_active=True)
    session.add(bw)

    session.add(BoardMembership(board_id=board.id, actor_id=admin_id, role="admin"))
    await session.flush()
    return board


async def _make_ticket_in_state(
    session: AsyncSession,
    *,
    key: str,
    state: str,
    board_id,
    reporter_id,
) -> Ticket:
    ticket = Ticket(
        key=key,
        title=f"Ticket in {state}",
        type="feature",
        state=state,
        board_id=board_id,
        reporter_id=reporter_id,
    )
    session.add(ticket)
    await session.flush()
    return ticket


# ---------------------------------------------------------------------------
# TC-1: Success — 3-state workflow, no tickets, no transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_success_no_cascade(db_session: AsyncSession, seed):
    """GIVEN a 3-state workflow with no transitions and no tickets in target state
    WHEN delete_state is called for the middle state
    THEN returns {deleted: True, state_name: X, removed_transitions: 0}
         and workflow.states shrinks from 3 to 2.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Three State WF",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "in_progress", "label": "In Progress"},
            {"name": "done", "label": "Done"},
        ],
    )

    result = await delete_state(db_session, str(wf.id), "in_progress")

    assert result["deleted"] is True
    assert result["state_name"] == "in_progress"
    assert result["removed_transitions"] == 0

    # Verify in-session state
    remaining_names = [s["name"] for s in wf.states]
    assert "in_progress" not in remaining_names
    assert len(remaining_names) == 2


# ---------------------------------------------------------------------------
# TC-2: Success with cascade — 2 transitions referencing state removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_success_cascade_transitions(db_session: AsyncSession, seed):
    """GIVEN a workflow where state 'review' has 2 transitions (in + out)
    WHEN delete_state is called for 'review'
    THEN returns removed_transitions=2 and those 2 transitions are gone;
         the unrelated transition (todo → done) is preserved.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Cascade WF",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "review", "label": "Review"},
            {"name": "done", "label": "Done"},
        ],
        transitions=[
            {"from": "todo", "to": "review", "allowed_roles": []},
            {"from": "review", "to": "done", "allowed_roles": []},
            {"from": "todo", "to": "done", "allowed_roles": []},  # unrelated, must survive
        ],
    )

    result = await delete_state(db_session, str(wf.id), "review")

    assert result["deleted"] is True
    assert result["state_name"] == "review"
    assert result["removed_transitions"] == 2

    # 'review' state gone
    assert all(s["name"] != "review" for s in wf.states)
    # Only the unrelated transition remains
    assert len(wf.transitions) == 1
    assert wf.transitions[0]["from"] == "todo"
    assert wf.transitions[0]["to"] == "done"


# ---------------------------------------------------------------------------
# TC-3: Guard tickets_exist — 1 live ticket in state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_blocked_tickets_exist(db_session: AsyncSession, seed):
    """GIVEN a workflow state 'testing' with 1 live ticket
    WHEN delete_state is called with board_id
    THEN StateDeletionBlocked(reason='tickets_exist', ticket_count=1) is raised.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Ticket Guard WF",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "testing", "label": "Testing"},
        ],
    )
    board = await _make_board_with_workflow(
        db_session, key="TG", workflow=wf, admin_id=seed.admin.id
    )
    await _make_ticket_in_state(
        db_session, key="TG-1", state="testing", board_id=board.id, reporter_id=seed.admin.id
    )

    with pytest.raises(StateDeletionBlocked) as exc_info:
        await delete_state(db_session, str(wf.id), "testing", board_id=str(board.id))

    exc = exc_info.value
    assert exc.reason == "tickets_exist"
    assert exc.state_name == "testing"
    assert exc.ticket_count == 1
    assert exc.code == "state_deletion_blocked"
    assert exc.status == 400


# ---------------------------------------------------------------------------
# TC-4: Guard last_state — workflow with exactly 1 state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_blocked_last_state(db_session: AsyncSession, seed):
    """GIVEN a workflow with only 1 state
    WHEN delete_state is called
    THEN StateDeletionBlocked(reason='last_state') is raised.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Single State WF",
        states=[{"name": "only", "label": "Only State"}],
    )

    with pytest.raises(StateDeletionBlocked) as exc_info:
        await delete_state(db_session, str(wf.id), "only")

    exc = exc_info.value
    assert exc.reason == "last_state"
    assert exc.state_name == "only"
    assert exc.code == "state_deletion_blocked"


# ---------------------------------------------------------------------------
# TC-5: Guard not_found — state_name not in workflow.states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_not_found_state_name(db_session: AsyncSession, seed):
    """GIVEN a workflow without state 'nonexistent'
    WHEN delete_state is called with state_name='nonexistent'
    THEN NotFound is raised.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="NF Test WF",
        states=[
            {"name": "todo", "label": "To Do"},
            {"name": "done", "label": "Done"},
        ],
    )

    with pytest.raises(NotFound) as exc_info:
        await delete_state(db_session, str(wf.id), "nonexistent")

    assert "state" in exc_info.value.resource
    assert "nonexistent" in exc_info.value.resource


# ---------------------------------------------------------------------------
# TC-6: PH-97 clone-guard — shared is_default workflow → board gets private copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_state_ph97_clone_guard(db_session: AsyncSession, seed):
    """GIVEN the default shared workflow (is_default=True) with 3 states
    WHEN delete_state is called with board_id (triggering ensure_board_owned_workflow)
    THEN a private clone is created for the board, state removed from clone only,
         original shared workflow is unchanged.
    """
    # The seed workflow is is_default=True with DEFAULT_STATES (backlog, to_do, etc.)
    # We need a fresh board that has the default as its active workflow.
    board = Board(
        key="CL",
        name="Clone Board",
        description="",
        project_type="web_app",
        workflow_id=seed.workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    bw = BoardWorkflow(board_id=board.id, workflow_id=seed.workflow.id, is_active=True)
    db_session.add(bw)
    db_session.add(BoardMembership(board_id=board.id, actor_id=seed.admin.id, role="admin"))
    await db_session.flush()

    original_state_count = len(seed.workflow.states)
    assert original_state_count >= 3, "Need at least 3 states to delete one and keep 1+ guard"

    # Pick the last state name in the default workflow (not the first — keep a state for guard)
    target_state_name = seed.workflow.states[-1]["name"]

    result = await delete_state(
        db_session,
        str(seed.workflow.id),  # original shared workflow id
        target_state_name,
        board_id=str(board.id),
    )

    assert result["deleted"] is True
    assert result["state_name"] == target_state_name

    # Original shared workflow UNCHANGED
    orig_names = [s["name"] for s in seed.workflow.states]
    assert target_state_name in orig_names, "Original shared workflow must not be mutated"

    # Board now has a new private workflow; retrieve it via the active BoardWorkflow junction.
    # Save board.id before expire_all to avoid a lazy-load trap after expiry.
    board_id_uuid = board.id
    original_wf_id = seed.workflow.id

    # We must expire session objects to avoid the SQLAlchemy identity-map cache returning
    # the stale pre-clone workflow reference via bw.workflow relationship.
    db_session.expire_all()

    from sqlalchemy.orm import selectinload
    active_bw = (
        await db_session.execute(
            select(BoardWorkflow)
            .options(selectinload(BoardWorkflow.workflow))
            .where(BoardWorkflow.board_id == board_id_uuid, BoardWorkflow.is_active.is_(True))
        )
    ).scalar_one_or_none()
    assert active_bw is not None
    private_wf = active_bw.workflow
    assert private_wf.id != original_wf_id, "Board must now own a private clone"
    private_names = [s["name"] for s in private_wf.states]
    assert target_state_name not in private_names, "State removed from the private clone"


# ---------------------------------------------------------------------------
# TC-7: MCP dispatch success — _dispatch_tool returns correct dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_delete_state_success(db_session: AsyncSession, seed):
    """GIVEN _dispatch_tool('delete_state', ...) called on a valid workflow
    WHEN the delete succeeds
    THEN a dict with deleted=True, state_name, removed_transitions is returned.
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Dispatch Happy WF",
        states=[
            {"name": "open", "label": "Open"},
            {"name": "closed", "label": "Closed"},
        ],
    )

    result = await _dispatch_tool(
        "delete_state",
        {"workflow_id": str(wf.id), "state_name": "closed"},
        seed.admin,
        db_session,
    )

    assert result["deleted"] is True
    assert result["state_name"] == "closed"
    assert isinstance(result["removed_transitions"], int)


# ---------------------------------------------------------------------------
# TC-8: MCP dispatch guard — ProjectHubError raised (isError surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_delete_state_guard_error_surface(db_session: AsyncSession, seed):
    """GIVEN _dispatch_tool('delete_state') called on a single-state workflow
    WHEN the last_state guard fires
    THEN StateDeletionBlocked is raised — the MCP JSON-RPC layer wraps this as isError=true.

    Also verifies that state_name and reason attributes are present on the exception
    so the mcp_jsonrpc error handler can surface them in the detail payload (AC-13).
    """
    wf = await _make_workflow_with_states(
        db_session,
        name="Single State Dispatch WF",
        states=[{"name": "solo", "label": "Solo"}],
    )

    with pytest.raises(StateDeletionBlocked) as exc_info:
        await _dispatch_tool(
            "delete_state",
            {"workflow_id": str(wf.id), "state_name": "solo"},
            seed.admin,
            db_session,
        )

    exc = exc_info.value
    assert exc.reason == "last_state"
    assert exc.state_name == "solo"
    assert exc.code == "state_deletion_blocked"

    # Verify that the TOOLS catalog also has delete_state (belt-and-suspenders)
    tool = next((t for t in TOOLS if t.name == "delete_state"), None)
    assert tool is not None, "delete_state must be in TOOLS catalog"
    assert tool.permission == "workflow.update"
