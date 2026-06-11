"""Ticket service logic."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.base import ExecutableOption

from app.core.exceptions import (
    AlreadyClaimed,
    FieldGateNotMet,
    InvalidTransition,
    NotFound,
    PermissionDenied,
)
from app.core.permissions import require_permission
from app.db.models import Actor, Board, Comment, Ticket, TicketHistory
from app.events import publish_ticket_event
from app.schemas import (
    AgentPhaseUpdate,
    AssignTicket,
    CommentCreate,
    DeleteTicket,
    TicketCreate,
    TicketUpdate,
)
from app.services.actors import get_actor
from app.services.boards import get_active_workflow, get_board, parse_uuid
from app.services.defaults import initial_state
from app.services.history import write_history
from app.services.notifications import (
    notify_comment_added,
    notify_state_changed,
)
from app.services.workflows import get_field_gates_for_ticket_transition


def _ticket_load_options() -> tuple[ExecutableOption, ...]:
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
        technical_depth=payload.technical_depth,
        steps_to_reproduce=payload.steps_to_reproduce,
        expected_behavior=payload.expected_behavior,
        actual_behavior=payload.actual_behavior,
        story_points=payload.story_points,
        due_date=payload.due_date,
    )
    session.add(ticket)
    await session.flush()
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="created",
        new_value={"key": ticket.key, "title": ticket.title, "type": ticket.type},
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    return ticket


async def update_ticket(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    payload: TicketUpdate,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    changes = payload.model_dump(exclude_unset=True)
    histories: list[tuple[Any, str, Any, Any]] = []

    for field in changes:
        require_permission(actor, ticket.board, f"ticket.update_field:{field}", resource=ticket)

    for field, new_value in changes.items():
        old_value = getattr(ticket, field)
        if old_value == new_value:
            continue
        setattr(ticket, field, new_value)
        history = await write_history(
            session,
            ticket_id=ticket.id,
            actor_id=actor.id,
            event_type="field_changed",
            field=field,
            old_value=_json_safe(old_value),
            new_value=_json_safe(new_value),
        )
        histories.append((history, field, _json_safe(old_value), _json_safe(new_value)))

    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    for history, field, old, new in histories:
        await publish_ticket_event(history, ticket, actor, extra_payload={"field": field, "old_value": old, "new_value": new})
    return ticket


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


async def assign_ticket(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    payload: AssignTicket,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, "ticket.assign", resource=ticket)
    old_assignee = str(ticket.assignee_id) if ticket.assignee_id else None
    assignee = await get_actor(session, payload.assignee_id) if payload.assignee_id else None
    ticket.assignee_id = assignee.id if assignee else None
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="assigned" if assignee else "unassigned",
        field="assignee_id",
        old_value=old_assignee,
        new_value=str(assignee.id) if assignee else None,
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    return ticket


def _actor_roles(actor: Actor, board: Board) -> list[str]:
    return [membership.role for membership in actor.memberships if membership.board_id == board.id]


def _transition_matches(transition: dict[str, Any], from_state: str, to_state: str) -> bool:
    """True when a workflow transition entry connects ``from_state`` → ``to_state``."""
    entry_from = str(transition["from"])
    entry_to = str(transition["to"])
    return entry_from in {from_state, "*"} and entry_to == to_state


def _transition_allowed_roles(transition: dict[str, Any]) -> set[str]:
    """Parse a transition entry's ``allowed_roles`` into a set (tolerant of bad data)."""
    raw_allowed_roles = transition.get("allowed_roles", [])
    if isinstance(raw_allowed_roles, list):
        return {str(role) for role in raw_allowed_roles}
    return set()


def _actor_satisfies_transition(
    transition: dict[str, Any], ticket: Ticket, actor: Actor, actor_roles: set[str]
) -> bool:
    """Whether ``actor`` (by role or claim/assignee) may take this matched transition."""
    allowed_roles = _transition_allowed_roles(transition)
    if allowed_roles & actor_roles:
        return True
    # Claim sahibi de "assignee" rolünde sayılır. Agent claim_ticket çağırdığında
    # assign_ticket'i unutsa bile workflow gate'i takılmaz. Bu Jarwis pilot'unda
    # agent'ların claim+transition yapıp assign'i atlamasıyla oluşan
    # permission_denied/invalid_transition zincirini çözer (FN-2 ve benzeri vakalar).
    return "assignee" in allowed_roles and (
        ticket.assignee_id == actor.id or ticket.claimed_by == actor.id
    )


def _admin_transition_allowed(
    transitions: list[dict[str, Any]], from_state: str, to_state: str
) -> bool:
    """Admin bypass: allowed_roles guard skipped; only the edge must exist.

    Downstream ``require_permission('state.transition:to_*')`` hala calisir.
    """
    return any(_transition_matches(t, from_state, to_state) for t in transitions)


def _transition_allowed_by_workflow(ticket: Ticket, actor: Actor, to_state: str) -> bool:
    actor_roles = set(_actor_roles(actor, ticket.board))
    transitions = ticket.board.workflow.transitions
    if "admin" in actor_roles:
        return _admin_transition_allowed(transitions, ticket.state, to_state)
    for transition in transitions:
        if not _transition_matches(transition, ticket.state, to_state):
            continue
        if _actor_satisfies_transition(transition, ticket, actor, actor_roles):
            return True
    return False


