"""PH-254 — per-repo SonarQube setup endpoint (store/override project key).

Two network-free layers (mirrors test_sonarqube_setup.py / test_sonarqube_multirepo.py):

  Service (services/sonarqube.setup_repo_project) — a real in-memory sqlite session
  exercises persist + idempotency + intra-board conflict (decision b) + blank-key
  validation + the derived-default vs explicit-override resolution, for real.

  Endpoint (api/boards.api_repo_sonarqube_setup) — a FastAPI TestClient with
  current_actor + get_db_session DI overrides. Covers: 200 persists the derived
  sibling key (<base>-<slug>), 200 custom override, idempotency, 404 unknown repo,
  404 unknown board, 422 blank key, 409 intra-board conflict (decision b), 403
  non-admin, 200 graceful when sonar disabled, cross-board reuse allowed, and the
  SECRET-FREE + RepoHealth-shape invariants.

Persist-only (decision a) — NO live SonarQube poll on the write path, so no httpx
patching is needed here (the service never touches the network).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.exceptions import Conflict
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    Repository,
    SonarQubeMetric,
    Workflow,
)
from app.db.session import get_db_session
from app.main import app
from app.services import sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

_SECRET = "super-secret-token"


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


def _settings(*, enabled: bool = True, scan_url: str = "http://localhost:9000") -> MagicMock:
    settings = MagicMock()
    settings.sonarqube_enabled = enabled
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = scan_url
    settings.sonarqube_token = _SECRET
    settings.sonarqube_project_key_map = ""
    return settings


async def _seed_board(
    session: AsyncSession,
    *,
    key: str = "GXA",
    project_key: str | None = "GameX",
    sibling_slugs: tuple[str, ...] = ("gamexsdk",),
    member_role: str = "admin",
) -> tuple[Board, Actor]:
    """Seed a board + admin/member actor + a primary repo + N siblings.

    Returns the board re-fetched with the eager-loads get_board uses (repositories
    + sonarqube_metrics.repository), so the route + serializers are async-safe.
    """
    workflow = Workflow(
        name=f"wf-{key}-{uuid4().hex[:6]}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()

    actor = Actor(kind="human", display_name="A", token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()

    board = Board(
        key=key,
        name=key,
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=actor.id,
        sonarqube_project_key=project_key,
    )
    session.add(board)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=member_role))

    session.add(
        Repository(
            board_id=board.id,
            slug="gamexcore",
            name="GameX Core",
            is_primary=True,
            provider="local",
            default_branch="main",
            local_path="/Users/huseyinkanat/gamexcore",
        )
    )
    for slug in sibling_slugs:
        session.add(
            Repository(
                board_id=board.id,
                slug=slug,
                name=slug,
                is_primary=False,
                provider="local",
                default_branch="main",
                local_path=f"/Users/huseyinkanat/{slug}",
            )
        )
    await session.commit()
    return await _refetch(session, board.id), await _refetch_actor(session, actor.id)


async def _refetch(session: AsyncSession, board_id) -> Board:
    return (
        await session.execute(
            select(Board)
            .where(Board.id == board_id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.memberships),
                selectinload(Board.repositories),
                selectinload(Board.sonarqube_metrics).selectinload(
                    SonarQubeMetric.repository
                ),
            )
        )
    ).scalar_one()


async def _refetch_actor(session: AsyncSession, actor_id) -> Actor:
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor_id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


def _repo_by_slug(board: Board, slug: str) -> Repository:
    return next(r for r in board.repositories if r.slug == slug)


# ===========================================================================
# Service layer — real sqlite, no network (persist-only, decision a)
# ===========================================================================


async def test_service_derives_sibling_default_when_omitted(
    mem_session: AsyncSession,
) -> None:
    """No key supplied → derives + persists ``<base>-<slug>`` for a secondary repo."""
    board, _ = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    key = await sonarqube.setup_repo_project(mem_session, board, sibling, None)
    assert key == "GameX-gamexsdk"
    assert sibling.sonarqube_project_key == "GameX-gamexsdk"


async def test_service_primary_default_inherits_board_key(
    mem_session: AsyncSession,
) -> None:
    """No key on the PRIMARY repo → inherits the board key (decision: no rename)."""
    board, _ = await _seed_board(mem_session)
    primary = _repo_by_slug(board, "gamexcore")
    key = await sonarqube.setup_repo_project(mem_session, board, primary, None)
    assert key == "GameX"
    assert primary.sonarqube_project_key == "GameX"


async def test_service_explicit_key_overrides(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    key = await sonarqube.setup_repo_project(
        mem_session, board, sibling, "custom-key"
    )
    assert key == "custom-key"
    assert sibling.sonarqube_project_key == "custom-key"


async def test_service_idempotent_second_call_noop(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    k1 = await sonarqube.setup_repo_project(mem_session, board, sibling, "custom-key")
    k2 = await sonarqube.setup_repo_project(mem_session, board, sibling, "custom-key")
    assert k1 == k2 == "custom-key"
    assert sibling.sonarqube_project_key == "custom-key"


async def test_service_blank_key_raises_value_error(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    with pytest.raises(ValueError):
        await sonarqube.setup_repo_project(mem_session, board, sibling, "   ")
    # Nothing persisted on the rejected blank-key write.
    assert sibling.sonarqube_project_key is None


async def test_service_intra_board_conflict_raises(mem_session: AsyncSession) -> None:
    """Decision b: a key already held by ANOTHER repo of THIS board → Conflict."""
    board, _ = await _seed_board(mem_session, sibling_slugs=("gamexsdk", "gamexdemo"))
    sdk = _repo_by_slug(board, "gamexsdk")
    demo = _repo_by_slug(board, "gamexdemo")
    # Pin sdk to an explicit key, then try to reuse it on demo → conflict.
    await sonarqube.setup_repo_project(mem_session, board, sdk, "shared-key")
    with pytest.raises(Conflict) as exc:
        await sonarqube.setup_repo_project(mem_session, board, demo, "shared-key")
    assert exc.value.conflicting_repo == "gamexsdk"
    assert demo.sonarqube_project_key is None  # unchanged on conflict


async def test_service_conflict_against_sibling_derived_key(
    mem_session: AsyncSession,
) -> None:
    """Decision b depth: collide with a sibling's DERIVED (unset) key, not just stored."""
    board, _ = await _seed_board(mem_session, sibling_slugs=("gamexsdk", "gamexdemo"))
    demo = _repo_by_slug(board, "gamexdemo")
    # gamexsdk has NO stored key, but its derived key is "GameX-gamexsdk".
    with pytest.raises(Conflict) as exc:
        await sonarqube.setup_repo_project(mem_session, board, demo, "GameX-gamexsdk")
    assert exc.value.conflicting_repo == "gamexsdk"


