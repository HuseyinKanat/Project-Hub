"""ORM to API response mappers."""

from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    Comment,
    SonarQubeMetric,
    Ticket,
    TicketHistory,
    Workflow,
)
from app.schemas import (
    ActorSummary,
    BoardHealth,
    BoardResponse,
    CommentResponse,
    HistoryResponse,
    MembershipResponse,
    TicketResponse,
    WorkflowResponse,
)
from app.services.boards import mask_webhook_secret
from app.services.repositories import repository_summary


def board_health(metric: SonarQubeMetric) -> BoardHealth:
    """Serialize a SonarQubeMetric ORM row to the compact health schema (PH-193).

    Numeric (Decimal) percentages are coerced to float for JSON. Token/secret is
    never read here — only the persisted metric columns are exposed.
    """
    return BoardHealth(
        quality_gate_status=metric.quality_gate_status,
        bugs=metric.bugs,
        vulnerabilities=metric.vulnerabilities,
        code_smells=metric.code_smells,
        coverage=float(metric.coverage) if metric.coverage is not None else None,
        duplicated_lines_density=(
            float(metric.duplicated_lines_density)
            if metric.duplicated_lines_density is not None
            else None
        ),
        ncloc=metric.ncloc,
        fetched_at=metric.fetched_at,
    )


def actor_summary(actor: Actor) -> ActorSummary:
    return ActorSummary(
        id=actor.id,
        kind=actor.kind,
        display_name=actor.display_name,
        agent_id=actor.agent_id,
        agent_role_hint=actor.agent_role_hint,
    )


def workflow_response(workflow: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        states=workflow.states,
        transitions=workflow.transitions,
        is_default=workflow.is_default,
    )


def board_response(board: Board) -> BoardResponse:
    # PH-150/PH-221: include the PRIMARY repository summary.
    # Board is fetched with selectinload(Board.repositories) in get_board/list_boards,
    # so the primary_repository property iterates the loaded collection safely in
    # async context (no lazy-load triggered).
    repo_orm = board.primary_repository
    repo_summary = repository_summary(repo_orm) if repo_orm is not None else None

    # PH-193: include SonarQube health snapshot. Board is fetched with
    # selectinload(Board.sonarqube_metric) in get_board/list_boards, so this
    # relationship access is safe in async context (no lazy-load triggered).
    metric_orm = board.sonarqube_metric
    health = board_health(metric_orm) if metric_orm is not None else None

    return BoardResponse(
        id=board.id,
        key=board.key,
        name=board.name,
        description=board.description,
        project_type=board.project_type,
        roles=mask_webhook_secret(board.roles),
        workflow=workflow_response(board.workflow),
        created_at=board.created_at,
        updated_at=board.updated_at,
        repository=repo_summary,
        health=health,
        # PH-228: expose the per-board HOST filesystem path (read surface only).
        repos_path=board.repos_path,
    )


def ticket_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse.model_validate(
        {
            "id": ticket.id,
            "key": ticket.key,
            "board_id": ticket.board_id,
            "type": ticket.type,
            "title": ticket.title,
            "description": ticket.description,
            "state": ticket.state,
            "agent_phase": ticket.agent_phase,
            "assignee": actor_summary(ticket.assignee) if ticket.assignee else None,
            "reporter": actor_summary(ticket.reporter),
            "priority": ticket.priority,
            "epic_id": ticket.epic_id,
            "labels": ticket.labels,
            "acceptance_criteria": ticket.acceptance_criteria,
            "technical_depth": ticket.technical_depth,
            "impact_analysis": ticket.impact_analysis,
            "test_plan": ticket.test_plan,
            "steps_to_reproduce": ticket.steps_to_reproduce,
            "expected_behavior": ticket.expected_behavior,
            "actual_behavior": ticket.actual_behavior,
            "story_points": ticket.story_points,
            "due_date": ticket.due_date,
            "branch_name": ticket.branch_name,
            "claimed_by": ticket.claimed_by,
            "claimed_at": ticket.claimed_at,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
            "_links": {
                "self": f"/api/tickets/{ticket.key}",
                "claim": f"/api/tickets/{ticket.key}/claim",
                "comments": f"/api/tickets/{ticket.key}/comments",
            },
        },
    )


def comment_response(comment: Comment) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        ticket_id=comment.ticket_id,
        author=actor_summary(comment.author),
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
    )


def history_response(history: TicketHistory) -> HistoryResponse:
    return HistoryResponse(
        id=history.id,
        event_type=history.event_type,
        field=history.field,
        old_value=history.old_value,
        new_value=history.new_value,
        metadata=history.event_metadata,
        actor=actor_summary(history.actor),
        created_at=history.created_at,
    )


def membership_response(membership: BoardMembership) -> MembershipResponse:
    """Serialize a BoardMembership ORM instance to MembershipResponse.

    Requires ``membership.actor`` to be eagerly loaded.
    """
    return MembershipResponse(
        id=membership.id,
        actor=actor_summary(membership.actor),
        role=membership.role,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )
