"""Workflow configuration services."""

from typing import Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.core import Workflow
from app.services.boards import get_active_workflow


async def get_field_gates(
    session: AsyncSession,
    workflow: Workflow,
    from_state: str,
    to_state: str,
) -> Tuple[list[str], frozenset[str]]:
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
) -> Tuple[list[str], frozenset[str]]:
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