async def test_service_reconfigure_same_repo_not_conflict(
    mem_session: AsyncSession,
) -> None:
    """Re-pointing the SAME repo to its own key is idempotent, never a self-conflict."""
    board, _ = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    await sonarqube.setup_repo_project(mem_session, board, sibling, "GameX-gamexsdk")
    # Same repo, same effective key again — must NOT raise Conflict.
    key = await sonarqube.setup_repo_project(
        mem_session, board, sibling, "GameX-gamexsdk"
    )
    assert key == "GameX-gamexsdk"


async def test_service_cross_board_reuse_allowed(mem_session: AsyncSession) -> None:
    """Decision b: the same key on a DIFFERENT board is allowed (no cross-board check)."""
    board_a, _ = await _seed_board(mem_session, key="AAA", project_key="alpha")
    board_b, _ = await _seed_board(mem_session, key="BBB", project_key="beta")
    sib_a = _repo_by_slug(board_a, "gamexsdk")
    sib_b = _repo_by_slug(board_b, "gamexsdk")
    await sonarqube.setup_repo_project(mem_session, board_a, sib_a, "shared-across")
    # Same key, different board → no Conflict.
    key = await sonarqube.setup_repo_project(
        mem_session, board_b, sib_b, "shared-across"
    )
    assert key == "shared-across"
    assert sib_b.sonarqube_project_key == "shared-across"


