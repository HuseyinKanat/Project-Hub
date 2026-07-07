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
    # in_review -> in_test path (when Reviewer step is skipped for audit/doc tickets)
    require_permission(actor, board, "state.transition:to_in_test", resource=ticket)
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


def test_pm_can_drive_worktree_preflight_branch_naming() -> None:
    """Jarwis wave-8 A1: the Coordinator's pm channel owns the dedicated-worktree
    pre-flight — it calls create_branch_for_ticket BEFORE any implementer is
    invoked (Jarwis contracts/git.md §3b.1), so the pm role must be able to write
    branch_name on a ticket it neither claims nor is assigned to. pm's bare
    "ticket.update_field" grant (NOT :if_assignee-scoped) is what makes this
    pass; narrowing it to a scoped or if_assignee grant would silently break
    every Coordinator pre-flight. Live-verified against BENCH on 2026-07-06."""
    board_id = uuid4()
    actor_id = uuid4()
    board = Board(
        id=board_id, key="TST", name="Test", workflow_id=uuid4(), roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=actor_id, kind="agent", display_name="pm", token_hash="hash")
    actor.memberships = [BoardMembership(board_id=board_id, actor_id=actor_id, role="pm")]
    ticket = Ticket(
        id=uuid4(), key="TST-1", board_id=board_id, type="task", title="Task",
        description="", state="to_do",
        reporter_id=uuid4(),
        assignee_id=uuid4(),   # someone ELSE — pm is neither assignee...
        claimed_by=None,       # ...nor claim owner at pre-flight time
        priority="medium", labels=[],
    )

    require_permission(actor, board, "ticket.update_field:branch_name", resource=ticket)


def test_create_branch_tool_permission_stays_pm_reachable() -> None:
    """create_branch_for_ticket is cataloged under bare "ticket.update_field"
    (which pm holds), NOT "git.create_branch" (which pm does NOT hold). The
    catalog field is advisory (dispatch enforces via update_ticket's
    ticket.update_field:branch_name check — covered above), but if someone
    re-points the tool at git.create_branch and later enforces it, the
    Coordinator pm-channel pre-flight breaks. Tripwire for that drift."""
    from app.mcp.server import TOOLS

    tool = next(t for t in TOOLS if t.name == "create_branch_for_ticket")
    assert tool.permission == "ticket.update_field"

    board, actor, _ = _board_with_role("pm")
    require_permission(actor, board, tool.permission)


def test_hotfix_exempt_from_field_gates() -> None:
    """PH-291: the Jarwis hotfix flow fills technical_depth/AC/impact_analysis
    POST-HOC (flows/hotfix.md — speed first, PM/Architect enrich after done), so
    every field gate must exempt hotfix like it exempts epic; otherwise each
    hotfix hits field_gate_not_met churn at in_review/in_test/done."""
    from app.services.defaults import DEFAULT_TRANSITIONS

    gated = [t for t in DEFAULT_TRANSITIONS if "field_gates" in t]
    assert gated, "no field-gated transitions left — update this test"
    for t in gated:
        exempt = t["field_gates"]["exempt_ticket_types"]
        assert "epic" in exempt and "hotfix" in exempt, (t["from"], t["to"], exempt)


def test_ticket_type_vocabulary_covers_jarwis_flows() -> None:
    """PH-290 (R12): the Jarwis ruleset opens tickets with chore/refactor/hotfix
    types (contracts/ticket-fields.md; exit-protocol §2 hotfix create) — before
    PH-290 these 500'd at TicketCreate validation. Guard the full vocabulary so
    a Literal cleanup can't silently break the Jarwis hotfix/refactor flows."""
    from typing import get_args

    from app.schemas import TicketType

    assert {"feature", "bug", "task", "epic", "chore", "refactor", "hotfix"} <= set(
        get_args(TicketType)
    )


def test_claim_owner_counts_as_assignee_for_if_assignee_permission() -> None:
    """Pilot pattern: agent claim_ticket çağırır ama assign_ticket'i atlar.
    Ticket.assignee_id null kalır, ama ticket.claimed_by actor.id'ye set.
    `state.transition:if_assignee` permission'ı claim sahibine de pas vermeli —
    aksi takdirde agent transition_state çağrısında permission_denied alır
    (FN-2 ve diğer pilot vakalarında görüldüğü gibi)."""
    board_id = uuid4()
    actor_id = uuid4()
    board = Board(
        id=board_id, key="TST", name="Test", workflow_id=uuid4(), roles=DEFAULT_WEB_ROLES,
    )
    actor = Actor(id=actor_id, kind="agent", display_name="claimer", token_hash="hash")
    actor.memberships = [BoardMembership(board_id=board_id, actor_id=actor_id, role="backend_dev")]
    ticket = Ticket(
        id=uuid4(), key="TST-1", board_id=board_id, type="task", title="Task",
        description="", state="to_do",
        reporter_id=uuid4(),
        assignee_id=None,        # ← intentionally null
        claimed_by=actor_id,     # ← claim sahibi
        priority="medium", labels=[],
    )

    # backend_dev'in state.transition:if_assignee permission'ı var.
    # Claim sahibi olduğu için pas vermeli — assignee_id null olsa bile.
    require_permission(actor, board, "state.transition:to_in_progress", resource=ticket)
    require_permission(actor, board, "ticket.update_field:branch_name", resource=ticket)


def test_unity_mode_roles_mirror_implementer_capabilities() -> None:
    """Both Unity-mode roles (unity_dev for C# logic, unity_scene_manager for
    scenes/prefabs) carry implementer permissions equivalent to backend_dev /
    frontend_dev: claim, branch, assignee-scoped field/state updates, assign
    for handoff. They are activated by Jarwis modes/unity.md and replace
    backend_dev + frontend_dev on Unity boards."""
    for role in ("unity_dev", "unity_scene_manager"):
        board, actor, ticket = _board_with_role(role)

        require_permission(actor, board, "ticket.claim", resource=ticket)
        require_permission(actor, board, "git.create_branch", resource=ticket)
        require_permission(actor, board, "ticket.assign", resource=ticket)
        require_permission(actor, board, "comment.add", resource=ticket)
        # Since ticket.assignee_id == actor.id in _board_with_role:
        require_permission(actor, board, "ticket.update_field:branch_name", resource=ticket)
        require_permission(actor, board, "state.transition:to_in_progress", resource=ticket)

        # Not a creator role
        with pytest.raises(PermissionDenied):
            require_permission(actor, board, "ticket.create")
        with pytest.raises(PermissionDenied):
            require_permission(actor, board, "ticket.delete", resource=ticket)
