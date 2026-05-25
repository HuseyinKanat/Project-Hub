"""Workflow configuration services."""

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, StateDeletionBlocked, WorkflowDeletionBlocked
from app.db.models.core import Board, BoardWorkflow, Ticket, Workflow
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
    """Add or update a transition in a workflow (upsert semantics).

    If the (from_state, to_state) tuple already exists in the workflow's
    transitions list, the existing entry is updated in-place (insertion order
    preserved).  Otherwise the new transition is appended.

    Upsert rules:
    - allowed_roles=None  → keep existing allowed_roles untouched
    - allowed_roles=[]    → remove allowed_roles key (= all roles allowed)
    - allowed_roles=[...] → replace with supplied list
    - field_gates=None    → keep existing field_gates untouched
    - field_gates={...}   → full replace (PUT semantic, not PATCH)

    If board_id is provided, ensure_board_owned_workflow is called first.
    """
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    workflow = await get_workflow(session, effective_workflow_id)

    # Search for existing (from, to) tuple in transitions list
    existing_index: int | None = None
    for idx, transition in enumerate(workflow.transitions):
        if (
            transition.get("from") == from_state
            and transition.get("to") == to_state
        ):
            existing_index = idx
            break

    if existing_index is not None:
        # In-place replace — keep insertion order, carry over fields not supplied
        existing = dict(workflow.transitions[existing_index])
        updated: dict[str, object] = {
            "from": from_state,
            "to": to_state,
        }
        # allowed_roles: None=keep, []=delete, [...]= replace
        if allowed_roles is None:
            if "allowed_roles" in existing:
                updated["allowed_roles"] = existing["allowed_roles"]
        elif len(allowed_roles) > 0:
            updated["allowed_roles"] = allowed_roles
        # else allowed_roles==[] → key omitted (= all roles)

        # field_gates: None=keep, {...}=full replace (incl. empty dict)
        if field_gates is None:
            if "field_gates" in existing:
                updated["field_gates"] = existing["field_gates"]
        else:
            updated["field_gates"] = field_gates

        updated_transitions = list(workflow.transitions)
        updated_transitions[existing_index] = updated
        workflow.transitions = updated_transitions
    else:
        # New transition — append
        new_transition: dict[str, object] = {
            "from": from_state,
            "to": to_state,
        }
        if allowed_roles:
            new_transition["allowed_roles"] = allowed_roles
        if field_gates is not None:
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

    Tickets are never modified by this operation. Tickets whose state name does
    not exist in the new workflow remain visible with their original state string
    (orphan state, kanban shows them in a fallback column or hides depending on
    UI). This is intentional — preserving the original state string allows a
    swap back to the old workflow to restore those tickets automatically.
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