# ===========================================================================
# Endpoint layer — TestClient with DI overrides
# ===========================================================================


def _make_client(actor: Actor, session: AsyncSession) -> TestClient:
    from app.api.deps import current_actor

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def _clear() -> None:
    app.dependency_overrides.clear()


def _url(board: Board, selector: str) -> str:
    return f"/api/boards/{board.id}/repositories/{selector}/sonarqube/setup"


async def test_endpoint_persists_derived_sibling_key(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(_url(board, sibling.slug), json={})
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    # RepoHealth shape + the derived <base>-<slug> echoed back.
    assert data["repo_slug"] == "gamexsdk"
    assert data["project_key"] == "GameX-gamexsdk"
    assert data["is_primary"] is False
    # Unscanned → honest null metrics.
    assert data["quality_gate_status"] is None
    assert data["fetched_at"] is None
    assert _SECRET not in resp.text
    assert "sonarqube:9000" not in resp.text  # no compose-internal url


async def test_endpoint_custom_key_overrides(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(
                _url(board, sibling.slug), json={"project_key": "custom-key"}
            )
    finally:
        _clear()

    assert resp.status_code == 200
    assert resp.json()["project_key"] == "custom-key"


async def test_endpoint_resolves_by_repo_id(mem_session: AsyncSession) -> None:
    """Selector may be a row id (not just a slug) — resolve_repository id-first."""
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(_url(board, str(sibling.id)), json={})
    finally:
        _clear()

    assert resp.status_code == 200
    assert resp.json()["repo_slug"] == "gamexsdk"


async def test_endpoint_idempotent(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            r1 = client.post(_url(board, sibling.slug), json={})
            r2 = client.post(_url(board, sibling.slug), json={})
    finally:
        _clear()

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["project_key"] == r2.json()["project_key"] == "GameX-gamexsdk"
    assert r1.json() == r2.json()  # second response equals the first


async def test_endpoint_blank_key_is_422(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(
                _url(board, sibling.slug), json={"project_key": "   "}
            )
    finally:
        _clear()

    assert resp.status_code == 422  # never 500
    assert "blank" in resp.text.lower()


async def test_endpoint_intra_board_conflict_is_409(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(
        mem_session, sibling_slugs=("gamexsdk", "gamexdemo")
    )
    sdk = _repo_by_slug(board, "gamexsdk")
    demo = _repo_by_slug(board, "gamexdemo")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            client.post(_url(board, sdk.slug), json={"project_key": "shared-key"})
            resp = client.post(
                _url(board, demo.slug), json={"project_key": "shared-key"}
            )
    finally:
        _clear()

    assert resp.status_code == 409
    body = resp.json()
    # The conflicting repo is named (decision b — actionable message).
    assert "gamexsdk" in resp.text
    assert body.get("conflicting_repo") == "gamexsdk"


async def test_endpoint_unknown_repo_is_404(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(_url(board, "no-such-repo"), json={})
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_unknown_board_is_404(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(
                f"/api/boards/{uuid4()}/repositories/{sibling.slug}/sonarqube/setup",
                json={},
            )
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_non_admin_is_403(mem_session: AsyncSession) -> None:
    board, member = await _seed_board(mem_session, member_role="developer")
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(member, mem_session)
    try:
        with patch.object(sonarqube, "get_settings", return_value=_settings()):
            resp = client.post(_url(board, sibling.slug), json={})
    finally:
        _clear()
    assert resp.status_code == 403


async def test_endpoint_disabled_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session)
    sibling = _repo_by_slug(board, "gamexsdk")
    client = _make_client(admin, mem_session)
    try:
        with patch.object(
            sonarqube, "get_settings", return_value=_settings(enabled=False)
        ):
            resp = client.post(_url(board, sibling.slug), json={})
    finally:
        _clear()

    # Sonar off → key still persisted, never 500.
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_key"] == "GameX-gamexsdk"  # config allowed offline
    assert data["repo_slug"] == "gamexsdk"
    assert _SECRET not in resp.text
