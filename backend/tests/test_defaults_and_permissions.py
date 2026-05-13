"""Low-level tests for plan defaults and permission matching."""

from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDenied
from app.core.permissions import require_permission
from app.db.models import Actor, Board, BoardMembership, Ticket
from app.services.defaults import DEFAULT_STATES, DEFAULT_WEB_ROLES, initial_state


def test_default_workflow_initial_state_is_backlog() -> None:
    assert initial_state(DEFAULT_STATES) == "backlog"


def test_admin_role_allows_any_permission() -> None:
    board_id = uuid4()
    actor_id = uuid4()
    board = Board(
        id=board_id,
        key="TST",
        name="Test",
        workflow_id=uuid4(),
        roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=actor_id, kind="human", display_name="Admin", token_hash="hash")
    actor.memberships = [BoardMembership(board_id=board_id, actor_id=actor_id, role="admin")]

    require_permission(actor, board, "ticket.create")


def test_missing_membership_denies_permission() -> None:
    board = Board(
        id=uuid4(),
        key="TST",
        name="Test",
        workflow_id=uuid4(),
        roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=uuid4(), kind="human", display_name="Guest", token_hash="hash")

    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.create")


def test_assignee_scoped_permission_allows_assigned_ticket_update() -> None:
    board_id = uuid4()
    actor_id = uuid4()
    board = Board(
        id=board_id,
        key="TST",
        name="Test",
        workflow_id=uuid4(),
        roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=actor_id, kind="agent", display_name="Backend", token_hash="hash")
    actor.memberships = [BoardMembership(board_id=board_id, actor_id=actor_id, role="backend_dev")]
    ticket = Ticket(
        id=uuid4(),
        key="TST-1",
        board_id=board_id,
        type="task",
        title="Task",
        description="",
        state="to_do",
        reporter_id=actor_id,
        assignee_id=actor_id,
        priority="medium",
        labels=[],
    )

    require_permission(actor, board, "ticket.update_field:title", resource=ticket)
