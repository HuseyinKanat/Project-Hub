"""Workflow configuration services."""

import copy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, ProjectHubError
from app.db.models.core import Board, BoardWorkflow, Workflow
from app.schemas import WorkflowCreate, WorkflowUpdate
from app.services.boards import get_active_workflow, get_board, parse_uuid


async def ensure_board_owned_workflow(
    session: AsyncSession,
    board_id: UUID,
) -> tuple[UUID, bool]:
    """PH-97: Ensure the board has its own private workflow (clone if shared).

    Uses SELECT FOR UPDATE to serialize concurrent calls — the second concurrent
    call will block until the first commits, then see shared_count==1 and return
    without cloning (idempotent).

    Returns:
        (workflow_id, cloned) — workflow_id is the board's current active
        workflow UUID (new clone UUID if cloned, existing UUID otherwise).
    """
    # Acquire a row-level lock on the active BoardWorkflow row so concurrent
    # calls for the same board are serialized (prevents double-clone race).
    bw_result = await session.execute(
        select(BoardWorkflow)
        .options(selectinload(BoardWorkflow.workflow))
        .where(
            BoardWorkflow.board_id == board_id,
            BoardWorkflow.is_active.is_(True),
        )
        .with_for_update()
    )
    bw = bw_result.scalar_one_or_none()

    if bw is None:
        # No active junction row — fall back to board.workflow_id
        board_result = await session.execute(
            select(Board)
            .options(selectinload(Board.workflow))
            .where(Board.id == board_id)
            .with_for_update()
        )
        board = board_result.scalar_one_or_none()
        if board is None:
            raise NotFound("board")

        wf = board.workflow

        # Count how many boards (via junction) share this workflow
        shared_count_result = await session.execute(
            select(func.count())
            .select_from(BoardWorkflow)
            .where(
                BoardWorkflow.workflow_id == wf.id,
                BoardWorkflow.is_active.is_(True),
            )
        )
        shared_count = shared_count_result.scalar_one()

        if shared_count > 1 or wf.is_default:
            new_wf = Workflow(
                name=f"{board.key} Workflow",
                states=copy.deepcopy(wf.states),
                transitions=copy.deepcopy(wf.transitions),
                is_default=False,
            )
            session.add(new_wf)
            await session.flush()

            # Create junction row for this board pointing to new workflow
            new_bw = BoardWorkflow(
                board_id=board.id,
                workflow_id=new_wf.id,
                is_active=True,
            )
            session.add(new_bw)

            # Align legacy FK
            board.workflow_id = new_wf.id
            await session.flush()
            return new_wf.id, True

        return wf.id, False

    wf = bw.workflow

    # Count active boards sharing this workflow
    shared_count_result = await session.execute(
        select(func.count())
        .select_from(BoardWorkflow)
        .where(
            BoardWorkflow.workflow_id == wf.id,
            BoardWorkflow.is_active.is_(True),
        )
    )
    shared_count = shared_count_result.scalar_one()

    if shared_count > 1 or wf.is_default:
        # Need to clone; load the board for its key
        board_result = await session.execute(
            select(Board).where(Board.id == board_id)
        )
        board = board_result.scalar_one_or_none()
        if board is None:
            raise NotFound("board")

        new_wf = Workflow(
            name=f"{board.key} Workflow",
            states=copy.deepcopy(wf.states),
            transitions=copy.deepcopy(wf.transitions),
            is_default=False,
        )
        session.add(new_wf)
        await session.flush()  # populate new_wf.id

        # Redirect junction row to new workflow
        bw.workflow_id = new_wf.id

        # Align legacy FK so get_active_workflow fallback returns correct workflow
        board.workflow_id = new_wf.id
        await session.flush()
        return new_wf.id, True

    # Already board-owned and not default — idempotent no-op
    return wf.id, False


