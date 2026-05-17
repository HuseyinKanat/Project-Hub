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


def _board_with_role(role: str) -> tuple[Board, Actor, Ticket]:
    board_id = uuid4()
    actor_id = uuid4()
    board = Board(
        id=board_id,
        key="TST",
        name="Test",
        workflow_id=uuid4(),
        roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=actor_id, kind="agent", display_name=role, token_hash="hash")
    actor.memberships = [BoardMembership(board_id=board_id, actor_id=actor_id, role=role)]
    ticket = Ticket(
        id=uuid4(),
        key="TST-1",
        board_id=board_id,
        type="task",
        title="Task",
        description="",
        state="in_review",
        reporter_id=uuid4(),
        assignee_id=actor_id,
        priority="medium",
        labels=[],
    )
    return board, actor, ticket


def test_reviewer_can_amend_technical_depth_and_transition() -> None:
    board, actor, ticket = _board_with_role("reviewer")

    require_permission(actor, board, "ticket.update_field:technical_depth", resource=ticket)
    require_permission(actor, board, "state.transition:to_in_test", resource=ticket)
    require_permission(actor, board, "state.transition:to_in_progress", resource=ticket)
    require_permission(actor, board, "ticket.assign", resource=ticket)
    require_permission(actor, board, "comment.add", resource=ticket)


def test_reviewer_cannot_touch_unrelated_field_or_claim() -> None:
    board, actor, ticket = _board_with_role("reviewer")

    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.update_field:title", resource=ticket)
    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.claim", resource=ticket)
    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.create")


def test_qa_can_drive_bug_reproduce_and_verify_transitions() -> None:
    board, actor, ticket = _board_with_role("qa")

    # bug reproduce: claim + create branch + assign Dev
    require_permission(actor, board, "ticket.claim", resource=ticket)
    require_permission(actor, board, "git.create_branch", resource=ticket)
    require_permission(actor, board, "ticket.assign", resource=ticket)
    # verify outcomes
    require_permission(actor, board, "state.transition:to_done", resource=ticket)
    require_permission(actor, board, "state.transition:to_in_progress", resource=ticket)
    require_permission(actor, board, "ticket.update_field:test_plan", resource=ticket)
    require_permission(actor, board, "ticket.update_field:impact_analysis", resource=ticket)


def test_qa_cannot_update_arbitrary_field() -> None:
    board, actor, ticket = _board_with_role("qa")

    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.update_field:title", resource=ticket)


def test_architect_can_update_fields_and_assign_but_not_claim() -> None:
    board, actor, ticket = _board_with_role("architect")

    require_permission(actor, board, "ticket.update_field:technical_depth", resource=ticket)
    require_permission(actor, board, "ticket.update_field:acceptance_criteria", resource=ticket)
    require_permission(actor, board, "ticket.assign", resource=ticket)
    require_permission(actor, board, "comment.add", resource=ticket)
    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.claim", resource=ticket)
    with pytest.raises(PermissionDenied):
        require_permission(actor, board, "ticket.delete", resource=ticket)


def test_pm_can_create_and_decompose_epics() -> None:
    board, actor, ticket = _board_with_role("pm")

    require_permission(actor, board, "ticket.create")
    require_permission(actor, board, "ticket.delete", resource=ticket)
    require_permission(actor, board, "epic.manage")
    require_permission(actor, board, "ticket.assign", resource=ticket)
    require_permission(actor, board, "ticket.update_field:description", resource=ticket)
    require_permission(actor, board, "state.transition:to_done", resource=ticket)
