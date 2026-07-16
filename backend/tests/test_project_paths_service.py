"""PH-322: per-owner project-path service — upsert, remove, guards, list.

Covers the write/read core: owner resolved from the token (two sources), upsert
single-row, empty=remove, 422 owner_unresolved (nothing written), 403 board-member
gate (DISTINCT from 422), >255 → 422, shared owner (human + agent fleet), and the
distinct-owner list shape.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import func, select

from app.core.exceptions import OwnerUnresolved, PermissionDenied, ProfileFieldInvalid
from app.db.models import Actor, BoardMembership, ProjectPath
from app.services.owners import set_owner_slug
from app.services.project_paths import (
    get_project_path,
    list_project_paths,
    set_project_path,
)


async def _add_actor(session, display_name, kind="agent", role="backend_dev", board=None):
    """Create an actor and (when board given) a membership; return the refreshed actor."""
    actor = Actor(kind=kind, display_name=display_name, token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()
    if board is not None:
        session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
    await session.commit()
    from sqlalchemy.orm import selectinload

    return (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


async def _path_count(session, board) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(ProjectPath).where(ProjectPath.board_id == board.id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    owner, row = await set_project_path(db_session, seed.admin, seed.board, "/Users/alice/ph")
    assert owner == "alice"
    assert row is not None and row.local_path == "/Users/alice/ph"

    got_owner, got_row = await get_project_path(db_session, seed.admin, seed.board)
    assert got_owner == "alice"
    assert got_row is not None and got_row.local_path == "/Users/alice/ph"


@pytest.mark.asyncio
async def test_second_set_is_upsert_single_row(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    _, first = await set_project_path(db_session, seed.admin, seed.board, "/a")
    _, second = await set_project_path(db_session, seed.admin, seed.board, "/b")

    assert await _path_count(db_session, seed.board) == 1  # no duplicate
    assert first.id == second.id  # SAME row updated
    assert second.local_path == "/b"
    assert second.updated_at >= second.created_at  # updated_at advances


@pytest.mark.asyncio
async def test_concurrent_first_write_integrityerror_becomes_update(
    db_session, seed, monkeypatch
) -> None:
    """F3 (PH-322): a concurrent first-write that races past the existing-row read
    hits uq_project_path_owner_board on commit; the handler rolls back, re-fetches the
    sibling row and applies our value (upsert stays last-writer-wins) instead of a raw
    500 — mirroring set_owner_slug's IntegrityError handling.
    """
    import app.services.project_paths as pp

    await set_owner_slug(db_session, seed.admin, "alice")

    # Simulate the race: a SIBLING writer already committed the (alice, board) row,
    # but THIS call's existing-row read returns None (as if it read before that commit
    # landed), forcing the INSERT path straight into the unique-constraint violation.
    db_session.add(
        ProjectPath(owner_slug="alice", board_id=seed.board.id, local_path="/first")
    )
    await db_session.commit()

    real_get_row = pp._get_row
    calls = {"n": 0}

    async def fake_get_row(session, owner_slug, board_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # pre-commit read misses the racing row → take INSERT branch
        return await real_get_row(session, owner_slug, board_id)  # re-fetch sees it

    monkeypatch.setattr(pp, "_get_row", fake_get_row)
    owner, row = await pp.set_project_path(db_session, seed.admin, seed.board, "/second")

    assert owner == "alice"
    assert row is not None and row.local_path == "/second"  # our value wins
    assert await _path_count(db_session, seed.board) == 1  # still ONE row, no duplicate


@pytest.mark.asyncio
async def test_empty_path_removes_row(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    await set_project_path(db_session, seed.admin, seed.board, "/a")
    assert await _path_count(db_session, seed.board) == 1

    owner, row = await set_project_path(db_session, seed.admin, seed.board, "")
    assert owner == "alice" and row is None
    assert await _path_count(db_session, seed.board) == 0

    # Reads back null.
    _, got = await get_project_path(db_session, seed.admin, seed.board)
    assert got is None


@pytest.mark.asyncio
async def test_owner_unresolved_422_writes_nothing(db_session, seed) -> None:
    """A human with owner_slug=NULL → 422, and NO row is written."""
    # seed.pm is a human member with owner_slug=None.
    with pytest.raises(OwnerUnresolved):
        await set_project_path(db_session, seed.pm, seed.board, "/nope")
    assert await _path_count(db_session, seed.board) == 0

    with pytest.raises(OwnerUnresolved):
        await get_project_path(db_session, seed.pm, seed.board)


@pytest.mark.asyncio
async def test_non_member_403_distinct_from_owner_unresolved(db_session, seed) -> None:
    """An agent WITH a resolved owner but NO membership → 403 (not 422)."""
    outsider = await _add_actor(
        db_session, "jarwis-qa@alice", kind="agent", board=None
    )  # resolves owner=alice, but not a board member
    with pytest.raises(PermissionDenied):
        await get_project_path(db_session, outsider, seed.board)
    with pytest.raises(PermissionDenied):
        await set_project_path(db_session, outsider, seed.board, "/x")
    with pytest.raises(PermissionDenied):
        await list_project_paths(db_session, outsider, seed.board)


@pytest.mark.asyncio
async def test_local_path_over_255_is_422(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    with pytest.raises(ProfileFieldInvalid) as exc:
        await set_project_path(db_session, seed.admin, seed.board, "x" * 256)
    assert exc.value.field == "local_path"
    assert await _path_count(db_session, seed.board) == 0  # nothing written


@pytest.mark.asyncio
async def test_shared_owner_human_and_agent_fleet(db_session, seed) -> None:
    """A human sets a path; an agent jarwis-<role>@<owner> reads the SAME row."""
    await set_owner_slug(db_session, seed.admin, "alice")
    await set_project_path(db_session, seed.admin, seed.board, "/Users/alice/ph")

    agent = await _add_actor(
        db_session, "jarwis-backend@alice", kind="agent", board=seed.board
    )
    owner, row = await get_project_path(db_session, agent, seed.board)
    assert owner == "alice"
    assert row is not None and row.local_path == "/Users/alice/ph"


@pytest.mark.asyncio
async def test_list_returns_distinct_member_owners(db_session, seed) -> None:
    # alice (admin, with a path) + bob (pm, no path); backend agent has no @ → excluded.
    await set_owner_slug(db_session, seed.admin, "alice")
    await set_owner_slug(db_session, seed.pm, "bob")
    await set_project_path(db_session, seed.admin, seed.board, "/a")
    # A second alice-fleet agent must NOT create a duplicate alice entry.
    await _add_actor(db_session, "jarwis-frontend@alice", kind="agent", board=seed.board)

    entries = await list_project_paths(db_session, seed.admin, seed.board)
    as_dict = {owner: (row.local_path if row else None) for owner, row in entries}
    assert as_dict == {"alice": "/a", "bob": None}  # sorted, distinct, backend excluded


@pytest.mark.asyncio
async def test_set_emits_audit_log(db_session, seed, caplog) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    with caplog.at_level(logging.INFO):
        await set_project_path(db_session, seed.admin, seed.board, "/a")
    assert "project_path_set" in caplog.text
    assert "owner=alice" in caplog.text
