"""Stale claim cron: auto-release tickets whose claims have timed out.

Runs as an asyncio background task every INTERVAL_SECONDS.
A claim is considered stale if:
    claimed_at + CLAIM_TIMEOUT_SECONDS < now
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Actor, Ticket
from app.db.session import SessionLocal
from app.events.dispatcher import publish_ticket_event
from app.services.tickets import write_history

logger = get_logger(__name__)

CLAIM_TIMEOUT_SECONDS: int = 300  # 5 minutes without heartbeat → stale
INTERVAL_SECONDS: int = 60  # check every 60 seconds


async def release_stale_claims() -> int:
    """Release all stale claims in one pass. Returns number of tickets released."""
    cutoff = datetime.now(UTC) - timedelta(seconds=CLAIM_TIMEOUT_SECONDS)
    released = 0

    async with SessionLocal() as session:
        stale_tickets = (
            await session.execute(
                select(Ticket).where(
                    Ticket.claimed_by.is_not(None),
                    Ticket.claimed_at < cutoff,
                )
            )
        ).scalars().all()

        if not stale_tickets:
            return 0

        system_actor = (
            await session.execute(
                select(Actor).where(Actor.kind == "agent", Actor.display_name == "system")
            )
        ).scalar_one_or_none()

        for ticket in stale_tickets:
            old_claimed_by = str(ticket.claimed_by)
            old_agent_phase = ticket.agent_phase
            # PH-340 (b): if the ticket has NO assignee, pin it to the expiring
            # claim owner BEFORE clearing claimed_by — so the de-facto owner keeps
            # its if_assignee write authority (via the assignee_id path) even after
            # the cron drops the claim during a long (> CLAIM_TIMEOUT_SECONDS) build.
            # A NON-NULL assignee is NEVER overwritten: the Coordinator's rotation
            # authority is preserved; the server only fills a NULL with the obvious
            # owner. Rationale + rejected alternative (a):
            # docs/adr/0001-stale-claim-assignee-backfill.md.
            backfilled_assignee: str | None = None
            if ticket.assignee_id is None:
                ticket.assignee_id = ticket.claimed_by
                backfilled_assignee = old_claimed_by
            ticket.claimed_by = None
            ticket.claimed_at = None
            ticket.agent_phase = None

            actor_id = system_actor.id if system_actor else ticket.reporter_id
            new_value: dict[str, Any] = {"claimed_by": None, "reason": "stale_claim_timeout"}
            if backfilled_assignee is not None:
                # AC-3: the pin is visible in history so an agent can correlate its
                # regained write authority with the release event.
                new_value["assignee_id"] = backfilled_assignee
            history = await write_history(
                session,
                ticket_id=ticket.id,
                actor_id=actor_id,
                event_type="released",
                old_value={"claimed_by": old_claimed_by, "agent_phase": old_agent_phase},
                new_value=new_value,
            )
            await session.flush()
            released += 1
            logger.info(
                "stale_claim_released ticket=%s claimed_by=%s claimed_at=%s",
                ticket.key,
                old_claimed_by,
                ticket.claimed_at,
            )

        if released:
            await session.commit()
            for ticket in stale_tickets:
                ticket_fresh = (
                    await session.execute(select(Ticket).where(Ticket.id == ticket.id))
                ).scalar_one_or_none()
                if ticket_fresh:
                    await publish_ticket_event(history, ticket_fresh, None)

    return released


async def stale_claim_cron() -> None:
    """Background task: periodically release stale claims."""
    logger.info("stale_claim_cron started interval=%ds timeout=%ds", INTERVAL_SECONDS, CLAIM_TIMEOUT_SECONDS)
    while True:
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
            n = await release_stale_claims()
            if n:
                logger.info("stale_claim_cron released=%d", n)
        except asyncio.CancelledError:
            # Cooperative cancellation: log, then re-raise so the framework
            # sees the task acknowledged the cancel (graceful shutdown).
            logger.info("stale_claim_cron stopped")
            raise
        except Exception as e:
            logger.warning("stale_claim_cron error=%s", str(e))
