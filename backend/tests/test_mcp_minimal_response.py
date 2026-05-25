"""MCP write-tool minimal-response default + verbose=true opt-in.

Six tools used to render the full ticket payload on every call (heartbeat
included). They now return compact responses by default; passing verbose=true
restores the legacy full payload for callers that need it.
"""

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.server import _dispatch_tool
from app.schemas import AssignTicket, TicketCreate
from app.services.tickets import assign_ticket, claim_ticket, create_ticket
from tests.conftest import Seed


MINIMAL_MAX_CHARS = 400
FULL_MIN_CHARS = 800


def _size(result: Any) -> int:
    return len(json.dumps(result, default=str))


async def _new_ticket(session: AsyncSession, seed: Seed):
    return await create_ticket(
        session,
        actor=seed.admin,
        payload=TicketCreate(
            board_id=seed.board.key,
            type="task",
            title="Minimal response coverage",
            description="Verify compact dispatch responses.",
            priority="medium",
            labels=["mcp"],
        ),
    )


async def _assigned_ticket(session: AsyncSession, seed: Seed):
    ticket = await _new_ticket(session, seed)
    await assign_ticket(
        session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=AssignTicket(assignee_id=str(seed.backend.id)),
    )
    return ticket


@pytest.mark.asyncio
async def test_assign_ticket_minimal_default(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "assign_ticket",
        {"id": ticket.key, "assignee_id": str(seed.backend.id)},
        seed.admin,
        db_session,
    )

    assert result == {
        "ok": True,
        "id": ticket.key,
        "assignee_id": str(seed.backend.id),
    }
    assert _size(result) < MINIMAL_MAX_CHARS


@pytest.mark.asyncio
async def test_assign_ticket_verbose_returns_full_payload(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "assign_ticket",
        {"id": ticket.key, "assignee_id": str(seed.backend.id), "verbose": True},
        seed.admin,
        db_session,
    )

    assert result["key"] == ticket.key
    # Full payload signature: top-level metadata blocks present
    for field in ("description", "technical_depth", "acceptance_criteria", "_links"):
        assert field in result
    assert _size(result) > FULL_MIN_CHARS


@pytest.mark.asyncio
async def test_transition_state_minimal_default(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "transition_state",
        {"id": ticket.key, "to_state": "to_do"},
        seed.admin,
        db_session,
    )

    assert result == {
        "ok": True,
        "id": ticket.key,
        "from_state": "backlog",
        "to_state": "to_do",
    }
    assert _size(result) < MINIMAL_MAX_CHARS


@pytest.mark.asyncio
async def test_transition_state_verbose_returns_full_payload(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "transition_state",
        {"id": ticket.key, "to_state": "to_do", "verbose": True},
        seed.admin,
        db_session,
    )

    assert result["key"] == ticket.key
    assert result["state"] == "to_do"
    assert "technical_depth" in result
    assert _size(result) > FULL_MIN_CHARS


@pytest.mark.asyncio
async def test_claim_ticket_minimal_default(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)

    result = await _dispatch_tool(
        "claim_ticket",
        {"id": ticket.key},
        seed.backend,
        db_session,
    )

    assert result["ok"] is True
    assert result["id"] == ticket.key
    assert result["claimed_by"] == str(seed.backend.id)
    assert result["claimed_at"] is not None
    assert set(result.keys()) == {
        "ok",
        "id",
        "claimed_by",
        "claimed_at",
        "branch_name",
        "state",
    }
    assert _size(result) < MINIMAL_MAX_CHARS


@pytest.mark.asyncio
async def test_claim_ticket_verbose_returns_full_payload(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)

    result = await _dispatch_tool(
        "claim_ticket",
        {"id": ticket.key, "verbose": True},
        seed.backend,
        db_session,
    )

    assert result["key"] == ticket.key
    assert "description" in result
    assert _size(result) > FULL_MIN_CHARS


@pytest.mark.asyncio
async def test_release_ticket_minimal_default(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)
    await claim_ticket(db_session, actor=seed.backend, ticket_id=ticket.key)

    result = await _dispatch_tool(
        "release_ticket",
        {"id": ticket.key},
        seed.backend,
        db_session,
    )

    assert result["ok"] is True
    assert result["id"] == ticket.key
    assert "state" in result
    assert set(result.keys()) == {"ok", "id", "state"}
    assert _size(result) < 200


@pytest.mark.asyncio
async def test_release_ticket_verbose_returns_full_payload(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)
    await claim_ticket(db_session, actor=seed.backend, ticket_id=ticket.key)

    result = await _dispatch_tool(
        "release_ticket",
        {"id": ticket.key, "verbose": True},
        seed.backend,
        db_session,
    )

    assert result["key"] == ticket.key
    assert "description" in result
    assert _size(result) > FULL_MIN_CHARS


@pytest.mark.asyncio
async def test_update_agent_phase_minimal_default(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)

    result = await _dispatch_tool(
        "update_agent_phase",
        {"id": ticket.key, "phase": "coding", "message": "wiring endpoint"},
        seed.backend,
        db_session,
    )

    assert result["ok"] is True
    assert result["id"] == ticket.key
    assert result["phase"] == "coding"
    assert result["ts"] is not None
    assert set(result.keys()) == {"ok", "id", "phase", "ts"}
    assert _size(result) < 300


@pytest.mark.asyncio
async def test_update_agent_phase_verbose_returns_full_payload(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)

    result = await _dispatch_tool(
        "update_agent_phase",
        {"id": ticket.key, "phase": "coding", "verbose": True},
        seed.backend,
        db_session,
    )

    assert result["key"] == ticket.key
    assert result["agent_phase"]["phase"] == "coding"
    assert _size(result) > FULL_MIN_CHARS


@pytest.mark.asyncio
async def test_update_ticket_minimal_default(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "update_ticket",
        {
            "id": ticket.key,
            "fields": {"priority": "high", "labels": ["mcp", "urgent"]},
        },
        seed.admin,
        db_session,
    )

    assert result["ok"] is True
    assert result["id"] == ticket.key
    assert set(result["updated_fields"]) == {"priority", "labels"}
    assert "state" in result
    assert _size(result) < MINIMAL_MAX_CHARS


@pytest.mark.asyncio
async def test_update_ticket_verbose_returns_full_payload(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "update_ticket",
        {
            "id": ticket.key,
            "fields": {"priority": "high"},
            "verbose": True,
        },
        seed.admin,
        db_session,
    )

    assert result["key"] == ticket.key
    assert result["priority"] == "high"
    assert _size(result) > FULL_MIN_CHARS
