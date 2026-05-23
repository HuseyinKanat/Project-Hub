"""PH-102 — delete_workflow service + MCP tool: 8 AC scenarios.

Tests verify all 4 safety guards, success path, ticket orphan safety contract,
404 on invalid/missing workflow_id, and idempotent 404 on second call.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound, WorkflowDeletionBlocked
from app.db.models.core import Board, BoardWorkflow, Ticket, Workflow
from app.mcp.server import TOOLS, _dispatch_tool
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.workflows import delete_workflow, list_workflows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_board_with_active_workflow(
    session: AsyncSession,
    *,
    admin_id,
    key: str,
    workflow: Workflow,
) -> Board:
    """Create a board referencing the given workflow (legacy FK)."""
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
    return board


async def _attach_junction(
    session: AsyncSession,
    *,
    board: Board,
    workflow: Workflow,
    is_active: bool = False,
) -> BoardWorkflow:
    bw = BoardWorkflow(
        board_id=board.id,
        workflow_id=workflow.id,
        is_active=is_active,
    )
    session.add(bw)
    await session.flush()
    return bw


async def _make_orphan_workflow(session: AsyncSession) -> Workflow:
    """Create a non-default workflow with no board FK and no junction rows."""
    wf = Workflow(
        name="Deletable Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    session.add(wf)
    await session.flush()
    return wf


# ---------------------------------------------------------------------------
# AC-1: Success — 2 workflows, delete inactive one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_success_two_workflows(db_session: AsyncSession, seed):
    """GIVEN a non-default workflow with no legacy Board FK and no junction rows
    WHEN delete_workflow is called without board_id (no board-scope guards needed)
    THEN workflow row is deleted and NotFound is raised on a follow-up get.

    SQLite note: `board_workflows` has a full unique on board_id in SQLite (the partial
    index `WHERE is_active=true` becomes a full unique). To avoid constraint issues, this
    test exercises the success path without junction rows — guards 2 & 3 are skipped when
    board_id is None, and guard 4 doesn't fire because no Board.workflow_id points here.
    """
    deletable = Workflow(
        name="Old Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(deletable)
    await db_session.flush()
    deletable_id = str(deletable.id)

    # Attach a junction row on a fresh board (only one junction → list_workflows baseline)
    board = Board(
        key="SW",
        name="Success Board",
        description="",
        project_type="web_app",
        workflow_id=seed.workflow.id,  # legacy FK does NOT point at deletable
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()
    await _attach_junction(db_session, board=board, workflow=deletable, is_active=False)

    # Call without board_id → guards 2 & 3 skipped; guard 4 doesn't fire
    deleted_id = await delete_workflow(db_session, deletable_id)

    assert deleted_id == deletable_id

    # Workflow row gone
    remaining = (
        await db_session.execute(select(Workflow).where(Workflow.id == deletable.id))
    ).scalar_one_or_none()
    assert remaining is None

    # Junction row gone
    jct = (
        await db_session.execute(
            select(BoardWorkflow).where(BoardWorkflow.workflow_id == deletable.id)
        )
    ).scalar_one_or_none()
    assert jct is None


# ---------------------------------------------------------------------------
# AC-2: last_workflow guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_blocked_last_workflow(db_session: AsyncSession, seed):
    """GIVEN a board with only 1 workflow
    WHEN delete_workflow is called
    THEN WorkflowDeletionBlocked(reason='last_workflow') is raised.
    """
    # Use a fresh non-default workflow as the only workflow on a board
    only_wf = Workflow(
        name="Only Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(only_wf)
    await db_session.flush()

    # Board references only_wf via legacy FK
    board = Board(
        key="LW",
        name="Last WF Board",
        description="",
        project_type="web_app",
        workflow_id=only_wf.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    # Attach only one inactive junction so count == 1
    await _attach_junction(db_session, board=board, workflow=only_wf, is_active=False)

    with pytest.raises(WorkflowDeletionBlocked) as exc_info:
        await delete_workflow(db_session, str(only_wf.id), board_id=str(board.id))

    assert exc_info.value.reason == "last_workflow"
    assert exc_info.value.code == "workflow_deletion_blocked"
    assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# AC-3: active_workflow_cannot_delete guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_blocked_active(db_session: AsyncSession, seed):
    """GIVEN a board whose only junction row is the active workflow
    WHEN delete_workflow is called on that active workflow
    THEN WorkflowDeletionBlocked(reason='active_workflow_cannot_delete') is raised.

    SQLite note: only ONE junction row per board to avoid the full unique constraint on
    board_id. Guard 2 (active check) fires BEFORE guard 3 (last_workflow), so we don't
    need a second junction row to suppress guard 3 here.
    """
    active_wf = Workflow(
        name="Active WF",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(active_wf)
    await db_session.flush()

    board = Board(
        key="AW",
        name="Active WF Board",
        description="",
        project_type="web_app",
        workflow_id=active_wf.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    # Single junction row: active_wf is active on this board
    await _attach_junction(db_session, board=board, workflow=active_wf, is_active=True)

    with pytest.raises(WorkflowDeletionBlocked) as exc_info:
        await delete_workflow(db_session, str(active_wf.id), board_id=str(board.id))

    assert exc_info.value.reason == "active_workflow_cannot_delete"


# ---------------------------------------------------------------------------
# AC-4: default_workflow_protected guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_blocked_default(db_session: AsyncSession, seed):
    """GIVEN is_default=true workflow
    WHEN delete_workflow is called
    THEN WorkflowDeletionBlocked(reason='default_workflow_protected') is raised.
    """
    assert seed.workflow.is_default is True

    with pytest.raises(WorkflowDeletionBlocked) as exc_info:
        await delete_workflow(db_session, str(seed.workflow.id))

    assert exc_info.value.reason == "default_workflow_protected"


# ---------------------------------------------------------------------------
# AC-5: workflow_is_board_legacy_fk guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_blocked_legacy_fk(db_session: AsyncSession, seed):
    """GIVEN Board.workflow_id == target_workflow.id
    WHEN delete_workflow is called
    THEN WorkflowDeletionBlocked(reason='workflow_is_board_legacy_fk') is raised.
    """
    # Create a non-default workflow referenced by a board's legacy FK
    fk_wf = Workflow(
        name="FK Workflow",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(fk_wf)
    await db_session.flush()

    board = Board(
        key="FK",
        name="FK Board",
        description="",
        project_type="web_app",
        workflow_id=fk_wf.id,  # legacy FK pointing at fk_wf
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    # No junction rows — no board_id context needed for legacy FK guard to trigger
    with pytest.raises(WorkflowDeletionBlocked) as exc_info:
        await delete_workflow(db_session, str(fk_wf.id))

    assert exc_info.value.reason == "workflow_is_board_legacy_fk"


# ---------------------------------------------------------------------------
# AC-6: Ticket orphan safety contract — tickets not mutated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_tickets_not_mutated(db_session: AsyncSession, seed):
    """GIVEN a board with 5 tickets (3 backlog, 2 code_review) and a deletable workflow
    WHEN the deletable workflow is deleted (board_id=None to skip board-scope guards)
    THEN ticket count unchanged, ticket.state strings preserved (PH-101 orphan strategy).

    SQLite note: board_id=None is passed so guards 2 & 3 (which require multiple junction
    rows per board) are skipped. Guard 4 doesn't fire because board.workflow_id points at
    seed.workflow, not at the deletable workflow.
    """
    deletable = Workflow(
        name="Old WF with orphans",
        states=[{"name": "code_review", "label": "Code Review"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(deletable)
    await db_session.flush()

    board = Board(
        key="TP",
        name="Ticket Preservation Board",
        description="",
        project_type="web_app",
        workflow_id=seed.workflow.id,  # legacy FK does NOT point at deletable
        roles=DEFAULT_WEB_ROLES,
        created_by=seed.admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    # Single junction row for the deletable workflow (inactive)
    await _attach_junction(db_session, board=board, workflow=deletable, is_active=False)

    # Create 5 tickets: 3 backlog, 2 code_review
    for i in range(3):
        db_session.add(
            Ticket(
                key=f"TP-{i+1}",
                title=f"Backlog ticket {i+1}",
                type="feature",
                state="backlog",
                board_id=board.id,
                reporter_id=seed.admin.id,
            )
        )
    for i in range(2):
        db_session.add(
            Ticket(
                key=f"TP-{i+4}",
                title=f"Code review ticket {i+1}",
                type="feature",
                state="code_review",
                board_id=board.id,
                reporter_id=seed.admin.id,
            )
        )
    await db_session.flush()

    # Count before
    count_before = (
        await db_session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.board_id == board.id)
        )
    ).scalar_one()
    assert count_before == 5

    # Delete without board_id → guards 2 & 3 skipped; guard 4 doesn't fire
    await delete_workflow(db_session, str(deletable.id))

    # Count after
    count_after = (
        await db_session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.board_id == board.id)
        )
    ).scalar_one()
    assert count_after == 5

    # code_review state strings preserved
    cr_tickets = (
        await db_session.execute(
            select(Ticket).where(
                Ticket.board_id == board.id,
                Ticket.state == "code_review",
            )
        )
    ).scalars().all()
    assert len(cr_tickets) == 2


# ---------------------------------------------------------------------------
# AC-7: 404 on invalid workflow_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_not_found_invalid_id(db_session: AsyncSession, seed):
    """GIVEN an invalid/unknown workflow_id
    WHEN delete_workflow is called
    THEN NotFound is raised.
    """
    with pytest.raises(NotFound):
        await delete_workflow(db_session, "not-a-uuid")

    with pytest.raises(NotFound):
        await delete_workflow(db_session, "00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# AC-8: Idempotent 404 on second call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_workflow_idempotent_second_call(db_session: AsyncSession, seed):
    """GIVEN a successfully deleted workflow
    WHEN delete_workflow is called again with the same workflow_id
    THEN NotFound is raised (idempotency soft).

    SQLite note: board_id=None is passed to skip board-scope guards (guards 2 & 3 require
    multiple junction rows per board, which SQLite disallows via full unique on board_id).
    """
    deletable = Workflow(
        name="Will Be Deleted",
        states=[{"name": "todo", "label": "To Do"}],
        transitions=[],
        is_default=False,
    )
    db_session.add(deletable)
    await db_session.flush()
    deletable_id = str(deletable.id)

    # No board FK pointing at deletable → guard 4 won't fire
    # No board_id passed → guards 2 & 3 skipped

    # First call succeeds
    deleted_id = await delete_workflow(db_session, deletable_id)
    assert deleted_id == deletable_id

    # Second call → NotFound
    with pytest.raises(NotFound):
        await delete_workflow(db_session, deletable_id)


# ---------------------------------------------------------------------------
# MCP surface: TOOLS list contains delete_workflow
# ---------------------------------------------------------------------------


def test_delete_workflow_tool_in_tools_list():
    """GIVEN TOOLS catalog THEN delete_workflow entry exists with workflow.update permission."""
    tool = next((t for t in TOOLS if t.name == "delete_workflow"), None)
    assert tool is not None, "delete_workflow not found in TOOLS"
    assert tool.permission == "workflow.update"


# ---------------------------------------------------------------------------
# MCP dispatch: _dispatch_tool returns correct error envelope for guard failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_delete_workflow_guard_error_surface(db_session: AsyncSession, seed):
    """GIVEN _dispatch_tool('delete_workflow') called on is_default=true workflow
    THEN WorkflowDeletionBlocked raised (MCP layer catches it for isError envelope).
    """
    from app.core.exceptions import WorkflowDeletionBlocked as WDB

    with pytest.raises(WDB) as exc_info:
        await _dispatch_tool(
            "delete_workflow",
            {"workflow_id": str(seed.workflow.id)},
            seed.admin,
            db_session,
        )

    assert exc_info.value.reason == "default_workflow_protected"
    assert exc_info.value.code == "workflow_deletion_blocked"
