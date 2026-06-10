"""Ticket REST endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import PermissionDenied, RepoNotConfigured
from app.db.models import Actor, BoardMembership, Repository
from app.db.session import get_db_session
from app.schemas import (
    AgentPhaseUpdate,
    AssignTicket,
    CommentCreate,
    CommentResponse,
    DeleteTicket,
    HistoryResponse,
    TicketCommitsResponse,
    TicketCreate,
    TicketListResponse,
    TicketResponse,
    TicketUpdate,
)
from app.services.git_queries import ticket_commits_payload
from app.services.serializers import comment_response, history_response, ticket_response
from app.services.tickets import (
    add_comment,
    assign_ticket,
    claim_ticket,
    create_ticket,
    delete_ticket,
    get_ticket,
    list_comments,
    list_ticket_history,
    query_tickets,
    release_ticket,
    transition_ticket_state,
    update_agent_phase,
    update_ticket,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("", response_model=TicketListResponse)
async def api_query_tickets(
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    board_id: str | None = None,
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> TicketListResponse:
    tickets = await query_tickets(session, board_id=board_id, state=state, limit=limit)
    return TicketListResponse(tickets=[ticket_response(ticket) for ticket in tickets])


@router.post("", response_model=TicketResponse, status_code=201)
async def api_create_ticket(
    payload: TicketCreate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    ticket = await create_ticket(session, actor=actor, payload=payload)
    return ticket_response(ticket)


@router.get("/{ticket_id}", response_model=TicketResponse)
async def api_get_ticket(
    ticket_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    return ticket_response(await get_ticket(session, ticket_id))


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def api_update_ticket(
    ticket_id: str,
    payload: TicketUpdate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    ticket = await update_ticket(session, actor=actor, ticket_id=ticket_id, payload=payload)
    return ticket_response(ticket)


@router.post("/{ticket_id}/assign", response_model=TicketResponse)
async def api_assign_ticket(
    ticket_id: str,
    payload: AssignTicket,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    ticket = await assign_ticket(session, actor=actor, ticket_id=ticket_id, payload=payload)
    return ticket_response(ticket)


@router.post("/{ticket_id}/claim", response_model=TicketResponse)
async def api_claim_ticket(
    ticket_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    return ticket_response(await claim_ticket(session, actor=actor, ticket_id=ticket_id))


@router.post("/{ticket_id}/release", response_model=TicketResponse)
async def api_release_ticket(
    ticket_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    return ticket_response(await release_ticket(session, actor=actor, ticket_id=ticket_id))


@router.post("/{ticket_id}/phase", response_model=TicketResponse)
async def api_update_agent_phase(
    ticket_id: str,
    payload: AgentPhaseUpdate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    ticket = await update_agent_phase(session, actor=actor, ticket_id=ticket_id, payload=payload)
    return ticket_response(ticket)


@router.post("/{ticket_id}/transition/{to_state}", response_model=TicketResponse)
async def api_transition_ticket(
    ticket_id: str,
    to_state: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketResponse:
    ticket = await transition_ticket_state(
        session,
        actor=actor,
        ticket_id=ticket_id,
        to_state=to_state,
    )
    return ticket_response(ticket)


@router.get("/{ticket_id}/comments", response_model=list[CommentResponse])
async def api_list_comments(
    ticket_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[CommentResponse]:
    """List comments on a ticket, oldest → newest."""
    comments = await list_comments(session, ticket_id)
    return [comment_response(c) for c in comments]


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=201)
async def api_add_comment(
    ticket_id: str,
    payload: CommentCreate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommentResponse:
    comment = await add_comment(session, actor=actor, ticket_id=ticket_id, payload=payload)
    return comment_response(comment)


@router.get("/{ticket_id}/history", response_model=list[HistoryResponse])
async def api_list_history(
    ticket_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[HistoryResponse]:
    history = await list_ticket_history(session, ticket_id)
    return [history_response(item) for item in history]


@router.delete("/{ticket_id}", status_code=204)
async def api_delete_ticket(
    ticket_id: str,
    payload: DeleteTicket,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await delete_ticket(session, actor=actor, ticket_id=ticket_id, payload=payload)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# G5 — ticket commits endpoint (PH-154)
# ---------------------------------------------------------------------------


@router.get(
    "/{ticket_key}/commits",
    response_model=TicketCommitsResponse,
    operation_id="api_ticket_commits",
)
async def api_ticket_commits(
    ticket_key: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketCommitsResponse:
    """List commits linked to a ticket (cache-only; no patch text).

    Resolves the ticket by key or UUID, then checks that the calling actor is
    a member of the ticket's board (any role).  Returns 404 for unknown ticket,
    403 for non-members, 409 if no repository is configured for the board.

    ``additions``/``deletions`` are summed from ``git_commit_files``.
    ``files_changed`` is the row count for that commit.  Zero linked commits
    returns an empty list (200, not 404).  Diff text is NOT inlined — UI calls
    ``GET /api/boards/{key}/git/commits/{sha}/diff`` on demand.

    PH-250: the response also carries ``branches`` — the ticket's branches
    across ALL linked repos (joined by ``git_branches.ticket_key == ticket.key``
    string, each tagged with ``repo_id``/``repo_slug``/``repo_name``). The
    legacy ``branch_name`` single-string pointer is retained unchanged.
    """
    ticket = await get_ticket(session, ticket_key)

    # Verify actor membership on the ticket's board
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == ticket.board_id,
                BoardMembership.actor_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise PermissionDenied(required="board.member", have=[])

    # Verify the board has at least one repository row.
    #
    # PH-247: this is a PRESENCE check only — the ticket commits surface
    # aggregates across ALL of a board's repos (ticket_commits_payload joins
    # git_commit_tickets by ticket_id, then tags each entry with its own repo),
    # so we must NOT collapse the result to a single repo. A plain
    # ``scalar_one_or_none()`` raised ``MultipleResultsFound`` → 500 on every
    # multi-repo board (GXA has 3). Use a LIMIT 1 presence probe: 409 only when
    # truly ZERO repos, never 500 with N≥2.
    has_repo = (
        await session.execute(
            select(Repository.id).where(Repository.board_id == ticket.board_id).limit(1)
        )
    ).first()
    if has_repo is None:
        raise RepoNotConfigured()

    return await ticket_commits_payload(session, ticket)
