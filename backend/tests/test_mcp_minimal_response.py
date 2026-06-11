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


# ----- get_state -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state_returns_compact_snapshot(db_session, seed):
    ticket = await _assigned_ticket(db_session, seed)
    await claim_ticket(db_session, actor=seed.backend, ticket_id=ticket.key)

    result = await _dispatch_tool(
        "get_state",
        {"id": ticket.key},
        seed.admin,
        db_session,
    )

    assert result["id"] == ticket.key
    assert result["state"] == "backlog"
    assert result["assignee_id"] == str(seed.backend.id)
    assert result["claim_owner"] == str(seed.backend.id)
    assert "branch_name" in result
    assert "last_phase" in result
    assert "last_heartbeat_at" in result
    assert "updated_at" in result
    # Compact contract: <300 chars regardless of ticket size
    assert _size(result) < 300


@pytest.mark.asyncio
async def test_get_state_size_independent_of_ticket_payload(db_session, seed):
    ticket = await _new_ticket(db_session, seed)
    # Bloat the ticket with large fields
    from app.schemas import TicketUpdate
    from app.services.tickets import update_ticket as svc_update_ticket
    await svc_update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=TicketUpdate(
            technical_depth="x" * 4000,
            impact_analysis="y" * 4000,
            description="z" * 4000,
        ),
    )

    state_result = await _dispatch_tool("get_state", {"id": ticket.key}, seed.admin, db_session)
    full_result = await _dispatch_tool("get_ticket", {"id": ticket.key}, seed.admin, db_session)

    # State probe stays tiny even when the ticket is huge
    assert _size(state_result) < 300
    assert _size(full_result) > 12_000
    assert _size(full_result) / _size(state_result) > 40  # 40x+ reduction


# ----- get_ticket_slice ----------------------------------------------------


@pytest.mark.asyncio
async def test_slice_returns_skeleton_when_include_empty(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "get_ticket_slice",
        {"id": ticket.key, "include": []},
        seed.admin,
        db_session,
    )

    # Skeleton: only id, key, state
    assert set(result.keys()) == {"id", "key", "state"}
    assert result["key"] == ticket.key
    assert _size(result) < 200


@pytest.mark.asyncio
async def test_slice_projects_requested_fields(db_session, seed):
    ticket = await _new_ticket(db_session, seed)
    from app.schemas import TicketUpdate
    from app.services.tickets import update_ticket as svc_update_ticket
    await svc_update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=TicketUpdate(
            technical_depth="Plan: build endpoint with version service.",
            acceptance_criteria="GET /health/version returns {version, commit}.",
        ),
    )

    result = await _dispatch_tool(
        "get_ticket_slice",
        {
            "id": ticket.key,
            "include": ["description", "acceptance_criteria", "technical_depth", "branch_name"],
        },
        seed.admin,
        db_session,
    )

    assert set(result.keys()) == {
        "id",
        "key",
        "state",
        "description",
        "acceptance_criteria",
        "technical_depth",
        "branch_name",
    }
    assert "Plan: build endpoint" in result["technical_depth"]


@pytest.mark.asyncio
async def test_slice_ignores_unknown_fields(db_session, seed):
    ticket = await _new_ticket(db_session, seed)

    result = await _dispatch_tool(
        "get_ticket_slice",
        {
            "id": ticket.key,
            "include": ["state", "no_such_field", "definitely_not_real"],
        },
        seed.admin,
        db_session,
    )

    # Unknown names silently dropped; skeleton + valid fields only
    assert set(result.keys()) == {"id", "key", "state"}


@pytest.mark.asyncio
async def test_slice_smaller_than_full_get_ticket(db_session, seed):
    ticket = await _new_ticket(db_session, seed)
    from app.schemas import TicketUpdate
    from app.services.tickets import update_ticket as svc_update_ticket
    await svc_update_ticket(
        db_session,
        actor=seed.admin,
        ticket_id=ticket.key,
        payload=TicketUpdate(
            technical_depth="x" * 2000,
            impact_analysis="y" * 2000,
        ),
    )

    slice_result = await _dispatch_tool(
        "get_ticket_slice",
        {"id": ticket.key, "include": ["acceptance_criteria"]},
        seed.admin,
        db_session,
    )
    full_result = await _dispatch_tool(
        "get_ticket",
        {"id": ticket.key},
        seed.admin,
        db_session,
    )

    # Skipping technical_depth + impact_analysis saves ~4K chars
    assert _size(slice_result) < 500
    assert _size(full_result) > _size(slice_result) * 8
