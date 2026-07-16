"""PH-322: REST profile + project-paths + members enrichment.

GET/PUT /api/profile (owner_slug; human-only; regex 422; dup 409) and GET/PUT
/api/profile/project-paths (upsert, delete-on-empty, role-token parity, cross-owner
403), plus GET /api/boards/{id}/members carrying owner + local_path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.models import Actor, BoardMembership
from app.db.session import get_db_session
from app.main import app
from app.services.owners import set_owner_slug
from app.services.project_paths import set_project_path


def make_client(actor: Actor, session: AsyncSession) -> TestClient:
    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


async def _add_agent_member(session, display_name, board, role="backend_dev") -> Actor:
    actor = Actor(kind="agent", display_name=display_name, token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
    await session.commit()
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# GET/PUT /api/profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_initial_null_owner(db_session, seed) -> None:
    client = make_client(seed.admin, db_session)
    try:
        resp = client.get("/api/profile")
    finally:
        clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_slug"] is None and body["owner"] is None
    assert body["kind"] == "human"


@pytest.mark.asyncio
async def test_put_profile_sets_owner_slug(db_session, seed) -> None:
    client = make_client(seed.admin, db_session)
    try:
        resp = client.put("/api/profile", json={"owner_slug": "alice"})
    finally:
        clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_slug"] == "alice" and body["owner"] == "alice"


@pytest.mark.asyncio
async def test_put_profile_agent_forbidden(db_session, seed) -> None:
    client = make_client(seed.backend, db_session)  # agent token
    try:
        resp = client.put("/api/profile", json={"owner_slug": "alice"})
    finally:
        clear_overrides()
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_profile_bad_slug_422(db_session, seed) -> None:
    client = make_client(seed.admin, db_session)
    try:
        resp = client.put("/api/profile", json={"owner_slug": "-nope"})
    finally:
        clear_overrides()
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "profile_field_invalid"
    assert body["field"] == "owner_slug"


@pytest.mark.asyncio
async def test_put_profile_duplicate_slug_409(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    client = make_client(seed.pm, db_session)
    try:
        resp = client.put("/api/profile", json={"owner_slug": "alice"})
    finally:
        clear_overrides()
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET/PUT /api/profile/project-paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_path_null_before_set(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    client = make_client(seed.admin, db_session)
    try:
        resp = client.get("/api/profile/project-paths", params={"board": "PH"})
    finally:
        clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == "alice" and body["local_path"] is None


@pytest.mark.asyncio
async def test_put_project_path_upsert_then_remove(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    client = make_client(seed.admin, db_session)
    try:
        up = client.put("/api/profile/project-paths", json={"board": "PH", "local_path": "/x"})
        assert up.status_code == 200
        assert up.json()["local_path"] == "/x"
        # Empty removes.
        rm = client.put("/api/profile/project-paths", json={"board": "PH", "local_path": ""})
        assert rm.status_code == 200
        assert rm.json()["local_path"] is None
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_put_project_path_cross_owner_forbidden(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    client = make_client(seed.admin, db_session)
    try:
        resp = client.put(
            "/api/profile/project-paths",
            json={"board": "PH", "local_path": "/x", "owner": "bob"},
        )
    finally:
        clear_overrides()
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_project_path_role_token_parity(db_session, seed) -> None:
    """A Bearer ROLE token (jarwis-*@owner) writes under the @suffix owner — no
    human owner_slug needed (jarwis-init curl parity)."""
    agent = await _add_agent_member(db_session, "jarwis-backend@alice", seed.board)
    client = make_client(agent, db_session)
    try:
        resp = client.put("/api/profile/project-paths", json={"board": "PH", "local_path": "/z"})
    finally:
        clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == "alice" and body["local_path"] == "/z"


# ---------------------------------------------------------------------------
# Members enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_list_carries_owner_and_local_path(db_session, seed) -> None:
    await set_owner_slug(db_session, seed.admin, "alice")
    await set_project_path(db_session, seed.admin, seed.board, "/Users/alice/ph")

    client = make_client(seed.admin, db_session)
    try:
        resp = client.get(f"/api/boards/{seed.board.id}/members")
    finally:
        clear_overrides()
    assert resp.status_code == 200
    members = resp.json()["members"]
    by_display = {m["actor"]["display_name"]: m for m in members}
    # admin (owner alice) carries the enriched path; pm (no owner) carries nulls.
    assert by_display["Admin"]["owner"] == "alice"
    assert by_display["Admin"]["local_path"] == "/Users/alice/ph"
    assert by_display["PM"]["owner"] is None
    assert by_display["PM"]["local_path"] is None
