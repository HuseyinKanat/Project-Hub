"""Integration tests for ticket lifecycle services against an in-memory db."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyClaimed,
    FieldGateNotMet,
    InvalidTransition,
    NotFound,
    PermissionDenied,
)
from app.schemas import (
    AgentPhaseUpdate,
    AssignTicket,
    DeleteTicket,
    TicketCreate,
    TicketUpdate,
)
from app.services.tickets import (
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
from tests.conftest import Seed


async def _new_ticket(session: AsyncSession, seed: Seed, **overrides: object):
    payload = TicketCreate(
        board_id=seed.board.key,
        type="task",
        title=str(overrides.pop("title", "Implement search")),
        description="Compact ticket search.",
        priority="medium",
        labels=list(overrides.pop("labels", ["mcp"])),  # type: ignore[arg-type]
    )
    return await create_ticket(session, actor=seed.admin, payload=payload)


async def test_create_ticket_assigns_initial_state_and_writes_history(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)

    assert ticket.key == "PH-1"
    assert ticket.state == "backlog"
    assert ticket.reporter_id == seed.admin.id
    assert ticket.labels == ["mcp"]

    history = await list_ticket_history(db_session, ticket.key)
    assert [event.event_type for event in history] == ["created"]


async def test_ticket_keys_are_sequential_per_board(
    db_session: AsyncSession, seed: Seed
) -> None:
    first = await _new_ticket(db_session, seed, title="One")
    second = await _new_ticket(db_session, seed, title="Two")
    assert (first.key, second.key) == ("PH-1", "PH-2")


async def test_update_ticket_records_field_changes(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)

    updated = await update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=TicketUpdate(priority="high", labels=["mcp", "urgent"]),
    )
    assert updated.priority == "high"
    assert updated.labels == ["mcp", "urgent"]

    history = await list_ticket_history(db_session, ticket.key)
    field_events = [event for event in history if event.event_type == "field_changed"]
    fields_changed = sorted(event.field for event in field_events if event.field)
    assert fields_changed == ["labels", "priority"]


async def test_backend_dev_cannot_update_field_when_not_assignee(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    with pytest.raises(PermissionDenied):
        await update_ticket(
            db_session,
            actor=seed.backend,
            ticket_id=ticket.key,
            payload=TicketUpdate(priority="urgent"),
        )


async def test_assign_then_backend_dev_can_update_field(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await assign_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=seed.backend.agent_id),
    )
    updated = await update_ticket(
        db_session,
        actor=seed.backend,
        ticket_id=ticket.key,
        payload=TicketUpdate(priority="urgent"),
    )
    assert updated.assignee_id == seed.backend.id
    assert updated.priority == "urgent"


async def test_claim_conflict_raises_already_claimed(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await assign_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=seed.backend.agent_id),
    )

    await claim_ticket(db_session, actor=seed.backend, ticket_id=ticket.key)

    # Admin has '*' so passes the permission check; the conflict surfaces.
    with pytest.raises(AlreadyClaimed):
        await claim_ticket(db_session, actor=seed.admin, ticket_id=ticket.key)


async def test_release_clears_claim_and_phase(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await assign_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=seed.backend.agent_id),
    )
    await update_agent_phase(
        db_session,
        actor=seed.backend,
        ticket_id=ticket.key,
        payload=AgentPhaseUpdate(phase="coding", message="Hacking"),
    )
    released = await release_ticket(db_session, actor=seed.backend, ticket_id=ticket.key)
    assert released.claimed_by is None
    assert released.claimed_at is None
    assert released.agent_phase is None


async def test_transition_to_done_clears_claim_and_writes_history(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await claim_ticket(db_session, actor=seed.admin, ticket_id=ticket.key)

    transitioned = await transition_ticket_state(
        db_session,
        actor=seed.pm,
        ticket_id=ticket.key,
        to_state="done",
    )
    assert transitioned.state == "done"
    assert transitioned.claimed_by is None
    assert transitioned.claimed_at is None

    history = await list_ticket_history(db_session, ticket.key)
    state_changes = [event for event in history if event.event_type == "state_changed"]
    assert len(state_changes) == 1
    assert state_changes[0].old_value == "backlog"
    assert state_changes[0].new_value == "done"


async def test_invalid_transition_lists_allowed_targets(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    with pytest.raises(InvalidTransition) as exc:
        await transition_ticket_state(
            db_session,
            actor=seed.pm,
            ticket_id=ticket.key,
            to_state="in_review",
        )
    assert "to_do" in exc.value.allowed
    assert "done" in exc.value.allowed


async def test_update_agent_phase_implicit_claim(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    updated = await update_agent_phase(
        db_session,
        actor=seed.backend,
        ticket_id=ticket.key,
        payload=AgentPhaseUpdate(phase="planning", message="Reading"),
    )
    assert updated.claimed_by == seed.backend.id
    assert updated.agent_phase is not None
    assert updated.agent_phase["phase"] == "planning"
    assert updated.agent_phase["agent_id"] == "claude-backend-1"


async def test_soft_delete_hides_ticket_from_get(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await delete_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=DeleteTicket(reason="duplicate"),
    )
    with pytest.raises(NotFound):
        await get_ticket(db_session, ticket.key)


async def test_query_tickets_filters_by_board_and_state(
    db_session: AsyncSession, seed: Seed
) -> None:
    a = await _new_ticket(db_session, seed, title="A")
    b = await _new_ticket(db_session, seed, title="B")
    await transition_ticket_state(
        db_session,
        actor=seed.pm,
        ticket_id=a.key,
        to_state="to_do",
    )

    backlog = await query_tickets(db_session, board_id=seed.board.key, state="backlog")
    to_do = await query_tickets(db_session, board_id=seed.board.key, state="to_do")

    assert {ticket.key for ticket in backlog} == {b.key}
    assert {ticket.key for ticket in to_do} == {a.key}


async def test_transition_to_in_progress_has_no_gate(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=ticket.key, to_state="to_do"
    )
    # to_do -> in_progress no longer has a gate; should succeed with no fields set
    transitioned = await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=ticket.key, to_state="in_progress"
    )
    assert transitioned.state == "in_progress"


async def test_transition_to_in_review_blocked_when_fields_missing(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=ticket.key, to_state="to_do"
    )
    # Assign backend_dev so they can do in_progress -> in_review (assignee role required)
    await assign_ticket(
        db_session, actor=seed.admin, ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=str(seed.backend.id)),
    )
    await transition_ticket_state(
        db_session, actor=seed.backend, ticket_id=ticket.key, to_state="in_progress"
    )

    with pytest.raises(FieldGateNotMet) as exc:
        await transition_ticket_state(
            db_session, actor=seed.backend, ticket_id=ticket.key, to_state="in_review"
        )

    assert set(exc.value.missing_fields) == {"technical_depth", "acceptance_criteria"}
    assert exc.value.transition == "in_progress->in_review"


async def test_transition_to_in_review_succeeds_after_fields_filled(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _new_ticket(db_session, seed)
    await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=ticket.key, to_state="to_do"
    )
    # Assign backend_dev so they can do in_progress -> in_review (assignee role required)
    await assign_ticket(
        db_session, actor=seed.admin, ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=str(seed.backend.id)),
    )
    await transition_ticket_state(
        db_session, actor=seed.backend, ticket_id=ticket.key, to_state="in_progress"
    )
    await update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=TicketUpdate(
            technical_depth="## Technical Debt\n- retry not implemented",
            acceptance_criteria="## DoD\n- [x] service written",
        ),
    )

    transitioned = await transition_ticket_state(
        db_session, actor=seed.backend, ticket_id=ticket.key, to_state="in_review"
    )
    assert transitioned.state == "in_review"
    assert transitioned.technical_depth is not None
    assert transitioned.acceptance_criteria is not None


async def test_epic_transition_skips_field_gate(
    db_session: AsyncSession, seed: Seed
) -> None:
    epic = await create_ticket(
        db_session,
        actor=seed.admin,
        payload=TicketCreate(
            board_id=seed.board.key,
            type="epic",
            title="Quarterly initiative",
            description="Umbrella epic",
            priority="medium",
        ),
    )
    await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=epic.key, to_state="to_do"
    )
    transitioned = await transition_ticket_state(
        db_session, actor=seed.pm, ticket_id=epic.key, to_state="in_progress"
    )
    assert transitioned.state == "in_progress"
    assert transitioned.technical_depth is None


async def test_admin_bypasses_workflow_allowed_roles(
    db_session: AsyncSession, seed: Seed
) -> None:
    """Admin role'u workflow allowed_roles ile kisitlanmaz; field gate'leri yine
    enforced edilir (gate, role degil field-level guard'dir)."""
    ticket = await _new_ticket(db_session, seed)
    # backlog -> to_do allowed_roles=['pm', 'architect']; admin yine de gecebilmeli.
    transitioned = await transition_ticket_state(
        db_session, actor=seed.admin, ticket_id=ticket.key, to_state="to_do"
    )
    assert transitioned.state == "to_do"

    # to_do->in_progress has no gate; admin can transition freely
    transitioned_ip = await transition_ticket_state(
        db_session, actor=seed.admin, ticket_id=ticket.key, to_state="in_progress"
    )
    assert transitioned_ip.state == "in_progress"
    # Admin still blocked by in_progress->in_review gate (field-level guard)
    with pytest.raises(FieldGateNotMet):
        await transition_ticket_state(
            db_session, actor=seed.admin, ticket_id=ticket.key, to_state="in_review"
        )


async def test_create_ticket_persists_technical_depth(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await create_ticket(
        db_session,
        actor=seed.admin,
        payload=TicketCreate(
            board_id=seed.board.key,
            type="task",
            title="With depth",
            description="",
            priority="medium",
            technical_depth="## Plan\n- step 1",
        ),
    )
    assert ticket.technical_depth == "## Plan\n- step 1"

@pytest.mark.asyncio
async def test_ph294_globs_and_blocked_by_round_trip(db_session: AsyncSession, seed: Seed) -> None:
    """PH-294: files_touched_globs + blocked_by are REAL columns now — the Jarwis
    parallel-independence test and epic topo-sort read them structurally. Before
    PH-294 both were silently dropped as unknown Pydantic fields, so lock the
    full round-trip: create carries blocked_by, update carries globs, and the
    serialized response exposes both."""
    from app.services.serializers import ticket_response

    blocker = await _new_ticket(db_session, seed, title="Blocker ticket")
    payload = TicketCreate(
        board_id=seed.board.key,
        type="task",
        title="Dependent ticket",
        description="depends on blocker",
        priority="medium",
        labels=[],
        blocked_by=[blocker.key],
    )
    dependent = await create_ticket(db_session, actor=seed.admin, payload=payload)
    assert dependent.blocked_by == [blocker.key]

    updated = await update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=dependent.key,
        payload=TicketUpdate(files_touched_globs=["src/auth/**", "tests/test_auth.py"]),
    )
    assert updated.files_touched_globs == ["src/auth/**", "tests/test_auth.py"]

    body = ticket_response(updated).model_dump(mode="json", by_alias=True)
    assert body["files_touched_globs"] == ["src/auth/**", "tests/test_auth.py"]
    assert body["blocked_by"] == [blocker.key]