async def delete_workflow(
    session: AsyncSession,
    workflow_id: str,
    board_id: str | None = None,
) -> str:
    """Delete a workflow from a board with 4 safety guards.

    Guards (in order):
    1. default_workflow_protected: is_default=True workflows cannot be deleted.
    2. active_workflow_cannot_delete: the board's active workflow cannot be deleted
       (user must activate another workflow first).
    3. last_workflow: the last remaining workflow for the board cannot be deleted
       (min-1 invariant).
    4. workflow_is_board_legacy_fk: any board whose legacy workflow_id FK points
       at this workflow cannot be orphaned.

    On success: junction rows then the workflow row are deleted.
    Tickets are NOT touched (PH-101 orphan strategy).

    Returns:
        The deleted workflow's UUID string.

    Raises:
        NotFound: workflow_id invalid or not found.
        WorkflowDeletionBlocked: one of the 4 guards fires.
    """
    wf_uuid = parse_uuid(workflow_id)
    if wf_uuid is None:
        raise NotFound("workflow")

    # Lock the workflow row for concurrent-delete safety
    workflow_result = await session.execute(
        select(Workflow).where(Workflow.id == wf_uuid).with_for_update()
    )
    workflow = workflow_result.scalar_one_or_none()
    if workflow is None:
        raise NotFound("workflow")

    # Guard 1: default workflow protected
    if workflow.is_default:
        raise WorkflowDeletionBlocked(
            reason="default_workflow_protected",
            workflow_id=str(wf_uuid),
        )

    # Guards 2 & 3 require board_id context
    if board_id is not None:
        board = await get_board(session, board_id)

        # Guard 2: active workflow cannot be deleted
        active_bw = (
            await session.execute(
                select(BoardWorkflow).where(
                    BoardWorkflow.board_id == board.id,
                    BoardWorkflow.workflow_id == wf_uuid,
                    BoardWorkflow.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if active_bw is not None:
            raise WorkflowDeletionBlocked(
                reason="active_workflow_cannot_delete",
                workflow_id=str(wf_uuid),
            )

        # Guard 3: last workflow (min-1 invariant)
        junction_count_result = await session.execute(
            select(func.count())
            .select_from(BoardWorkflow)
            .where(BoardWorkflow.board_id == board.id)
        )
        junction_count = junction_count_result.scalar_one()
        if junction_count <= 1:
            raise WorkflowDeletionBlocked(
                reason="last_workflow",
                workflow_id=str(wf_uuid),
            )

    # Guard 4: workflow is legacy FK for any board (NOT NULL orphan protection)
    legacy_board_result = await session.execute(
        select(Board).where(Board.workflow_id == wf_uuid).limit(1)
    )
    legacy_board = legacy_board_result.scalar_one_or_none()
    if legacy_board is not None:
        raise WorkflowDeletionBlocked(
            reason="workflow_is_board_legacy_fk",
            workflow_id=str(wf_uuid),
        )

    # All guards passed — cleanup junction rows first (no FK CASCADE)
    await session.execute(
        BoardWorkflow.__table__.delete().where(
            BoardWorkflow.__table__.c.workflow_id == wf_uuid
        )
    )

    # Delete the workflow row; tickets are intentionally NOT touched (PH-101)
    await session.delete(workflow)
    await session.flush()

    return str(wf_uuid)


async def delete_state(
    session: AsyncSession,
    workflow_id: str,
    state_name: str,
    board_id: str | None = None,
) -> dict[str, Any]:
    """PH-106: Delete a state from a workflow with safety guards and cascade cleanup.

    Guards (in order):
    1. not_found (404): state_name does not exist in workflow.states.
    2. tickets_exist (400): tickets on the board reference this state name.
    3. last_state (400): workflow must retain at least 1 state.

    On success: state is removed and any transitions referencing it (from or to)
    are silently deleted (cascade). The count of removed transitions is returned
    so the UI can surface transparency info.

    If board_id is provided, ensure_board_owned_workflow is called first (PH-97
    clone-guard) so the operation targets the board's private workflow copy.

    Returns:
        dict with keys: deleted (True), state_name (str), removed_transitions (int).

    Raises:
        NotFound: workflow_id invalid or state_name not in workflow.states.
        StateDeletionBlocked: one of the guards fires (tickets_exist or last_state).
    """
    # PH-97 clone-guard: if board_id provided, ensure board owns private workflow copy
    effective_workflow_id = workflow_id
    if board_id is not None:
        board_uuid = parse_uuid(board_id)
        if board_uuid is not None:
            new_wf_id, _cloned = await ensure_board_owned_workflow(session, board_uuid)
            effective_workflow_id = str(new_wf_id)

    wf_uuid = parse_uuid(effective_workflow_id)
    if wf_uuid is None:
        raise NotFound("workflow")

    # Lock the workflow row for concurrent-delete safety (PH-102 pattern)
    workflow_result = await session.execute(
        select(Workflow).where(Workflow.id == wf_uuid).with_for_update()
    )
    workflow = workflow_result.scalar_one_or_none()
    if workflow is None:
        raise NotFound("workflow")

    # Guard 1 (404): state must exist in workflow.states
    state_names_in_wf = [s.get("name") for s in workflow.states]
    if state_name not in state_names_in_wf:
        raise NotFound(f"state '{state_name}'")

    # Guard 2 (400 — tickets_exist): count tickets in this state on the board.
    # Ticket.state stores the state NAME (String(80)), not an id — this is critical.
    # We need board_id to scope the count; if not provided use the workflow's board context.
    ticket_count = 0
    if board_id is not None:
        board_uuid_for_count = parse_uuid(board_id)
        if board_uuid_for_count is not None:
            count_result = await session.execute(
                select(func.count())
                .select_from(Ticket)
                .where(
                    Ticket.board_id == board_uuid_for_count,
                    Ticket.state == state_name,
                    Ticket.deleted_at.is_(None),
                )
            )
            ticket_count = count_result.scalar_one()
            if ticket_count > 0:
                raise StateDeletionBlocked(
                    reason="tickets_exist",
                    state_name=state_name,
                    ticket_count=ticket_count,
                )

    # Guard 3 (400 — last_state): workflow must retain at least 1 state
    if len(workflow.states) <= 1:
        raise StateDeletionBlocked(reason="last_state", state_name=state_name)

    # Mutation: remove the state and cascade-delete any referencing transitions
    new_states = [s for s in workflow.states if s.get("name") != state_name]
    removed_transitions = [
        t for t in workflow.transitions
        if t.get("from") == state_name or t.get("to") == state_name
    ]
    new_transitions = [t for t in workflow.transitions if t not in removed_transitions]

    # Re-assign lists so SQLAlchemy JSON dirty-tracking detects the change
    workflow.states = new_states
    workflow.transitions = new_transitions
    await session.flush()

    return {
        "deleted": True,
        "state_name": state_name,
        "removed_transitions": len(removed_transitions),
    }