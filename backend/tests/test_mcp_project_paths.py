"""PH-322: MCP project-path tools (get/set/list) via _dispatch_tool.

The agent surface. Proves set is SELF-only (no owner param — an injected 'owner'
is ignored, the write goes under the caller's token owner), the get/list shapes,
owner-unresolved (422 domain error), and the member gate.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import OwnerUnresolved, PermissionDenied
from app.db.models import Actor, BoardMembership
from app.mcp.server import _dispatch_tool
from app.services.owners import set_owner_slug


async def _add_member(session, display_name, board, kind="agent", role="backend_dev"):
    actor = Actor(kind=kind, display_name=display_name, token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
    await session.commit()
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_mcp_set_then_get(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    set_res = await _dispatch_tool(
        "set_my_project_path",
        {"board": "PH", "local_path": "/Users/alice/ph"},
        seed.admin,
        db_session,
    )
    assert set_res == {
        "board": "PH",
        "owner": "alice",
        "local_path": "/Users/alice/ph",
        "updated_at": set_res["updated_at"],
    }
    assert set_res["updated_at"] is not None

    get_res = await _dispatch_tool(
        "get_my_project_path", {"board": "PH"}, seed.admin, db_session
    )
    assert get_res["owner"] == "alice"
    assert get_res["local_path"] == "/Users/alice/ph"


@pytest.mark.asyncio
async def test_mcp_set_is_self_only_ignores_owner_param(db_session, seed) -> None:
    """set_my_project_path has NO owner param — an injected 'owner' cannot redirect
    the write; it always lands under the caller's own owner."""
    await set_owner_slug(db_session, seed.admin, "alice")
    res = await _dispatch_tool(
        "set_my_project_path",
        {"board": "PH", "local_path": "/x", "owner": "bob"},  # 'owner' must be ignored
        seed.admin,
        db_session,
    )
    assert res["owner"] == "alice"  # caller's own, NOT "bob"


@pytest.mark.asyncio
async def test_mcp_list_distinct_owners(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    await _dispatch_tool(
        "set_my_project_path", {"board": "PH", "local_path": "/a"}, seed.admin, db_session
    )
    res = await _dispatch_tool("list_project_paths", {"board": "PH"}, seed.admin, db_session)
    assert res["board"] == "PH"
    owners = {p["owner"]: p["local_path"] for p in res["paths"]}
    assert owners.get("alice") == "/a"


@pytest.mark.asyncio
async def test_mcp_owner_unresolved_raises(db_session, seed) -> None:
    """seed.pm is a human member with no owner_slug → 422 domain error via MCP."""
    with pytest.raises(OwnerUnresolved):
        await _dispatch_tool(
            "set_my_project_path", {"board": "PH", "local_path": "/x"}, seed.pm, db_session
        )


@pytest.mark.asyncio
async def test_mcp_non_member_forbidden(db_session, seed) -> None:
    # An agent with a resolved owner but no membership on PH.
    outsider = Actor(
        kind="agent", display_name="jarwis-qa@alice", token_hash="x", is_active=True
    )
    db_session.add(outsider)
    await db_session.commit()
    outsider = (
        await db_session.execute(
            select(Actor).where(Actor.id == outsider.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    with pytest.raises(PermissionDenied):
        await _dispatch_tool("get_my_project_path", {"board": "PH"}, outsider, db_session)


@pytest.mark.asyncio
async def test_mcp_set_emits_audit_log(db_session, seed, caplog) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    with caplog.at_level(logging.INFO):
        await _dispatch_tool(
            "set_my_project_path", {"board": "PH", "local_path": "/a"}, seed.admin, db_session
        )
    assert "project_path_set" in caplog.text
