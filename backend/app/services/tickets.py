"""Ticket service logic."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AlreadyClaimed, InvalidTransition, NotFound
from app.core.permissions import require_permission
from app.db.models import Actor, Board, Comment, Ticket, TicketHistory
from app.schemas import CommentCreate, TicketCreate
from app.services.boards import get_board, parse_uuid
from app.services.defaults import initial_state
from app.services.history import write_history


def _ticket_load_options() -> tuple[object, ...]:
    return (
        selectinload(Ticket.reporter),
        selectinload(Ticket.assignee),
        selectinload(Ticket.board).selectinload(Board.workflow),
    )


async def get_ticket(session: AsyncSession, ticket_id: str) -> Ticket:
    ticket_uuid = parse_uuid(ticket_id)
    statement = select(Ticket).options(*_ticket_load_options()).where(Ticket.deleted_at.is_(None))
    if ticket_uuid is None:
        statement = statement.where(Ticket.key == ticket_id.upper())
    else:
        statement = statement.where(Ticket.id == ticket_uuid)

    ticket = (await session.execute(statement)).scalar_one_or_none()
    if ticket is None:
        raise NotFound("ticket")
    return ticket


async def query_tickets(
    session: AsyncSession,
    *,
    board_id: str | None = None,
    state: str | None = None,
    limit: int = 50,
) -> list[Ticket]:
    statement = (
        select(Ticket)
        .options(*_ticket_load_options())
        .where(Ticket.deleted_at.is_(None))
        .order_by(Ticket.created_at.desc())
        .limit(min(limit, 100))
    )
    if board_id is not None:
        board = await get_board(session, board_id)
        statement = statement.where(Ticket.board_id == board.id)
    if state is not None:
        statement = statement.where(Ticket.state == state)

    result = await session.execute(statement)
    return list(result.scalars())


async def create_ticket(session: AsyncSession, *, actor: Actor, payload: TicketCreate) -> Ticket:
    board = await get_board(session, payload.board_id)
    require_permission(actor, board, "ticket.create")

    locked_board = (
        await session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow))
            .with_for_update()
        )
    ).scalar_one()
    ticket_number = locked_board.next_ticket_number
    locked_board.next_ticket_number += 1
    ticket_key = f"{locked_board.key}-{ticket_number}"

    ticket = Ticket(
        key=ticket_key,
        board_id=locked_board.id,
        type=payload.type,
        title=payload.title,
        description=payload.description,
        state=initial_state(locked_board.workflow.states),
        reporter_id=actor.id,
        priority=payload.priority,
        epic_id=payload.epic_id,
        labels=payload.labels,
        acceptance_criteria=payload.acceptance_criteria,
        steps_to_reproduce=payload.steps_to_reproduce,
        expected_behavior=payload.expected_behavior,
        actual_behavior=payload.actual_behavior,
        story_points=payload.story_points,
        due_date=payload.due_date,
    )
    session.add(ticket)
    await session.flush()
    await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="created",
        new_value={"key": ticket.key, "title": ticket.title, "type": ticket.type},
    )
    await session.commit()
    return await get_ticket(session, ticket.key)


def _actor_roles(actor: Actor, board: Board) -> list[str]:
    return [membership.role for membership in actor.memberships if membership.board_id == board.id]


def _transition_allowed_by_workflow(ticket: Ticket, actor: Actor, to_state: str) -> bool:
    actor_roles = set(_actor_roles(actor, ticket.board))
    for transition in ticket.board.workflow.transitions:
        from_state = str(transition["from"])
        target_state = str(transition["to"])
        if from_state not in {ticket.state, "*"} or target_state != to_state:
            continue
        raw_allowed_roles = transition.get("allowed_roles", [])
        allowed_roles = (
            {str(role) for role in raw_allowed_roles}
            if isinstance(raw_allowed_roles, list)
            else set()
        )
        if allowed_roles & actor_roles:
            return True
        if "assignee" in allowed_roles and ticket.assignee_id == actor.id:
            return True
    return False


async def transition_ticket_state(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    to_state: str,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    if not _transition_allowed_by_workflow(ticket, actor, to_state):
        allowed = [
            str(transition["to"])
            for transition in ticket.board.workflow.transitions
            if str(transition["from"]) in {ticket.state, "*"}
        ]
        raise InvalidTransition(ticket.state, to_state, allowed)

    required_permission = f"state.transition:to_{to_state}"
    require_permission(actor, ticket.board, required_permission, resource=ticket)

    old_state = ticket.state
    ticket.state = to_state
    if to_state in {"done", "blocked"}:
        ticket.claimed_by = None
        ticket.claimed_at = None
    await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="state_changed",
        field="state",
        old_value=old_state,
        new_value=to_state,
    )
    await session.commit()
    return await get_ticket(session, ticket.key)


async def add_comment(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    payload: CommentCreate,
) -> Comment:
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, "comment.add", resource=ticket)
    comment = Comment(ticket_id=ticket.id, author_id=actor.id, body=payload.body)
    session.add(comment)
    await session.flush()
    await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="comment_added",
        new_value={"comment_id": str(comment.id)},
    )
    await session.commit()

    result = await session.execute(
        select(Comment)
        .where(Comment.id == comment.id)
        .options(selectinload(Comment.author))
    )
    saved_comment = result.scalar_one()
    return saved_comment


async def list_ticket_history(session: AsyncSession, ticket_id: str) -> list[TicketHistory]:
    ticket = await get_ticket(session, ticket_id)
    result = await session.execute(
        select(TicketHistory)
        .where(TicketHistory.ticket_id == ticket.id)
        .options(selectinload(TicketHistory.actor))
        .order_by(TicketHistory.created_at.desc())
        .limit(100)
    )
    return list(result.scalars())


async def claim_ticket(session: AsyncSession, *, actor: Actor, ticket_id: str) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, "ticket.claim", resource=ticket)
    if ticket.claimed_by is not None and ticket.claimed_by != actor.id:
        since = ticket.claimed_at.isoformat() if ticket.claimed_at else ""
        raise AlreadyClaimed(claimed_by=str(ticket.claimed_by), since=since)
    ticket.claimed_by = actor.id
    ticket.claimed_at = datetime.now(UTC)
    await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="claimed",
        new_value={"claimed_by": str(actor.id)},
    )
    await session.commit()
    return await get_ticket(session, ticket.key)
