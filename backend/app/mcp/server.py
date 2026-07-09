"""HTTP transport for the project-hub tool catalog.

Two surfaces, same dispatch:
  * Legacy REST: GET /mcp/tools + POST /mcp/call/{tool_name}
    (compact, originally shipped for ad-hoc curl/agent use)
  * MCP JSON-RPC 2.0: POST /mcp
    (per modelcontextprotocol.io spec — what Claude Code and other MCP
    clients speak natively. Initialize → tools/list → tools/call.)

Both routes delegate to ``_dispatch_tool`` so behavior, permissions, and
history writes are identical regardless of caller.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import (
    AlreadyClaimed,
    FieldGateNotMet,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    ProjectHubError,
)
from app.db.models import Actor
from app.db.session import get_db_session
from app.events.bus import EventBus, EventEnvelope
from app.schemas import (
    AgentPhaseUpdate,
    AssignTicket,
    CommentCreate,
    DeleteTicket,
    EnsureBoardWorkflowInput,
    EnsureBoardWorkflowResponse,
    FieldGatesUpdate,
    TicketCreate,
    TicketUpdate,
    TransitionCreate,
    WorkflowActivation,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.services.boards import get_board, list_boards
from app.services.workflows import (
    activate_workflow,
    add_transition,
    create_workflow,
    deactivate_workflow,
    delete_state,
    delete_transition,
    delete_workflow,
    ensure_board_owned_workflow,
    get_workflow,
    list_workflows,
    set_field_gates,
    update_workflow,
)
from app.services.serializers import (
    board_response,
    comment_response,
    history_response,
    ticket_response,
    workflow_response,
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
    verbose: bool = Field(
        default=False,
        description=(
            "If true, write tools (claim_ticket/release_ticket) return the full ticket payload "
            "instead of the default minimal response. Ignored by read-only tools."
        ),
    )


class GetStateInput(BaseModel):
    id: str


# Allow-list of ticket_response keys callers can include via get_ticket_slice.
# Restricting prevents accidentally cloning the whole payload and acts as a
# documented contract of what's safe to project.
_SLICE_ALLOWED_FIELDS = frozenset(
    {
        "type",
        "title",
        "description",
        "state",
        "agent_phase",
        "assignee",
        "reporter",
        "priority",
        "epic_id",
        "labels",
        "acceptance_criteria",
        "technical_depth",
        "impact_analysis",
        "test_plan",
        "steps_to_reproduce",
        "expected_behavior",
        "actual_behavior",
        "story_points",
        "due_date",
        "branch_name",
        "files_touched_globs",
        "blocked_by",
        "claimed_by",
        "claimed_at",
        "created_at",
        "updated_at",
    }
)


class GetTicketSliceInput(BaseModel):
    id: str
    include: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of field names to include in the response. "
            "Allowed: type, title, description, state, agent_phase, assignee, "
            "reporter, priority, epic_id, labels, acceptance_criteria, "
            "technical_depth, impact_analysis, test_plan, steps_to_reproduce, "
            "expected_behavior, actual_behavior, story_points, due_date, "
            "branch_name, claimed_by, claimed_at, created_at, updated_at. "
            "Unknown names are ignored. Empty list → only the always-included "
            "skeleton {id, key, state}. Use this instead of get_ticket when only "
            "a subset of fields is needed (sub-agent role-specific reads)."
        ),
    )


class UpdateTicketInput(BaseModel):
    id: str
    fields: TicketUpdate
    verbose: bool = Field(
        default=False,
        description="If true, return the full ticket payload instead of the minimal {ok, updated_fields, state} response.",
    )


class AssignTicketInput(BaseModel):
    id: str
    assignee_id: str | None = None
    verbose: bool = Field(
        default=False,
        description="If true, return the full ticket payload instead of the minimal {ok, id, assignee_id} response.",
    )


class AddCommentInput(BaseModel):
    id: str
    body: str


class LinkPRInput(BaseModel):
    id: str
    pr_url: str
    pr_number: int | None = None
    pr_title: str = ""


class TransitionStateInput(BaseModel):
    id: str
    to_state: str
    comment: str | None = None
    verbose: bool = Field(
        default=False,
        description="If true, return the full ticket payload instead of the minimal {ok, from_state, to_state} response.",
    )


class DeleteTicketInput(BaseModel):
    id: str
    reason: str


class AgentPhaseInput(BaseModel):
    id: str
    phase: Literal["planning", "analyzing", "coding", "testing", "reviewing", "idle"]
    message: str = ""
    verbose: bool = Field(
        default=False,
        description="If true, return the full ticket payload instead of the minimal {ok, ts, phase} heartbeat response.",
    )


class SubscribeEventsInput(BaseModel):
    ticket_id: str | None = Field(default=None, description="Filter events for specific ticket")
    board_id: str | None = Field(default=None, description="Filter events for specific board")
    since_event_id: str | None = Field(
        default=None, description="Replay events from this ID onwards"
    )


# Workflow management input models
class CreateWorkflowInput(BaseModel):
    workflow: WorkflowCreate
    board_id: str | None = Field(
        default=None,
        description="Board UUID or key. When provided, a BoardWorkflow junction row is inserted so the new workflow appears in list_workflows(board_id).",
    )


class UpdateWorkflowInput(BaseModel):
    workflow_id: str
    fields: WorkflowUpdate
    board_id: str | None = None  # PH-97: optional board context for clone-guard


class ListWorkflowsInput(BaseModel):
    board_id: str | None = None


class DeleteTransitionInput(BaseModel):
    workflow_id: str
    from_state: str
    to_state: str


class DeleteWorkflowInput(BaseModel):
    workflow_id: str
    board_id: str | None = Field(
        default=None,
        description="Board UUID or key. Required for active/last-workflow guards.",
    )


class DeleteStateInput(BaseModel):
    workflow_id: str
    state_name: str
    board_id: str | None = Field(
        default=None,
        description="Board UUID or key. Required for clone-guard + tickets_exist count.",
    )


# Permission identifiers reused across multiple ToolDescription entries.
_PERM_TICKET_UPDATE_FIELD = "ticket.update_field"
_PERM_WORKFLOW_UPDATE = "workflow.update"

TOOLS: list[ToolDescription] = [
    ToolDescription(name="list_boards", description="List boards visible to the actor."),
    ToolDescription(name="get_board", description="Get board details, roles, and workflow."),
    ToolDescription(name="query_tickets", description="Query tickets with a compact projection."),
    ToolDescription(name="get_ticket", description="Get one ticket by UUID or key."),
    ToolDescription(
        name="get_state",
        description=(
            "Compact state probe for Coordinator self-verify. Returns "
            "{id, state, assignee_id, claim_owner, branch_name, last_phase, "
            "last_heartbeat_at, updated_at} (~200 chars) — no description, "
            "AC, technical_depth or comments. Use for routing/transition gates "
            "where only state + lock + assignee matter."
        ),
    ),
    ToolDescription(
        name="get_ticket_slice",
        description=(
            "Project a ticket onto a caller-chosen field subset. Always "
            "returns {id, key, state}; add fields via include=[...] (see "
            "input schema). Use when only a few ticket fields are needed — "
            "5-10x smaller than get_ticket. Sub-agents should prefer this "
            "with their role-specific minimum set."
        ),
    ),
    ToolDescription(
        name="create_ticket",
        description="Create a backlog ticket.",
        permission="ticket.create",
    ),
    ToolDescription(
        name="update_ticket",
        description=(
            "Update ticket fields. Default response is minimal: "
            "{ok, id, state, updated_fields}. Pass verbose=true for the full ticket payload."
        ),
        permission=_PERM_TICKET_UPDATE_FIELD,
    ),
    ToolDescription(
        name="assign_ticket",
        description=(
            "Assign or unassign a ticket. Default response is minimal: "
            "{ok, id, assignee_id}. Pass verbose=true for the full ticket payload."
        ),
        permission="ticket.assign",
    ),
    ToolDescription(
        name="transition_state",
        description=(
            "Move a ticket through workflow state. Default response is minimal: "
            "{ok, id, from_state, to_state}. Pass verbose=true for the full ticket payload."
        ),
    ),
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
        description=(
            "Claim a ticket lock. Default response is minimal: "
            "{ok, id, claimed_by, claimed_at, branch_name, state}. Pass verbose=true for the full ticket payload."
        ),
        permission="ticket.claim",
    ),
    ToolDescription(
        name="release_ticket",
        description=(
            "Release a ticket lock. Default response is minimal: "
            "{ok, id, state}. Pass verbose=true for the full ticket payload."
        ),
    ),
    ToolDescription(
        name="update_agent_phase",
        description=(
            "Update live agent phase (heartbeat). Default response is minimal: "
            "{ok, id, phase, ts}. Pass verbose=true for the full ticket payload."
        ),
        permission="ticket.claim",
    ),
    ToolDescription(name="query_history", description="Read the ticket activity timeline."),
    ToolDescription(
        name="subscribe_events",
        description="Stream real-time ticket events. Long-running streaming tool.",
    ),
    ToolDescription(
        name="create_branch_for_ticket",
        description="Compute and record the expected branch name for a ticket. Returns branch_name string.",
        permission=_PERM_TICKET_UPDATE_FIELD,
    ),
    ToolDescription(
        name="link_pr",
        description="Manually link a pull request URL to a ticket. Writes git_pr_linked history event.",
        permission=_PERM_TICKET_UPDATE_FIELD,
    ),
    # Workflow management tools
    ToolDescription(
        name="create_workflow",
        description="Create a new workflow with states and transitions.",
        permission="workflow.create",
    ),
    ToolDescription(
        name="update_workflow",
        description="Update an existing workflow definition.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="list_workflows",
        description="List all workflows, optionally filtered by board.",
        permission="workflow.read",
    ),
    ToolDescription(
        name="add_transition",
        description="Add a new state transition to a workflow.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="delete_transition",
        description="Remove a state transition from a workflow.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="set_field_gates",
        description="Update field requirements for a specific transition.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="activate_workflow",
        description="Activate a workflow for a board (deactivates current one).",
        permission="workflow.activate",
    ),
    ToolDescription(
        name="deactivate_workflow",
        description="Deactivate any active workflow for a board.",
        permission="workflow.activate",
    ),
    ToolDescription(
        name="ensure_board_workflow",
        description="PH-97: Ensure a board has its own private workflow copy. Clones the shared default if needed. Idempotent. Returns {workflow, cloned}.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="delete_workflow",
        description="Delete a workflow. Guards: cannot delete if is_default=true, active, last remaining for board, or board legacy FK still points at it.",
        permission=_PERM_WORKFLOW_UPDATE,
    ),
    ToolDescription(
        name="delete_state",
        description=(
            "Delete a state from a workflow. "
            "Guards: tickets_exist (state has tickets), last_state (cannot remove last state). "
            "Cascades: transitions referencing the deleted state are silently removed."
        ),
        permission=_PERM_WORKFLOW_UPDATE,
    ),
]


@router.get("/tools", response_model=list[ToolDescription])
async def list_tools(
    _actor: Annotated[Actor, Depends(current_actor)],
) -> list[ToolDescription]:
    return TOOLS


async def _dispatch_tool(
    tool_name: str,
    payload: dict[str, Any],
    actor: Actor,
    session: AsyncSession,
) -> Any:
    """Shared dispatch for both REST (`call_tool`) and JSON-RPC (`mcp_jsonrpc`).

    Returns the raw JSON-serializable result; callers wrap as needed.
    """
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
    elif tool_name == "get_state":
        state_input = GetStateInput.model_validate(payload)
        ticket = await get_ticket(session, state_input.id)
        phase_blob = ticket.agent_phase if isinstance(ticket.agent_phase, dict) else {}
        result = {
            "id": ticket.key,
            "state": ticket.state,
            "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else None,
            "claim_owner": str(ticket.claimed_by) if ticket.claimed_by else None,
            "branch_name": ticket.branch_name,
            "last_phase": phase_blob.get("phase"),
            "last_heartbeat_at": phase_blob.get("last_heartbeat_at"),
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        }
    elif tool_name == "get_ticket_slice":
        slice_input = GetTicketSliceInput.model_validate(payload)
        ticket = await get_ticket(session, slice_input.id)
        full = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        # Skeleton always present; project requested fields onto it.
        result = {"id": full.get("id"), "key": full.get("key"), "state": full.get("state")}
        for field in slice_input.include:
            if field in _SLICE_ALLOWED_FIELDS and field in full:
                result[field] = full[field]
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
        if update_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            result = {
                "ok": True,
                "id": ticket.key,
                "state": ticket.state,
                "updated_fields": sorted(update_input.fields.model_fields_set),
            }
    elif tool_name == "assign_ticket":
        assign_input = AssignTicketInput.model_validate(payload)
        ticket = await assign_ticket(
            session,
            actor=actor,
            ticket_id=assign_input.id,
            payload=AssignTicket(assignee_id=assign_input.assignee_id),
        )
        if assign_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            result = {
                "ok": True,
                "id": ticket.key,
                "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else None,
            }
    elif tool_name == "transition_state":
        transition_input = TransitionStateInput.model_validate(payload)
        pre_state = (await get_ticket(session, transition_input.id)).state
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
        if transition_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            result = {
                "ok": True,
                "id": ticket.key,
                "from_state": pre_state,
                "to_state": ticket.state,
            }
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
        if id_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            result = {
                "ok": True,
                "id": ticket.key,
                "claimed_by": str(ticket.claimed_by) if ticket.claimed_by else None,
                "claimed_at": (
                    ticket.claimed_at.isoformat() if ticket.claimed_at else None
                ),
                "branch_name": ticket.branch_name,
                "state": ticket.state,
            }
    elif tool_name == "release_ticket":
        id_input = IdInput.model_validate(payload)
        ticket = await release_ticket(session, actor=actor, ticket_id=id_input.id)
        if id_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            result = {"ok": True, "id": ticket.key, "state": ticket.state}
    elif tool_name == "update_agent_phase":
        phase_input = AgentPhaseInput.model_validate(payload)
        ticket = await update_agent_phase(
            session,
            actor=actor,
            ticket_id=phase_input.id,
            payload=AgentPhaseUpdate(phase=phase_input.phase, message=phase_input.message),
        )
        if phase_input.verbose:
            result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
        else:
            phase_blob = ticket.agent_phase or {}
            result = {
                "ok": True,
                "id": ticket.key,
                "phase": phase_blob.get("phase") if isinstance(phase_blob, dict) else None,
                "ts": (
                    phase_blob.get("last_heartbeat_at")
                    if isinstance(phase_blob, dict)
                    else None
                )
                or (ticket.updated_at.isoformat() if ticket.updated_at else None),
            }
    elif tool_name == "query_history":
        id_input = IdInput.model_validate(payload)
        history = await list_ticket_history(session, id_input.id)
        result = [history_response(item).model_dump(mode="json") for item in history]
    elif tool_name == "subscribe_events":
        result = {
            "error": "subscribe_events is a streaming tool. "
            "Use GET /mcp/stream/events instead."
        }
    elif tool_name == "create_branch_for_ticket":
        from app.git.parser import expected_branch_name
        id_input = IdInput.model_validate(payload)
        ticket = await get_ticket(session, id_input.id)
        branch = expected_branch_name(ticket.key, ticket.title)
        if ticket.branch_name != branch:
            await update_ticket(
                session,
                actor=actor,
                ticket_id=ticket.key,
                payload=TicketUpdate(branch_name=branch),
            )
        result = {"branch_name": branch, "ticket": ticket.key}
    elif tool_name == "link_pr":
        from app.services.history import write_history
        link_input = LinkPRInput.model_validate(payload)
        ticket = await get_ticket(session, link_input.id)
        from app.events import publish_ticket_event
        history = await write_history(
            session,
            ticket_id=ticket.id,
            actor_id=actor.id,
            event_type="git_pr_linked",
            metadata={
                "pr_url": link_input.pr_url,
                "pr_number": link_input.pr_number,
                "pr_title": link_input.pr_title,
                "manual": True,
            },
        )
        await session.commit()
        ticket = await get_ticket(session, ticket.key)
        await publish_ticket_event(history, ticket, actor)
        result = ticket_response(ticket).model_dump(mode="json", by_alias=True)
    # Workflow management tools
    elif tool_name == "create_workflow":
        create_input = CreateWorkflowInput.model_validate(payload)
        workflow = await create_workflow(session, create_input.workflow, create_input.board_id)
        await session.commit()
        result = workflow_response(workflow).model_dump(mode="json", by_alias=True)
    elif tool_name == "update_workflow":
        update_input = UpdateWorkflowInput.model_validate(payload)
        board_id_param = update_input.board_id or update_input.fields.board_id
        workflow = await update_workflow(
            session,
            update_input.workflow_id,
            update_input.fields,
            board_id=board_id_param,
        )
        await session.commit()
        result = workflow_response(workflow).model_dump(mode="json", by_alias=True)
    elif tool_name == "list_workflows":
        list_input = ListWorkflowsInput.model_validate(payload)
        workflows = await list_workflows(session, list_input.board_id)
        result = [workflow_response(w).model_dump(mode="json", by_alias=True) for w in workflows]
    elif tool_name == "add_transition":
        transition_input = TransitionCreate.model_validate(payload)
        workflow = await add_transition(
            session,
            transition_input.workflow_id,
            transition_input.from_state,
            transition_input.to_state,
            transition_input.allowed_roles,
            transition_input.field_gates,
            board_id=transition_input.board_id,
        )
        await session.commit()
        result = workflow_response(workflow).model_dump(mode="json", by_alias=True)
    elif tool_name == "delete_transition":
        delete_input = DeleteTransitionInput.model_validate(payload)
        workflow = await delete_transition(
            session,
            delete_input.workflow_id,
            delete_input.from_state,
            delete_input.to_state,
            board_id=payload.get("board_id"),
        )
        await session.commit()
        result = workflow_response(workflow).model_dump(mode="json", by_alias=True)
    elif tool_name == "set_field_gates":
        gates_input = FieldGatesUpdate.model_validate(payload)
        workflow = await set_field_gates(
            session,
            gates_input.workflow_id,
            gates_input.from_state,
            gates_input.to_state,
            gates_input.field_gates,
            board_id=gates_input.board_id,
        )
        await session.commit()
        result = workflow_response(workflow).model_dump(mode="json", by_alias=True)
    elif tool_name == "activate_workflow":
        activation_input = WorkflowActivation.model_validate(payload)
        await activate_workflow(session, activation_input.board_id, activation_input.workflow_id)
        await session.commit()
        result = {"status": "activated"}
    elif tool_name == "deactivate_workflow":
        deactivation_input = GetBoardInput.model_validate(payload)
        await deactivate_workflow(session, deactivation_input.board_id)
        await session.commit()
        result = {"status": "deactivated"}
    elif tool_name == "ensure_board_workflow":
        # PH-97: Clone shared/default workflow to board-private copy if needed
        from app.services.boards import parse_uuid as _parse_uuid
        ensure_input = EnsureBoardWorkflowInput.model_validate(payload)
        board_uuid = _parse_uuid(ensure_input.board_id)
        if board_uuid is None:
            raise NotFound("board")
        new_wf_id, cloned = await ensure_board_owned_workflow(session, board_uuid)
        await session.commit()
        wf = await get_workflow(session, str(new_wf_id))
        result = EnsureBoardWorkflowResponse(
            workflow=WorkflowResponse(
                id=wf.id,
                name=wf.name,
                states=wf.states,
                transitions=wf.transitions,
                is_default=wf.is_default,
            ),
            cloned=cloned,
        ).model_dump(mode="json")
    elif tool_name == "delete_workflow":
        # PH-102: Delete workflow with 4 safety guards (min-1, active, default, legacy FK)
        delete_wf_input = DeleteWorkflowInput.model_validate(payload)
        deleted_id = await delete_workflow(
            session,
            delete_wf_input.workflow_id,
            board_id=delete_wf_input.board_id,
        )
        await session.commit()
        result = {"deleted": True, "id": deleted_id}
    elif tool_name == "delete_state":
        # PH-106: Delete a state with 3 guards (not_found, tickets_exist, last_state)
        # and cascade transition cleanup.
        delete_state_input = DeleteStateInput.model_validate(payload)
        result = await delete_state(
            session,
            delete_state_input.workflow_id,
            delete_state_input.state_name,
            board_id=delete_state_input.board_id,
        )
        await session.commit()
    else:
        raise NotFound("tool")

    return result


# Map each tool to its Pydantic input model (or None for no-input tools).
# Used by tools/list to advertise JSON Schemas to MCP clients.
_TOOL_INPUT_MODELS: dict[str, type[BaseModel] | None] = {
    "list_boards": None,
    "get_board": GetBoardInput,
    "query_tickets": QueryTicketsInput,
    "get_ticket": IdInput,
    "get_state": GetStateInput,
    "get_ticket_slice": GetTicketSliceInput,
    "create_ticket": TicketCreate,
    "update_ticket": UpdateTicketInput,
    "assign_ticket": AssignTicketInput,
    "transition_state": TransitionStateInput,
    "add_comment": AddCommentInput,
    "delete_ticket": DeleteTicketInput,
    "claim_ticket": IdInput,
    "release_ticket": IdInput,
    "update_agent_phase": AgentPhaseInput,
    "query_history": IdInput,
    "subscribe_events": SubscribeEventsInput,
    "create_branch_for_ticket": IdInput,
    "link_pr": LinkPRInput,
    "ensure_board_workflow": EnsureBoardWorkflowInput,
    "delete_workflow": DeleteWorkflowInput,
    "delete_state": DeleteStateInput,
}

_EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _build_mcp_tool_list() -> list[dict[str, Any]]:
    """Build the MCP `tools/list` payload: name + description + inputSchema."""
    by_name = {t.name: t for t in TOOLS}
    out: list[dict[str, Any]] = []
    for name, model in _TOOL_INPUT_MODELS.items():
        meta = by_name.get(name)
        if meta is None:
            continue  # keep dispatcher and TOOLS catalog in sync
        schema = (
            model.model_json_schema() if model is not None else dict(_EMPTY_INPUT_SCHEMA)
        )
        out.append(
            {
                "name": name,
                "description": meta.description,
                "inputSchema": schema,
            }
        )
    return out


@router.post("/call/{tool_name}", response_model=ToolCallResponse)
async def call_tool(
    tool_name: str,
    payload: dict[str, Any],
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ToolCallResponse:
    """Legacy REST shape — kept for curl/scripts. New clients use POST /mcp."""
    result = await _dispatch_tool(tool_name, payload, actor, session)
    return ToolCallResponse(tool=tool_name, result=result)


def _domain_error_detail(exc: ProjectHubError) -> dict[str, Any]:
    """Flatten a ProjectHubError into an MCP tool-error detail dict.

    Agents need the structured detail to recover — e.g. invalid_transition tells
    them which intermediate state to use, field_gate_not_met what field is missing.
    """
    detail: dict[str, Any] = {
        "error": getattr(exc, "code", "domain_error"),
        "message": getattr(exc, "message", str(exc)),
    }
    for attr in (
        "required", "have", "from_state", "to_state", "allowed",
        "claimed_by", "since", "transition", "missing_fields",
        "reason", "workflow_id", "state_name", "ticket_count",
    ):
        if hasattr(exc, attr):
            val = getattr(exc, attr)
            if val is not None:
                detail[attr] = list(val) if isinstance(val, (set, tuple)) else val
    return detail


async def _jsonrpc_tools_call(
    params: dict[str, Any], actor: Actor, session: AsyncSession
) -> dict[str, Any] | tuple[int, str]:
    """Run a tools/call request; return an MCP content envelope or (code, msg) error.

    A tuple result signals a JSON-RPC param error the caller surfaces via ``_err``;
    a dict is the MCP content envelope passed to ``_ok`` (success or domain isError).
    """
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(tool_name, str):
        return (-32602, "Invalid params: 'name' must be a string")
    try:
        raw = await _dispatch_tool(tool_name, arguments, actor, session)
    except ProjectHubError as exc:
        # Surface ALL domain-level errors (PermissionDenied, NotFound,
        # InvalidTransition, AlreadyClaimed, FieldGateNotMet, ...) as MCP
        # tool-level errors (isError=true), NOT JSON-RPC internal errors.
        detail = _domain_error_detail(exc)
        return {
            "content": [{"type": "text", "text": json.dumps(detail, default=str)}],
            "isError": True,
        }
    # Successful call → MCP content envelope (single text part with JSON).
    return {
        "content": [{"type": "text", "text": json.dumps(raw, default=str)}],
        "isError": False,
    }


@router.post("")
async def mcp_jsonrpc(
    request: Request,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """MCP JSON-RPC 2.0 over HTTP (streamable HTTP transport).

    Handles: initialize, notifications/initialized, ping, tools/list, tools/call.
    Spec: https://modelcontextprotocol.io/specification/2024-11-05/basic/transports
    """
    try:
        msg = await request.json()
    except Exception:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    method: str | None = msg.get("method")
    params: dict[str, Any] = msg.get("params") or {}
    req_id = msg.get("id")
    is_notification = req_id is None

    # Notifications: no response body, just 202 Accepted.
    if is_notification:
        return Response(status_code=202)

    def _err(code: int, message: str, data: Any = None) -> JSONResponse:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": err})

    def _ok(result: Any) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

    try:
        if method == "initialize":
            return _ok(
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "project-hub", "version": "1.0.0"},
                }
            )
        if method == "ping":
            return _ok({})
        if method == "tools/list":
            return _ok({"tools": _build_mcp_tool_list()})
        if method == "tools/call":
            call_result = await _jsonrpc_tools_call(params, actor, session)
            if isinstance(call_result, tuple):
                return _err(*call_result)
            return _ok(call_result)
        return _err(-32601, f"Method not found: {method}")
    except Exception as exc:  # noqa: BLE001 — JSON-RPC needs to mask raw internals
        return _err(-32603, "Internal error", {"detail": str(exc)})


# SSE streaming endpoint for subscribe_events
def _history_envelope(
    item: Any, ticket: Any, board_id: str | None
) -> EventEnvelope:
    """Build a replay EventEnvelope from a TicketHistory row."""
    return EventEnvelope(
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


async def _replay_history(
    session: AsyncSession,
    since_event_id: str,
    ticket: Any,
    ticket_id: str | None,
    board_id: str | None,
) -> AsyncIterator[str]:
    """Yield SSE frames replaying TicketHistory rows after ``since_event_id``."""
    from uuid import UUID

    from sqlalchemy import select

    from app.db.models import Ticket, TicketHistory

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
            envelope = _history_envelope(item, ticket, board_id)
            yield f"data: {envelope.to_json()}\n\n"
    except Exception as e:
        yield f"data: {{\"error\": \"replay_failed: {e!s}\"}}\n\n"


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
        async for frame in _replay_history(
            session, since_event_id, ticket, ticket_id, board_id
        ):
            yield frame

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