async def get_field_gates(
    session: AsyncSession,
    workflow: Workflow,
    from_state: str,
    to_state: str,
) -> tuple[list[str], frozenset[str]]:
    """
    Return (required_fields, exempt_types) for a specific transition.

    Args:
        session: Database session
        workflow: Workflow object to check
        from_state: Starting state for the transition
        to_state: Target state for the transition

    Returns:
        Tuple of (required_fields, exempt_ticket_types)
        Returns ([], frozenset()) if no field_gates found for the transition
    """

    # Find matching transition
    for transition in workflow.transitions:
        transition_from = str(transition.get("from", ""))
        transition_to = str(transition.get("to", ""))

        if transition_from == from_state and transition_to == to_state:
            field_gates = transition.get("field_gates")
            if field_gates:
                required_fields = field_gates.get("required_fields", [])
                exempt_types = frozenset(field_gates.get("exempt_ticket_types", []))
                return required_fields, exempt_types

    # No field gates found for this transition
    return [], frozenset()


async def get_field_gates_for_ticket_transition(
    session: AsyncSession,
    board_id: UUID,
    from_state: str,
    to_state: str,
) -> tuple[list[str], frozenset[str]]:
    """
    Return (required_fields, exempt_types) for a transition within a board's active workflow.

    This is the main entry point for field gate checking that handles workflow resolution.

    Args:
        session: Database session
        board_id: UUID of the board to get workflow from
        from_state: Starting state for the transition
        to_state: Target state for the transition

    Returns:
        Tuple of (required_fields, exempt_ticket_types)
        Returns ([], frozenset()) if no field_gates found
    """
    workflow = await get_active_workflow(session, board_id)
    return await get_field_gates(session, workflow, from_state, to_state)


# Workflow management functions

async def create_workflow(
    session: AsyncSession,
    payload: WorkflowCreate,
    board_id: str | None = None,
) -> Workflow:
    """Create a new workflow and optionally attach it to a board.

    If board_id is provided, a BoardWorkflow junction row (is_active=False) is
    inserted so that list_workflows(board_id) can return the new workflow.
    """
    workflow = Workflow(
        name=payload.name,
        states=payload.states,
        transitions=payload.transitions,
        is_default=payload.is_default,
    )
    session.add(workflow)
    await session.flush()  # populate workflow.id

    if board_id is not None:
        board = await get_board(session, board_id)
        junction = BoardWorkflow(
            board_id=board.id,
            workflow_id=workflow.id,
            is_active=False,
        )
        session.add(junction)
        await session.flush()

    return workflow


async def get_workflow(session: AsyncSession, workflow_id: str) -> Workflow:
    """Get workflow by UUID."""
    workflow_uuid = parse_uuid(workflow_id)
    if workflow_uuid is None:
        raise NotFound("workflow")

    workflow = (
        await session.execute(select(Workflow).where(Workflow.id == workflow_uuid))
    ).scalar_one_or_none()

    if workflow is None:
        raise NotFound("workflow")
    return workflow


async def list_workflows(session: AsyncSession, board_id: str | None = None) -> list[Workflow]:
    """List workflows, optionally filtered by board."""
    if board_id is None:
        # Return all workflows
        result = await session.execute(select(Workflow).order_by(Workflow.name))
        return list(result.scalars())

    # Get workflows for specific board
    board = await get_board(session, board_id)

    # Get workflows associated with this board via BoardWorkflow junction table
    result = await session.execute(
        select(Workflow)
        .join(BoardWorkflow)
        .where(BoardWorkflow.board_id == board.id)
        .order_by(Workflow.name)
    )
    workflows = list(result.scalars())

    # Also include the legacy workflow_id if it exists and isn't already included
    if board.workflow_id:
        workflow_ids = {w.id for w in workflows}
        if board.workflow_id not in workflow_ids:
            legacy_workflow = await get_workflow(session, str(board.workflow_id))
            workflows.insert(0, legacy_workflow)

    return workflows


async def update_workflow(
    session: AsyncSession,
    workflow_id: str,
    payload: WorkflowUpdate,
    board_id: str | None = None,
) -> Workflow:
    """Update an existing workflow.

    If board_id is provided, ensure_board_owned_workflow is called first so
    the mutation operates on the board's private copy (cloning if needed).
    """
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    workflow = await get_workflow(session, effective_workflow_id)

    if payload.name is not None:
        workflow.name = payload.name
    if payload.states is not None:
        workflow.states = payload.states
    if payload.transitions is not None:
        workflow.transitions = payload.transitions
    if payload.is_default is not None:
        workflow.is_default = payload.is_default

    await session.flush()
    return workflow


async def add_transition(
    session: AsyncSession,
    workflow_id: str,
    from_state: str,
    to_state: str,
    allowed_roles: list[str] | None = None,
    field_gates: dict[str, object] | None = None,
    board_id: str | None = None,
) -> Workflow:
    """Add a new transition to a workflow.

    If board_id is provided, ensure_board_owned_workflow is called first.
    """
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    workflow = await get_workflow(session, effective_workflow_id)

    # Check if transition already exists
    for transition in workflow.transitions:
        if (
            transition.get("from") == from_state
            and transition.get("to") == to_state
        ):
            raise ProjectHubError(
                f"Transition from '{from_state}' to '{to_state}' already exists"
            )

    new_transition = {
        "from": from_state,
        "to": to_state,
    }

    if allowed_roles:
        new_transition["allowed_roles"] = allowed_roles

    if field_gates:
        new_transition["field_gates"] = field_gates

    workflow.transitions = [*workflow.transitions, new_transition]
    await session.flush()
    return workflow


async def delete_transition(
    session: AsyncSession,
    workflow_id: str,
    from_state: str,
    to_state: str,
    board_id: str | None = None,
) -> Workflow:
    """Remove a transition from a workflow.

    If board_id is provided, ensure_board_owned_workflow is called first.
    """
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    workflow = await get_workflow(session, effective_workflow_id)

    # Find and remove the transition
    updated_transitions = []
    found = False

    for transition in workflow.transitions:
        if (
            transition.get("from") == from_state
            and transition.get("to") == to_state
        ):
            found = True
            continue  # Skip this transition (delete it)
        updated_transitions.append(transition)

    if not found:
        raise NotFound(f"transition from '{from_state}' to '{to_state}'")

    workflow.transitions = updated_transitions
    await session.flush()
    return workflow


async def set_field_gates(
    session: AsyncSession,
    workflow_id: str,
    from_state: str,
    to_state: str,
    field_gates: dict[str, object],
    board_id: str | None = None,
) -> Workflow:
    """Update field gates for a specific transition.

    If board_id is provided, ensure_board_owned_workflow is called first.
    """
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    workflow = await get_workflow(session, effective_workflow_id)

    # Find and update the transition
    updated = False
    for transition in workflow.transitions:
        if (
            transition.get("from") == from_state
            and transition.get("to") == to_state
        ):
            transition["field_gates"] = field_gates
            updated = True
            break

    if not updated:
        raise NotFound(f"transition from '{from_state}' to '{to_state}'")

    # Mark the transitions as updated for SQLAlchemy
    workflow.transitions = list(workflow.transitions)
    await session.flush()
    return workflow


async def activate_workflow(session: AsyncSession, board_id: str, workflow_id: str) -> None:
    """Activate a workflow for a board, deactivating the current one.

    PH-97: After activating, ensure_board_owned_workflow is called so the board
    never shares a workflow with another board (clones if necessary).
    """
    board = await get_board(session, board_id)
    workflow = await get_workflow(session, workflow_id)

    # Deactivate any currently active workflow for this board
    await session.execute(
        BoardWorkflow.__table__.update()
        .where(
            (BoardWorkflow.board_id == board.id) & (BoardWorkflow.is_active.is_(True))
        )
        .values(is_active=False)
    )

    # Check if this workflow is already associated with the board
    existing = (
        await session.execute(
            select(BoardWorkflow).where(
                (BoardWorkflow.board_id == board.id)
                & (BoardWorkflow.workflow_id == workflow.id)
            )
        )
    ).scalar_one_or_none()

    if existing:
        # Reactivate existing association
        existing.is_active = True
    else:
        # Create new association
        board_workflow = BoardWorkflow(
            board_id=board.id,
            workflow_id=workflow.id,
            is_active=True,
        )
        session.add(board_workflow)

    await session.flush()

    # PH-97: ensure this board now owns its workflow privately (clone if shared)
    await ensure_board_owned_workflow(session, board.id)


async def deactivate_workflow(session: AsyncSession, board_id: str) -> None:
    """Deactivate any active workflow for a board."""
    board = await get_board(session, board_id)

    await session.execute(
        BoardWorkflow.__table__.update()
        .where(
            (BoardWorkflow.board_id == board.id) & (BoardWorkflow.is_active.is_(True))
        )
        .values(is_active=False)
    )

    await session.flush()