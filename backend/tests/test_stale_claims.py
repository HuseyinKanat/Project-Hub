"""PH-340: stale-claim release backfills an empty assignee with the expiring claim owner.

``release_stale_claims()`` opens its OWN ``SessionLocal``; each test patches it to the
in-memory ``db_session`` (borrowed — the conftest fixture owns its lifecycle), mirroring
``test_cli.test_seed_backlog_*``. ``publish_ticket_event`` is patched to a no-op so no
Redis connection is attempted (it is a guarded no-op without Redis anyway).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Actor, Ticket, TicketHistory
from app.services import stale_claims


def _patch_session_and_events(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    @asynccontextmanager
    async def _fake_sessionlocal() -> AsyncIterator[AsyncSession]:
        yield db_session  # borrowed: do NOT close the fixture-owned session

    async def _noop_publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.services.stale_claims.SessionLocal", _fake_sessionlocal)
    monkeypatch.setattr("app.services.stale_claims.publish_ticket_event", _noop_publish)


async def _add_stale_ticket(
    db_session: AsyncSession,
    *,
    claim_owner: Actor,
    assignee_id: object | None,
) -> Ticket:
    """A ticket claimed by ``claim_owner`` whose claim is older than the timeout."""
    stale_at = datetime.now(UTC) - timedelta(
        seconds=stale_claims.CLAIM_TIMEOUT_SECONDS + 100
    )
    ticket = Ticket(
        id=uuid4(),
        key="TST-1",
        board_id=uuid4(),
        type="task",
        title="Long build",
        description="",
        state="in_progress",
        reporter_id=claim_owner.id,
        assignee_id=assignee_id,
        priority="medium",
        labels=[],
        claimed_by=claim_owner.id,
        claimed_at=stale_at,
        agent_phase={"phase": "coding"},
    )
    db_session.add(ticket)
    await db_session.commit()
    return ticket


async def _released_event(db_session: AsyncSession, ticket_id: object) -> TicketHistory:
    return (
        await db_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket_id,
                TicketHistory.event_type == "released",
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_release_stale_claims_backfills_null_assignee_to_claim_owner(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2/AC-3: with assignee_id NULL, the stale release pins assignee to the
    expiring claim owner (same tx, before claimed_by is nulled) and records the pin
    in the ``released`` history event so the agent keeps if_assignee write authority."""
    agent = Actor(id=uuid4(), kind="agent", display_name="Backend Bot", token_hash="x")
    db_session.add(agent)
    await db_session.flush()
    ticket = await _add_stale_ticket(db_session, claim_owner=agent, assignee_id=None)

    _patch_session_and_events(monkeypatch, db_session)
    released = await stale_claims.release_stale_claims()
    assert released == 1

    fresh = (
        await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
    ).scalar_one()
    assert fresh.assignee_id == agent.id  # backfilled to the expiring claim owner
    assert fresh.claimed_by is None
    assert fresh.claimed_at is None

    event = await _released_event(db_session, ticket.id)
    assert event.new_value["reason"] == "stale_claim_timeout"
    assert event.new_value["assignee_id"] == str(agent.id)  # AC-3: pin is auditable


@pytest.mark.asyncio
async def test_release_stale_claims_preserves_existing_assignee(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2 invariant: a NON-NULL assignee is never overwritten by the backfill —
    the Coordinator's rotation authority is preserved. No assignee_id pin is written
    to the ``released`` event when no backfill happened."""
    agent = Actor(id=uuid4(), kind="agent", display_name="Backend Bot", token_hash="x")
    other_assignee = Actor(id=uuid4(), kind="agent", display_name="Reviewer Bot", token_hash="x")
    db_session.add_all([agent, other_assignee])
    await db_session.flush()
    ticket = await _add_stale_ticket(
        db_session, claim_owner=agent, assignee_id=other_assignee.id
    )

    _patch_session_and_events(monkeypatch, db_session)
    released = await stale_claims.release_stale_claims()
    assert released == 1

    fresh = (
        await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
    ).scalar_one()
    assert fresh.assignee_id == other_assignee.id  # untouched — NOT overwritten
    assert fresh.claimed_by is None

    event = await _released_event(db_session, ticket.id)
    assert event.new_value["reason"] == "stale_claim_timeout"
    assert "assignee_id" not in event.new_value  # no backfill → no pin recorded
