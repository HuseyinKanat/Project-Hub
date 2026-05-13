"""Minimal HTTP MCP-style tool router.

This keeps the first implementation dependency-light while preserving the
plan's tool names and shared service-layer behavior.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import NotFound
from app.db.models import Actor
from app.db.session import get_db_session
from app.events.bus import EventBus, EventEnvelope
from app.schemas import (
    AgentPhaseUpdate,
    AssignTicket,
    CommentCreate,
    DeleteTicket,
    TicketCreate,
    TicketUpdate,
)
from app.services.boards import get_board, list_boards
from app.services.serializers import (
    board_response,
    comment_response,
    history_response,
    ticket_response,
)
from app.services.tickets import (
    add_comment,
    assign_ticket,
    claim_ticket,
    create_ticket,
    delete_ticket,
    get_ticket,
    list_ticket_history,
    query_tickets,
    release_ticket,
    transition_ticket_state,
    update_agent_phase,
    update_ticket,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class ToolDescription(BaseModel):
    name: str
    description: str
    permission: str | None = None


class ToolCallResponse(BaseModel):
    tool: str
    result: Any


class GetBoardInput(BaseModel):
    board_id: str


class QueryTicketsInput(BaseModel):
    board_id: str | None = None
    state: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class IdInput(BaseModel):
    id: str


class UpdateTicketInput(BaseModel):
    id: str
    fields: TicketUpdate


class AssignTicketInput(BaseModel):
    id: str
    assignee_id: str | None = None


class AddCommentInput(BaseModel):
    id: str
    body: str


class TransitionStateInput(BaseModel):
    id: str
    to_state: str
    comment: str | None = None


class DeleteTicketInput(BaseModel):
    id: str
    reason: str


class AgentPhaseInput(BaseModel):
    id: str
    phase: Literal["planning", "analyzing", "coding", "testing", "reviewing", "idle"]
    message: str = ""


class SubscribeEventsInput(BaseModel):
    ticket_id: str | None = Field(default=None, description="Filter events for specific ticket")
    board_id: str | None = Field(default=None, description="Filter events for specific board")
    since_event_id: str | None = Field(
        default=None, description="Replay events from this ID onwards"
    )


TOOLS: list[ToolDescription] = [
    ToolDescription(name="list_boards", description="List boards visible to the actor."),
    ToolDescription(name="get_board", description="Get board details, roles, and workflow."),
    ToolDescription(name="query_tickets", description="Query tickets with a compact projection."),
    ToolDescription(name="get_ticket", description="Get one ticket by UUID or key."),
    ToolDescription(
        name="create_ticket",
        description="Create a backlog ticket.",
        permission="ticket.create",
    ),
    ToolDescription(
        name="update_ticket",
        description="Update ticket fields.",
        permission="ticket.update_field",
    ),
    ToolDescription(
        name="assign_ticket",
        description="Assign or unassign a ticket.",
        permission="ticket.assign",
    ),
    ToolDescription(name="transition_state", description="Move a ticket through workflow state."),
    ToolDescription(
        name="add_comment",
        description="Add a ticket comment.",
        permission="comment.add",
    ),
    ToolDescription(
        name="delete_ticket",
        description="Soft-delete a ticket.",
        permission="ticket.delete",
    ),
    ToolDescription(
        name="claim_ticket",
        description="Claim a ticket lock.",
        permission="ticket.claim",
    ),
    ToolDescription(name="release_ticket", description="Release a ticket lock."),
    ToolDescription(
        name="update_agent_phase",
        description="Update live agent phase.",
        permission="ticket.claim",
    ),
    ToolDescription(name="query_history", description="Read the ticket activity timeline."),
    ToolDescription(
        name="subscribe_events",
        description="Stream real-time ticket events. Long-running streaming tool.",
    ),
]


@router.get("/tools", response_model=list[ToolDescription])
async def list_tools(
    _actor: Annotated[Actor, Depends(current_actor)],
) -> list[ToolDescription]:
    return TOOLS


@router.post("/call/{tool_name}", response_model=ToolCallResponse)
async def call_tool(
    tool_name: str,
    payload: dict[str, Any],
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ToolCallResponse:
    result: Any

    if tool_name == "list_boards":
        boards = await list_boards(session)
        result = [board_response(board).model_dump(mode="json") for board in boards]
    elif tool_name == "get_board":
        get_board_input = GetBoardInput.model_validate(payload)
        result = board_response(
            await get_board(session, get_board_input.board_id)
        ).model_dump(mode="json")
    elif tool_name == "query_tickets":
        query_input = QueryTicketsInput.model_validate(payload)
        tickets = await query_tickets(
            session,
            board_id=query_input.board_id,
            state=query_input.state,
            limit=query_input.limit,
        )
        result = [
            ticket_response(ticket).model_dump(mode="json", by_alias=True) for ticket in tickets
        ]
    elif tool_name == "get_ticket":
        id_input = IdInput.model_validate(payload)
        ticket = await get_ticket(session, id_input.id)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "create_ticket":
        create_input = TicketCreate.model_validate(payload)
        ticket = await create_ticket(session, actor=actor, payload=create_input)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "update_ticket":
        update_input = UpdateTicketInput.model_validate(payload)
        ticket = await update_ticket(
            session,
            actor=actor,
            ticket_id=update_input.id,
            payload=update_input.fields,
        )
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "assign_ticket":
        assign_input = AssignTicketInput.model_validate(payload)
        ticket = await assign_ticket(
            session,
            actor=actor,
            ticket_id=assign_input.id,
            payload=AssignTicket(assignee_id=assign_input.assignee_id),
        )
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "transition_state":
        transition_input = TransitionStateInput.model_validate(payload)
        ticket = await transition_ticket_state(
            session,
            actor=actor,
            ticket_id=transition_input.id,
            to_state=transition_input.to_state,
        )
        if transition_input.comment:
            await add_comment(
                session,
                actor=actor,
                ticket_id=ticket.key,
                payload=CommentCreate(body=transition_input.comment),
            )
            ticket = await get_ticket(session, ticket.key)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "add_comment":
        comment_input = AddCommentInput.model_validate(payload)
        comment = await add_comment(
            session,
            actor=actor,
            ticket_id=comment_input.id,
            payload=CommentCreate(body=comment_input.body),
        )
        result = comment_response(comment).model_dump(mode="json")
    elif tool_name == "delete_ticket":
        delete_input = DeleteTicketInput.model_validate(payload)
        await delete_ticket(
            session,
            actor=actor,
            ticket_id=delete_input.id,
            payload=DeleteTicket(reason=delete_input.reason),
        )
        result = {"deleted": True, "id": delete_input.id}
    elif tool_name == "claim_ticket":
        id_input = IdInput.model_validate(payload)
        ticket = await claim_ticket(session, actor=actor, ticket_id=id_input.id)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "release_ticket":
        id_input = IdInput.model_validate(payload)
        ticket = await release_ticket(session, actor=actor, ticket_id=id_input.id)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "update_agent_phase":
        phase_input = AgentPhaseInput.model_validate(payload)
        ticket = await update_agent_phase(
            session,
            actor=actor,
            ticket_id=phase_input.id,
            payload=AgentPhaseUpdate(phase=phase_input.phase, message=phase_input.message),
        )
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    elif tool_name == "query_history":
        id_input = IdInput.model_validate(payload)
        history = await list_ticket_history(session, id_input.id)
        result = [history_response(item).model_dump(mode="json") for item in history]
    elif tool_name == "subscribe_events":
        result = {
            "error": "subscribe_events is a streaming tool. "
            "Use GET /mcp/stream/events instead."
        }
    else:
        raise NotFound("tool")

    return ToolCallResponse(tool=tool_name, result=result)


# SSE streaming endpoint for subscribe_events
async def event_stream(
    session: AsyncSession,
    board_id: str | None = None,
    ticket_id: str | None = None,
    since_event_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream events as SSE (Server-Sent Events) format.
    
    1. If since_event_id provided: replay from history first
    2. Subscribe to Redis channels for live events
    """
    from uuid import UUID

    from sqlalchemy import select

    from app.db.models import Ticket, TicketHistory

    channels = []
    if board_id:
        channels.append(f"board:{board_id}")

    ticket = None
    if ticket_id:
        ticket = await get_ticket(session, ticket_id)
        channels.append(f"ticket:{ticket.id}")
        if not board_id:
            channels.append(f"board:{ticket.board_id}")

    if not channels:
        err = '{"error": "board_id or ticket_id required"}\n\n'
        yield f"data: {err}"
        return

    if since_event_id:
        try:
            since_uuid = UUID(since_event_id)
            query = select(TicketHistory).where(TicketHistory.id > since_uuid)
            if ticket_id:
                query = query.where(TicketHistory.ticket_id == ticket.id)
            elif board_id:
                query = query.join(Ticket).where(Ticket.board_id == UUID(board_id))

            query = query.order_by(TicketHistory.id.asc())
            result = await session.execute(query)
            history_items = result.scalars().all()

            for item in history_items:
                envelope = EventEnvelope(
                    event_id=str(item.id),
                    type=item.event_type,
                    board_id=str(ticket.board_id) if ticket else board_id or "",
                    ticket_id=str(item.ticket_id),
                    ticket_key=ticket.key if ticket else "",
                    actor_id=str(item.actor_id) if item.actor_id else None,
                    payload={
                        "field": item.field,
                        "old_value": item.old_value,
                        "new_value": item.new_value,
                        "metadata": item.event_metadata,
                    },
                    occurred_at=item.created_at.isoformat(),
                )
                yield f"data: {envelope.to_json()}\n\n"
        except Exception as e:
            yield f"data: {{\"error\": \"replay_failed: {e!s}\"}}\n\n"

    try:
        channel = channels[0]
        async for envelope in EventBus.subscribe(channel):
            yield f"data: {envelope.to_json()}\n\n"
    except Exception as e:
        yield f"data: {{\"error\": \"subscription_failed: {e!s}\"}}\n\n"


@router.get("/stream/events")
async def subscribe_events_stream(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _actor: Annotated[Actor, Depends(current_actor)],
    board_id: str | None = Query(default=None),
    ticket_id: str | None = Query(default=None),
    since_event_id: str | None = Query(default=None),
) -> StreamingResponse:
    """SSE streaming endpoint for real-time ticket events.
    
    Query params:
        board_id: Filter by board (stream all board events)
        ticket_id: Filter by specific ticket
        since_event_id: Replay events from this ID onwards, then stream live
    
    Returns: text/event-stream with JSON data lines
    """
    if not board_id and not ticket_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="board_id or ticket_id required")
    
    return StreamingResponse(
        event_stream(session, board_id, ticket_id, since_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
