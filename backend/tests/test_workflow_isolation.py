"""PH-97 — Workflow isolation tests: clone default workflow per-board on first edit.

Tests verify the ensure_board_owned_workflow helper across 8 scenarios derived
from the AC list approved by the Architect.
"""


import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Board, BoardWorkflow, Ticket, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS
from app.services.workflows import activate_workflow, ensure_board_owned_workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_board(
    session: AsyncSession,
    *,
    key: str,
    workflow: Workflow,
    admin_id,
) -> Board:
    """Create a minimal Board row referencing the given workflow."""
    from app.services.defaults import DEFAULT_WEB_ROLES

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


async def _attach_active_junction(
    session: AsyncSession,
    *,
    board: Board,
    workflow: Workflow,
) -> BoardWorkflow:
    """Insert an active BoardWorkflow junction row."""
    bw = BoardWorkflow(
        board_id=board.id,
        workflow_id=workflow.id,
        is_active=True,
    )
    session.add(bw)
    await session.flush()
    return bw


# ---------------------------------------------------------------------------
# 1. Shared default workflow triggers a clone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_default_triggers_clone(db_session: AsyncSession, seed):
    """When a board's active workflow is_default=True, ensure_board_owned_workflow
    must produce a new private clone (cloned=True) with is_default=False."""
    assert seed.workflow.is_default is True  # pre-condition

    # Give the board an active junction row pointing at the default workflow.
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )

    new_wf_id, cloned = await ensure_board_owned_workflow(db_session, seed.board.id)

    assert cloned is True
    assert new_wf_id != seed.workflow.id

    # The clone must not be marked as default.
    clone = (
        await db_session.execute(select(Workflow).where(Workflow.id == new_wf_id))
    ).scalar_one()
    assert clone.is_default is False

    # The original default workflow must still exist, untouched.
    original = (
        await db_session.execute(
            select(Workflow).where(Workflow.id == seed.workflow.id)
        )
    ).scalar_one()
    assert original is not None
    assert original.is_default is True


# ---------------------------------------------------------------------------
# 2. Already board-owned workflow — no clone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_owned_no_clone(db_session: AsyncSession, seed):
    """If the board already has a private workflow (is_default=False, shared by
    no other board), ensure_board_owned_workflow must be a no-op (cloned=False)."""
    # Create a private workflow exclusively for this board.
    private_wf = Workflow(
        name="Private Workflow",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    db_session.add(private_wf)
    await db_session.flush()

    # Update board's legacy FK to the private workflow.
    seed.board.workflow_id = private_wf.id
    await db_session.flush()

    # Attach active junction row.
    await _attach_active_junction(
        db_session, board=seed.board, workflow=private_wf
    )

    original_wf_id, cloned = await ensure_board_owned_workflow(
        db_session, seed.board.id
    )

    assert cloned is False
    assert original_wf_id == private_wf.id


# ---------------------------------------------------------------------------
# 3. Two boards sharing a workflow — first call clones, second call is no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_clone_idempotent(db_session: AsyncSession, seed):
    """When two boards share the same workflow via junction rows, calling
    ensure_board_owned_workflow for board A clones the workflow; calling it again
    immediately returns cloned=False (idempotent)."""
    # Create a second board sharing the same workflow.
    board_b = await _create_board(
        db_session,
        key="BB",
        workflow=seed.workflow,
        admin_id=seed.admin.id,
    )
    # Both boards get active junction rows pointing to the shared workflow.
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )
    await _attach_active_junction(
        db_session, board=board_b, workflow=seed.workflow
    )

    # First call: board_a should get a clone.
    wf_id_first, cloned_first = await ensure_board_owned_workflow(
        db_session, seed.board.id
    )
    assert cloned_first is True

    # Second call on the same board: must be a no-op.
    wf_id_second, cloned_second = await ensure_board_owned_workflow(
        db_session, seed.board.id
    )
    assert cloned_second is False
    assert wf_id_second == wf_id_first


# ---------------------------------------------------------------------------
# 4. activate_workflow path — activating a shared workflow clones it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_foreign_workflow_clones(db_session: AsyncSession, seed):
    """A board activating a workflow that is_default=True should yield a
    per-board clone: the activated workflow itself stays intact while a
    private copy is produced for this board.
    """
    # Simulates post-activate situation: board has junction row on default wf.
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )
    assert seed.workflow.is_default is True

    wf_id, cloned = await ensure_board_owned_workflow(db_session, seed.board.id)

    assert cloned is True

    # Clone exists and is private.
    clone = (
        await db_session.execute(select(Workflow).where(Workflow.id == wf_id))
    ).scalar_one()
    assert clone.is_default is False

    # Board junction row now points to the clone.
    bw = (
        await db_session.execute(
            select(BoardWorkflow).where(
                BoardWorkflow.board_id == seed.board.id,
                BoardWorkflow.is_active.is_(True),
            )
        )
    ).scalar_one()
    assert bw.workflow_id == wf_id


# ---------------------------------------------------------------------------
# 5. Default workflow preserved after board gets its own clone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_workflow_preserved(db_session: AsyncSession, seed):
    """The original is_default workflow must remain in the database with
    is_default=True after cloning so future new boards can use it as a template.
    """
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )

    _, cloned = await ensure_board_owned_workflow(db_session, seed.board.id)
    assert cloned is True

    # Original default still exists and is still the default.
    original = (
        await db_session.execute(
            select(Workflow).where(Workflow.id == seed.workflow.id)
        )
    ).scalar_one()
    assert original is not None
    assert original.is_default is True
    assert original.states == seed.workflow.states
    assert original.transitions == seed.workflow.transitions