async def _transition_allowed_by_active_workflow(
    session: AsyncSession, ticket: Ticket, actor: Actor, to_state: str
) -> tuple[bool, list[str]]:
    """Check if transition is allowed by active workflow, returning (allowed, available_transitions)."""
    workflow = await get_active_workflow(session, ticket.board_id)
    actor_roles = set(_actor_roles(actor, ticket.board))

    # Get available transitions for error reporting
    available_transitions = [
        str(transition["to"])
        for transition in workflow.transitions
        if str(transition["from"]) in {ticket.state, "*"}
    ]

    if "admin" in actor_roles:
        # Admin role bypasses workflow allowed_roles guard
        allowed = _admin_transition_allowed(workflow.transitions, ticket.state, to_state)
        return allowed, available_transitions

    for transition in workflow.transitions:
        if not _transition_matches(transition, ticket.state, to_state):
            continue
        if _actor_satisfies_transition(transition, ticket, actor, actor_roles):
            return True, available_transitions
    return False, available_transitions


async def _missing_gate_fields(
    session: AsyncSession, ticket: Ticket, to_state: str
) -> list[str]:
    """
    Check for missing required fields in ticket transition using workflow configuration.

    Returns list of missing field names that must be filled before transition can proceed.
    Uses workflow JSON configuration instead of hardcoded field gates.
    """
    # Get field gates from workflow configuration
    required_fields, exempt_types = await get_field_gates_for_ticket_transition(
        session, ticket.board_id, ticket.state, to_state
    )

    # Check if ticket type is exempt
    if ticket.type in exempt_types:
        return []

    # Check for missing required fields
    missing: list[str] = []
    for field in required_fields:
        value = getattr(ticket, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


async def transition_ticket_state(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    to_state: str,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    allowed, available_transitions = await _transition_allowed_by_active_workflow(
        session, ticket, actor, to_state
    )
    if not allowed:
        raise InvalidTransition(ticket.state, to_state, available_transitions)

    required_permission = f"state.transition:to_{to_state}"
    require_permission(actor, ticket.board, required_permission, resource=ticket)

    missing = await _missing_gate_fields(session, ticket, to_state)
    if missing:
        raise FieldGateNotMet(
            transition=f"{ticket.state}->{to_state}",
            missing_fields=missing,
        )

    old_state = ticket.state
    ticket.state = to_state
    if to_state in {"done", "blocked"}:
        ticket.claimed_by = None
        ticket.claimed_at = None
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="state_changed",
        field="state",
        old_value=old_state,
        new_value=to_state,
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    await notify_state_changed(
        session, ticket=ticket, actor_id=actor.id, old_state=old_state, new_state=to_state
    )
    await session.commit()
    return ticket


async def list_comments(
    session: AsyncSession,
    ticket_id: str,
) -> list[Comment]:
    """Return comments on a ticket ordered oldest → newest, with author eagerly loaded."""
    ticket = await get_ticket(session, ticket_id)
    result = await session.execute(
        select(Comment)
        .where(Comment.ticket_id == ticket.id)
        .options(selectinload(Comment.author))
        .order_by(Comment.created_at.asc())
    )
    return list(result.scalars())


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
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="comment_added",
        new_value={"comment_id": str(comment.id)},
    )
    await session.commit()

    # Publish after commit
    ticket_for_event = await get_ticket(session, ticket_id)
    await publish_ticket_event(history, ticket_for_event, actor)
    await notify_comment_added(
        session, ticket=ticket_for_event, actor_id=actor.id, author_name=actor.display_name
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
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="claimed",
        new_value={"claimed_by": str(actor.id)},
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    return ticket


async def release_ticket(session: AsyncSession, *, actor: Actor, ticket_id: str) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    if ticket.claimed_by is not None and ticket.claimed_by != actor.id:
        # Claim sahibi olmayan biri release etmeye çalışıyor — board'da
        # ticket.release:* (PM/admin) permission'ı varsa izin ver, yoksa 403.
        # Bu Coordinator'ın (PM token) sub-agent claim'lerini release
        # etmesine izin verir (Jarwis exit-protocol §2.6).
        try:
            require_permission(actor, ticket.board, "ticket.release:any", resource=ticket)
        except PermissionDenied:
            raise PermissionDenied(required="ticket.release:if_claimed_by", have=[]) from None

    old_claimed_by = str(ticket.claimed_by) if ticket.claimed_by else None
    ticket.claimed_by = None
    ticket.claimed_at = None
    ticket.agent_phase = None
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="released",
        old_value={"claimed_by": old_claimed_by},
        new_value={"claimed_by": None},
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    return ticket


async def update_agent_phase(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    payload: AgentPhaseUpdate,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, "ticket.claim", resource=ticket)
    if ticket.claimed_by is not None and ticket.claimed_by != actor.id:
        since = ticket.claimed_at.isoformat() if ticket.claimed_at else ""
        raise AlreadyClaimed(claimed_by=str(ticket.claimed_by), since=since)

    now = datetime.now(UTC)
    old_phase = ticket.agent_phase
    ticket.claimed_by = actor.id
    ticket.claimed_at = ticket.claimed_at or now
    ticket.agent_phase = {
        "agent_id": actor.agent_id or str(actor.id),
        "phase": payload.phase,
        "message": payload.message,
        "started_at": ticket.claimed_at.isoformat(),
        "last_heartbeat_at": now.isoformat(),
    }
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="phase_updated",
        field="agent_phase",
        old_value=old_phase,
        new_value=ticket.agent_phase,
    )
    await session.commit()
    ticket = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket, actor)
    return ticket


async def delete_ticket(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    payload: DeleteTicket,
) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, "ticket.delete", resource=ticket)
    ticket.deleted_at = datetime.now(UTC)
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="deleted",
        new_value={"reason": payload.reason},
    )
    await session.commit()
    await publish_ticket_event(history, ticket, actor)
    return ticket
