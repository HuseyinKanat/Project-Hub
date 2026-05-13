"""ORM to API response mappers."""

from app.db.models import Actor, Board, Comment, Ticket, TicketHistory, Workflow
from app.schemas import (
    ActorSummary,
    BoardResponse,
    CommentResponse,
    HistoryResponse,
    TicketResponse,
    WorkflowResponse,
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
    return BoardResponse(
        id=board.id,
        key=board.key,
        name=board.name,
        description=board.description,
        project_type=board.project_type,
        roles=board.roles,
        workflow=workflow_response(board.workflow),
        created_at=board.created_at,
        updated_at=board.updated_at,
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
            "impact_analysis": ticket.impact_analysis,
            "test_plan": ticket.test_plan,
            "steps_to_reproduce": ticket.steps_to_reproduce,
            "expected_behavior": ticket.expected_behavior,
            "actual_behavior": ticket.actual_behavior,
            "story_points": ticket.story_points,
            "due_date": ticket.due_date,
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
