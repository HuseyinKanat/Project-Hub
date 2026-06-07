"""Tests for PH-162 G13 — bearer alt-auth on /git/refresh + rotate-refresh-secret endpoint.

AC coverage:
  B1  POST /git/refresh — admin Bearer (no X-Git-Refresh-Token) → 202 queued/coalesced
  B2  POST /git/refresh — non-admin Bearer, secret configured, no matching header → 403
  B3  POST /git/refresh — shared-secret path still works (regression)
  B4  POST /repository/rotate-refresh-secret — admin → 200 plaintext + DB updated + masked on GET
  B5  POST /repository/rotate-refresh-secret — non-admin → 403
  B6  POST /git/refresh after rotate — old secret 401, new secret 202
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Repository, Workflow
from app.db.session import get_db_session
from app.git.refresh import RefreshRegistry
from app.main import app
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECRET = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"  # 48-hex
OLD_SECRET = "0" * 48
VALID_SECRET_HEADERS = {"X-Git-Refresh-Token": SECRET}
# Fake bearer token value — we patch _resolve_actor_from_bearer so the raw
# value doesn't matter; header presence is what the endpoint checks.
FAKE_BEARER_TOKEN = "fake-bearer-token-for-testing"
BEARER_HEADERS = {"Authorization": f"Bearer {FAKE_BEARER_TOKEN}"}


# ---------------------------------------------------------------------------
# In-memory DB fixtures
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
    """Board 'RS' with admin + member actors, refresh_secret set, repo attached."""
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
    mem_session.add_all([admin, member])
    await mem_session.flush()

    roles_with_secret: dict[str, Any] = dict(DEFAULT_WEB_ROLES)
    roles_with_secret["refresh_secret"] = SECRET

    board = Board(
        key="RS",
        name="Repo Settings Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=roles_with_secret,
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
    await mem_session.flush()

    repo = Repository(
        board_id=board.id,
        slug="rs",
        name="rs",
        is_primary=True,
        provider="local",
        local_path="/repos/test/rs",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.commit()

    # Reload with relationships.
    refreshed_board = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repositories))
        )
    ).scalar_one()

    return {
        "board": refreshed_board,
        "admin": admin,
        "member": member,
        "repo": repo,
        "session": mem_session,
    }


# ---------------------------------------------------------------------------
# Client factory — injects session; current_actor bypassed for endpoints that
# need it (rotate-secret uses _require_board_admin which needs current_actor).
# ---------------------------------------------------------------------------


def make_client_as(actor: Actor, session: AsyncSession) -> TestClient:
    """Create a TestClient with current_actor and get_db_session overridden."""

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def make_anon_client(session: AsyncSession) -> TestClient:
    """Create a TestClient with get_db_session overridden but no current_actor override.

    Used for the /git/refresh endpoint which resolves its own actor from the
    Authorization header.
    """

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = _fake_session
    # Ensure no leftover current_actor override from previous test.
    app.dependency_overrides.pop(current_actor, None)
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# AC-B1: Bearer admin (no X-Git-Refresh-Token) → 202 queued
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_bearer_admin_no_secret_header_queued(
    seeded: dict[str, Any],
) -> None:
    """AC-B1: Admin Bearer without X-Git-Refresh-Token → 202 queued."""
    admin: Actor = seeded["admin"]
    fresh_registry = RefreshRegistry()

    # Patch _resolve_actor_from_bearer to return the admin actor so we skip
    # bcrypt overhead in unit tests.
    with (
        patch("app.api.repositories.registry", fresh_registry),
        patch("app.api.repositories.run_in_background") as mock_bg,
        patch(
            "app.api.repositories._resolve_actor_from_bearer",
            new=AsyncMock(return_value=admin),
        ),
    ):
        client = make_anon_client(seeded["session"])
        try:
            resp = client.post("/api/boards/RS/git/refresh", headers=BEARER_HEADERS)
            assert resp.status_code == 202, resp.text
            data = resp.json()
            assert data["ok"] is True
            assert data["status"] in ("queued", "coalesced")
            mock_bg.assert_called_once()
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC-B2: Non-admin Bearer, secret configured, no matching header → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_non_admin_bearer_rejected_403(
    seeded: dict[str, Any],
) -> None:
    """AC-B2: Non-admin actor via Bearer → 403; must NOT fall through to secret path."""
    member: Actor = seeded["member"]

    with (
        patch(
            "app.api.repositories._resolve_actor_from_bearer",
            new=AsyncMock(return_value=member),
        ),
        patch("app.api.repositories.run_in_background") as mock_bg,
    ):
        client = make_anon_client(seeded["session"])
        try:
            # Send bearer but NO X-Git-Refresh-Token.  If fall-through happened,
            # the secret path would 401 (mismatch). We expect 403.
            resp = client.post("/api/boards/RS/git/refresh", headers=BEARER_HEADERS)
            assert resp.status_code == 403, resp.text
            data = resp.json()
            assert data["error"] == "permission_denied"
            # Background sync must NOT be triggered.
            mock_bg.assert_not_called()
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC-B3: Shared-secret path still works (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_shared_secret_still_works(seeded: dict[str, Any]) -> None:
    """AC-B3: Existing X-Git-Refresh-Token path unaffected by G13 changes."""
    fresh_registry = RefreshRegistry()

    with (
        patch("app.api.repositories.registry", fresh_registry),
        patch("app.api.repositories.run_in_background") as mock_bg,
    ):
        # No Authorization header — falls straight through to shared-secret path.
        client = make_anon_client(seeded["session"])
        try:
            resp = client.post(
                "/api/boards/RS/git/refresh",
                headers=VALID_SECRET_HEADERS,
            )
            assert resp.status_code == 202, resp.text
            data = resp.json()
            assert data["ok"] is True
            assert data["status"] in ("queued", "coalesced")
            mock_bg.assert_called_once()
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC-B4: Rotate secret — admin → plaintext returned, DB updated, GET masks it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_admin_returns_plaintext_and_masked_on_get(
    seeded: dict[str, Any],
) -> None:
    """AC-B4: Admin rotate → 200 with 48-hex plaintext; subsequent GET shows '*****'."""
    admin: Actor = seeded["admin"]
    session: AsyncSession = seeded["session"]

    client = make_client_as(admin, session)
    try:
        resp = client.post("/api/boards/RS/repository/rotate-refresh-secret")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Plaintext: exactly 48 hex characters.
        assert "refresh_secret" in data
        new_secret: str = data["refresh_secret"]
        assert len(new_secret) == 48
        assert all(c in "0123456789abcdef" for c in new_secret)

        # Hook install command contains the board key and the new secret.
        assert "hook_install_command" in data
        assert "RS" in data["hook_install_command"]
        assert new_secret in data["hook_install_command"]

        # Subsequent GET /boards/RS must show the secret masked.
        session.expire_all()
        get_resp = client.get("/api/boards/RS")
        assert get_resp.status_code == 200, get_resp.text
        board_data = get_resp.json()
        assert board_data["roles"].get("refresh_secret") == "*****"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC-B5: Rotate secret — non-admin → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_non_admin_403(seeded: dict[str, Any]) -> None:
    """AC-B5: Non-admin member calling rotate-refresh-secret → 403."""
    member: Actor = seeded["member"]
    client = make_client_as(member, seeded["session"])
    try:
        resp = client.post("/api/boards/RS/repository/rotate-refresh-secret")
        assert resp.status_code == 403, resp.text
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC-B6: After rotate — old secret 401, new secret 202
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_after_rotate_old_secret_fails_new_succeeds(
    seeded: dict[str, Any],
) -> None:
    """AC-B6: After rotate, old secret → 401; new secret (from rotate) → 202."""
    admin: Actor = seeded["admin"]
    session: AsyncSession = seeded["session"]

    # Step 1: rotate to get new secret.
    rotate_client = make_client_as(admin, session)
    try:
        rotate_resp = rotate_client.post(
            "/api/boards/RS/repository/rotate-refresh-secret"
        )
        assert rotate_resp.status_code == 200, rotate_resp.text
        new_secret: str = rotate_resp.json()["refresh_secret"]
    finally:
        clear_overrides()

    # Step 2: old secret must now be rejected with 401.
    fresh_registry_old = RefreshRegistry()
    with (
        patch("app.api.repositories.registry", fresh_registry_old),
        patch("app.api.repositories.run_in_background"),
    ):
        old_client = make_anon_client(session)
        try:
            resp_old = old_client.post(
                "/api/boards/RS/git/refresh",
                headers={"X-Git-Refresh-Token": SECRET},  # original secret
            )
            assert resp_old.status_code == 401, resp_old.text
        finally:
            clear_overrides()

    # Step 3: new secret must succeed with 202.
    fresh_registry_new = RefreshRegistry()
    with (
        patch("app.api.repositories.registry", fresh_registry_new),
        patch("app.api.repositories.run_in_background") as mock_bg,
    ):
        new_client = make_anon_client(session)
        try:
            resp_new = new_client.post(
                "/api/boards/RS/git/refresh",
                headers={"X-Git-Refresh-Token": new_secret},
            )
            assert resp_new.status_code == 202, resp_new.text
            data = resp_new.json()
            assert data["ok"] is True
            mock_bg.assert_called_once()
        finally:
            clear_overrides()
