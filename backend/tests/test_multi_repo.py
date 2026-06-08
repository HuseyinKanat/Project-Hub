"""PH-221 — multi-repo (1:N per board) service + API tests.

Covers the AC:
  - GET /repositories lists all, exactly one is_primary
  - POST /repositories adds (first auto-primary, second non-primary; slug dedupe)
  - DELETE /repositories/{selector} removes; auto-promote oldest if primary deleted
  - POST /repositories/{selector}/set-primary flips primary, one-primary invariant
  - /git/* repo selector defaults to primary; explicit slug/id selects; 404 unmatched
  - singular PUT/DELETE /repository aliases still operate on the primary
  - resolve_repository contract (None→primary 409 if none; unmatched→404)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.core.exceptions import NotFound, RepoNotConfigured
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, GitBranch, Workflow
from app.db.session import get_db_session
from app.main import app
from app.schemas import RepositoryCreate, RepositoryUpsert
from app.services import repositories as repo_svc
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(mem_session: AsyncSession) -> dict[str, Any]:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    member = Actor(kind="human", display_name="Member", token_hash="x", is_active=True)
    outsider = Actor(kind="human", display_name="Outsider", token_hash="x", is_active=True)
    mem_session.add_all([admin, member, outsider])
    await mem_session.flush()

    board = Board(
        key="MR",
        name="Multi Repo Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()
    mem_session.add_all(
        [
            BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board.id, actor_id=member.id, role="backend_dev"),
        ]
    )
    await mem_session.commit()

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repositories))
        )
    ).scalar_one()
    return {
        "board": refreshed,
        "admin": admin,
        "member": member,
        "outsider": outsider,
        "session": mem_session,
    }


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


def _create_payload(local_path: str, **kw: Any) -> dict[str, Any]:
    return {"provider": "local", "default_branch": "main", "local_path": local_path, **kw}


# ===========================================================================
# Service-layer tests (direct, dialect-portable)
# ===========================================================================


@pytest.mark.asyncio
async def test_add_first_repo_is_primary(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    repo = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/api")
    )
    await session.commit()
    assert repo.is_primary is True
    assert repo.slug == "api"
    assert repo.name == "api"


@pytest.mark.asyncio
async def test_add_second_repo_not_primary(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/api"))
    second = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/web")
    )
    await session.commit()
    assert second.is_primary is False
    repos = await repo_svc.list_repositories(session, board)
    assert [r.is_primary for r in repos].count(True) == 1


@pytest.mark.asyncio
async def test_add_repo_slug_dedupe(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    # Two repos whose basenames slugify to the same value → second gets -2 suffix.
    r1 = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/foo/api")
    )
    r2 = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/bar/api")
    )
    await session.commit()
    assert r1.slug == "api"
    assert r2.slug == "api-2"


@pytest.mark.asyncio
async def test_set_primary_flips_invariant(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    r1 = await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/api"))
    r2 = await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/web"))
    await session.commit()

    new_primary = await repo_svc.set_primary(session, board, r2.slug)
    await session.commit()
    assert new_primary.id == r2.id
    assert new_primary.is_primary is True

    repos = await repo_svc.list_repositories(session, board)
    primaries = [r for r in repos if r.is_primary]
    assert len(primaries) == 1
    assert primaries[0].id == r2.id
    # former primary demoted
    await session.refresh(r1)
    assert r1.is_primary is False


@pytest.mark.asyncio
async def test_remove_primary_auto_promotes_oldest(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    r1 = await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/api"))
    r2 = await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/web"))
    await session.commit()
    assert r1.is_primary is True

    # Remove the primary (r1); r2 (the only remaining) must be auto-promoted.
    await repo_svc.remove_repository(session, board, str(r1.id))
    await session.commit()
    repos = await repo_svc.list_repositories(session, board)
    assert len(repos) == 1
    assert repos[0].id == r2.id
    assert repos[0].is_primary is True


@pytest.mark.asyncio
async def test_resolve_repository_contract(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]

    # No repo at all + None selector → RepoNotConfigured (409 semantics).
    with pytest.raises(RepoNotConfigured):
        await repo_svc.resolve_repository(session, board, None)

    r1 = await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/api"))
    await session.commit()

    # None → primary
    assert (await repo_svc.resolve_repository(session, board, None)).id == r1.id
    # by slug
    assert (await repo_svc.resolve_repository(session, board, "api")).id == r1.id
    # by id
    assert (await repo_svc.resolve_repository(session, board, str(r1.id))).id == r1.id
    # unmatched selector → NotFound (404 semantics)
    with pytest.raises(NotFound):
        await repo_svc.resolve_repository(session, board, "nope")


@pytest.mark.asyncio
async def test_upsert_alias_updates_primary(seeded: dict[str, Any]) -> None:
    """Singular PUT alias upserts the primary in place (stable id)."""
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    r1 = await repo_svc.upsert_repository(
        session, board, RepositoryUpsert(local_path="/repos/api")
    )
    await session.commit()
    assert r1.is_primary is True

    r2 = await repo_svc.upsert_repository(
        session, board, RepositoryUpsert(local_path="/repos/api", default_branch="develop")
    )
    await session.commit()
    assert r2.id == r1.id  # same row
    assert r2.default_branch == "develop"


# ===========================================================================
# API-layer tests
# ===========================================================================


@pytest.mark.asyncio
async def test_api_list_and_add(seeded: dict[str, Any]) -> None:
    admin = make_client(seeded["admin"], seeded["session"])
    try:
        r1 = admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/api"))
        assert r1.status_code == 201, r1.text
        assert r1.json()["is_primary"] is True
        assert r1.json()["slug"] == "api"

        r2 = admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/web"))
        assert r2.status_code == 201
        assert r2.json()["is_primary"] is False

        lst = admin.get("/api/boards/MR/repositories")
        assert lst.status_code == 200
        repos = lst.json()["repositories"]
        assert len(repos) == 2
        assert [r["is_primary"] for r in repos].count(True) == 1
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_api_set_primary(seeded: dict[str, Any]) -> None:
    admin = make_client(seeded["admin"], seeded["session"])
    try:
        admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/api"))
        admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/web"))

        resp = admin.post("/api/boards/MR/repositories/web/set-primary")
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == "web"
        assert resp.json()["is_primary"] is True

        lst = admin.get("/api/boards/MR/repositories").json()["repositories"]
        assert [r["is_primary"] for r in lst].count(True) == 1
        primary = next(r for r in lst if r["is_primary"])
        assert primary["slug"] == "web"
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_api_remove_promotes(seeded: dict[str, Any]) -> None:
    admin = make_client(seeded["admin"], seeded["session"])
    try:
        admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/api"))
        admin.post("/api/boards/MR/repositories", json=_create_payload("/repos/web"))

        # Delete primary 'api' by slug → 'web' auto-promoted.
        d = admin.delete("/api/boards/MR/repositories/api")
        assert d.status_code == 204
        lst = admin.get("/api/boards/MR/repositories").json()["repositories"]
        assert len(lst) == 1
        assert lst[0]["slug"] == "web"
        assert lst[0]["is_primary"] is True

        # Unmatched selector → 404.
        assert admin.delete("/api/boards/MR/repositories/ghost").status_code == 404
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_api_add_non_admin_403(seeded: dict[str, Any]) -> None:
    member = make_client(seeded["member"], seeded["session"])
    try:
        resp = member.post("/api/boards/MR/repositories", json=_create_payload("/repos/api"))
        assert resp.status_code == 403
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_singular_put_delete_alias_still_works(seeded: dict[str, Any]) -> None:
    """The old PUT/DELETE /repository routes still operate on the primary."""
    admin = make_client(seeded["admin"], seeded["session"])
    try:
        put = admin.put(
            "/api/boards/MR/repository",
            json={"provider": "local", "default_branch": "main", "local_path": "/repos/legacy"},
        )
        assert put.status_code == 200, put.text
        assert put.json()["is_primary"] is True

        # GET status (no repo selector) defaults to primary.
        st = admin.get("/api/boards/MR/git/status").json()
        assert st["connected"] is True
        assert st["repository"]["slug"] == "legacy"

        # DELETE singular removes the primary.
        assert admin.delete("/api/boards/MR/repository").status_code == 204
        st2 = admin.get("/api/boards/MR/git/status").json()
        assert st2["connected"] is False
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_git_branches_repo_selector_defaults_to_primary(seeded: dict[str, Any]) -> None:
    """/git/branches: omitted repo → primary's cache; explicit slug → that repo; 404 unmatched."""
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]

    primary = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/api")
    )
    secondary = await repo_svc.add_repository(
        session, board, RepositoryCreate(local_path="/repos/web")
    )
    # Seed a distinct branch in each repo's cache.
    session.add(
        GitBranch(
            id=uuid.uuid4(),
            repo_id=primary.id,
            name="main",
            head_sha="a" * 40,
            is_default=True,
        )
    )
    session.add(
        GitBranch(
            id=uuid.uuid4(),
            repo_id=secondary.id,
            name="feature-web",
            head_sha="b" * 40,
            is_default=True,
        )
    )
    await session.commit()

    admin = make_client(seeded["admin"], session)
    try:
        # Default → primary (api): main
        default = admin.get("/api/boards/MR/git/branches").json()
        assert {b["name"] for b in default["branches"]} == {"main"}

        # Explicit slug → secondary (web): feature-web
        by_slug = admin.get("/api/boards/MR/git/branches?repo=web").json()
        assert {b["name"] for b in by_slug["branches"]} == {"feature-web"}

        # Explicit id → secondary
        by_id = admin.get(f"/api/boards/MR/git/branches?repo={secondary.id}").json()
        assert {b["name"] for b in by_id["branches"]} == {"feature-web"}

        # Unmatched selector → 404
        assert admin.get("/api/boards/MR/git/branches?repo=ghost").status_code == 404
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_git_status_explicit_selector(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    board: Board = seeded["board"]
    await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/api"))
    await repo_svc.add_repository(session, board, RepositoryCreate(local_path="/repos/web"))
    await session.commit()

    member = make_client(seeded["member"], session)
    try:
        st = member.get("/api/boards/MR/git/status?repo=web").json()
        assert st["connected"] is True
        assert st["repository"]["slug"] == "web"
        # unmatched explicit selector → 404
        assert member.get("/api/boards/MR/git/status?repo=ghost").status_code == 404
    finally:
        clear_overrides()