# ---------------------------------------------------------------------------
# 6. Migration idempotency — calling ensure twice on shared workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_idempotent(db_session: AsyncSession, seed):
    """ensure_board_owned_workflow is idempotent: running it twice on a board
    that shares a default workflow clones once, then returns the same ID without
    cloning again."""
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )

    wf_id_1, cloned_1 = await ensure_board_owned_workflow(db_session, seed.board.id)
    assert cloned_1 is True

    # Count workflows before second call.
    count_before = (
        await db_session.execute(
            select(Workflow).where(Workflow.is_default.is_(False))
        )
    ).scalars()
    private_count_before = len(list(count_before))

    wf_id_2, cloned_2 = await ensure_board_owned_workflow(db_session, seed.board.id)
    assert cloned_2 is False
    assert wf_id_2 == wf_id_1

    # No extra clone must have been created.
    count_after = (
        await db_session.execute(
            select(Workflow).where(Workflow.is_default.is_(False))
        )
    ).scalars()
    private_count_after = len(list(count_after))
    assert private_count_after == private_count_before


# ---------------------------------------------------------------------------
# 7. Legacy FK (Board.workflow_id) aligned after clone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_fk_aligned_after_clone(db_session: AsyncSession, seed):
    """After cloning, Board.workflow_id must equal the new clone's UUID so
    that the legacy FK fallback path in get_active_workflow is consistent."""
    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )

    new_wf_id, cloned = await ensure_board_owned_workflow(db_session, seed.board.id)
    assert cloned is True

    # Refresh the board from DB to pick up any flush changes.
    await db_session.refresh(seed.board)
    assert seed.board.workflow_id == new_wf_id


# ---------------------------------------------------------------------------
# 8. Existing tickets unaffected after clone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_tickets_unaffected_after_clone(db_session: AsyncSession, seed):
    """Existing tickets retain their current state strings after the board
    workflow is cloned — their state field must not be altered."""
    # Create a ticket in some state.
    ticket = Ticket(
        key="PH-1",
        board_id=seed.board.id,
        type="feature",
        title="Existing ticket",
        description="",
        state="backlog",
        reporter_id=seed.admin.id,
        priority="medium",
        labels=[],
    )
    db_session.add(ticket)
    await db_session.flush()

    await _attach_active_junction(
        db_session, board=seed.board, workflow=seed.workflow
    )

    _new_wf_id, cloned = await ensure_board_owned_workflow(db_session, seed.board.id)
    assert cloned is True

    # Ticket state is unchanged.
    await db_session.refresh(ticket)
    assert ticket.state == "backlog"
    assert ticket.board_id == seed.board.id


# ---------------------------------------------------------------------------
# 9. activate_workflow preserves all ticket states (PH-101 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_workflow_preserves_tickets(db_session: AsyncSession, seed):
    """activate_workflow must never touch tickets — count and state strings must
    be identical before and after activation.

    Covers two state variants:
    - 'backlog': a state present in the new workflow (safe path)
    - 'code_review': a state absent from the new workflow (orphan path)

    Ticket count must remain 10 and every state string must be unchanged.
    PH-101: backend safety contract for the 'no data loss on swap' guarantee.
    """
    from sqlalchemy import func

    # Create a second workflow that only contains 'backlog' and 'done'
    # (i.e. does NOT include 'code_review' or 'in_progress').
    new_workflow = Workflow(
        name="Bug Flow",
        states=[
            {"name": "backlog", "color": "#6b7280"},
            {"name": "done", "color": "#22c55e"},
        ],
        transitions=[
            {"from_state": "backlog", "to_state": "done", "name": "close"},
        ],
        is_default=False,
    )
    db_session.add(new_workflow)
    await db_session.flush()

    # Create 10 tickets: 8 with 'backlog' (valid in new wf) + 2 with 'code_review' (orphan).
    tickets = []
    for i in range(8):
        t = Ticket(
            key=f"PH-ACT-{i}",
            board_id=seed.board.id,
            type="feature",
            title=f"Ticket {i}",
            description="",
            state="backlog",
            reporter_id=seed.admin.id,
            priority="medium",
            labels=[],
        )
        db_session.add(t)
        tickets.append(t)

    for i in range(2):
        t = Ticket(
            key=f"PH-ACT-ORPHAN-{i}",
            board_id=seed.board.id,
            type="feature",
            title=f"Orphan ticket {i}",
            description="",
            state="code_review",
            reporter_id=seed.admin.id,
            priority="medium",
            labels=[],
        )
        db_session.add(t)
        tickets.append(t)

    await db_session.flush()

    # Count before activation.
    count_before = (
        await db_session.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.board_id == seed.board.id
            )
        )
    ).scalar_one()
    assert count_before == 10

    # Activate the new workflow — must not touch tickets.
    await activate_workflow(db_session, str(seed.board.id), str(new_workflow.id))

    # Count after activation — must be identical.
    count_after = (
        await db_session.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.board_id == seed.board.id
            )
        )
    ).scalar_one()
    assert count_after == count_before, (
        f"Ticket count changed after activate_workflow: {count_before} -> {count_after}"
    )

    # State strings must be unchanged for all tickets.
    for ticket in tickets:
        original_state = ticket.state
        await db_session.refresh(ticket)
        assert ticket.state == original_state, (
            f"Ticket {ticket.key} state mutated: '{original_state}' -> '{ticket.state}'"
        )
