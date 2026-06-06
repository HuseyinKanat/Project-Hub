"""PH-203 — tests for the SonarQube issue-search proxy.

Two layers, both network-free:

  Service (services/sonarqube.fetch_issues) — httpx is mocked via
  ``httpx.MockTransport`` (no network, no respx), reusing the ``_client_with``
  factory pattern from ``test_sonarqube_client.py``. Covers happy-path, the type
  filter, component-prefix stripping, empty results, and the unreachable/401/
  malformed graceful-degradation paths (status="unreachable", never raises).

  Endpoint (api/boards.api_board_sonarqube_issues) — a FastAPI ``TestClient`` with
  ``current_actor`` + ``get_db_session`` DI overrides (the membership-API pattern).
  Covers happy-path shape, host-facing ``dashboard_url`` + no-token, the
  ``page_size>100`` 422 boundary, unreachable graceful 200, no-projectKey /
  not-configured graceful 200, and the 404 for a genuinely missing board.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.db.session import get_db_session
from app.main import app
from app.services import sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# asyncio_mode="auto" (pyproject) auto-detects async tests — no module mark needed.


# ===========================================================================
# Service layer — httpx.MockTransport (mirrors test_sonarqube_client.py)
# ===========================================================================

# Capture the real class BEFORE any patching so the factory builds a genuine client.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_with(handler: Any) -> Any:
    """Factory that ignores ctor kwargs and yields a MockTransport-backed client."""

    def _factory(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(
            base_url="http://sonarqube:9000",
            transport=httpx.MockTransport(handler),
        )

    return _factory


_ISSUES_PAYLOAD = {
    "total": 2,
    "p": 1,
    "ps": 50,
    "issues": [
        {
            "key": "AY-issue-1",
            "rule": "python:S1481",
            "severity": "MINOR",
            "type": "CODE_SMELL",
            "component": "project-hub:backend/app/services/sonarqube.py",
            "line": 42,
            "message": "Remove this unused local variable.",
            "hash": "abc123",
        },
        {
            "key": "AY-issue-2",
            "rule": "python:S112",
            "severity": "MAJOR",
            "type": "BUG",
            # project-level component (no ":") — must pass through unchanged.
            "component": "project-hub",
            # no line key (project-level) — must degrade to None.
            "message": "Define and throw a dedicated exception.",
        },
    ],
}


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    return settings


async def test_fetch_issues_success_parses_and_strips_prefix() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_ISSUES_PAYLOAD)

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues("project-hub", types="BUG", page=1, page_size=50)

    assert captured["path"] == "/api/issues/search"
    # type filter forwarded as `types`, resolved=false, paging passthrough.
    assert captured["params"]["types"] == "BUG"
    assert captured["params"]["componentKeys"] == "project-hub"
    assert captured["params"]["resolved"] == "false"
    assert captured["params"]["p"] == "1"
    assert captured["params"]["ps"] == "50"

    assert result.status == "ok"
    assert result.total == 2
    assert len(result.issues) == 2
    first = result.issues[0]
    # "<projectKey>:" prefix stripped → relative file path.
    assert first.component == "backend/app/services/sonarqube.py"
    assert first.key == "AY-issue-1"
    assert first.rule == "python:S1481"
    assert first.severity == "MINOR"
    assert first.type == "CODE_SMELL"
    assert first.line == 42
    assert first.hash == "abc123"
    # project-level component (no colon) returned unchanged; missing keys → None.
    second = result.issues[1]
    assert second.component == "project-hub"
    assert second.line is None
    assert second.hash is None
    assert second.severity == "MAJOR"


async def test_fetch_issues_omits_filter_params_when_none() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 0, "issues": []})

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        await sonarqube.fetch_issues("project-hub", types=None, severities=None)

    # Omitted filters must be ABSENT, never sent as empty string (SonarQube 400s on that).
    assert "types" not in captured["params"]
    assert "severities" not in captured["params"]


async def test_fetch_issues_severity_passthrough() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 0, "issues": []})

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        await sonarqube.fetch_issues("project-hub", severities="CRITICAL")

    assert captured["params"]["severities"] == "CRITICAL"


async def test_fetch_issues_paging_total_shape() -> None:
    """Newer SonarQube builds nest `total` under `paging` — read defensively."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"paging": {"total": 7}, "issues": []}
        )

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues("project-hub")

    assert result.status == "ok"
    assert result.total == 7


async def test_fetch_issues_empty_result() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "issues": []})

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues("project-hub", types="BUG")

    assert result.status == "ok"
    assert result.total == 0
    assert result.issues == []


async def test_fetch_issues_connection_error_unreachable() -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sonarqube down")

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_boom)),
    ):
        result = await sonarqube.fetch_issues("project-hub", types="BUG")

    assert result.status == "unreachable"
    assert result.total == 0
    assert result.issues == []


async def test_fetch_issues_401_unreachable() -> None:
    def _unauth(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"msg": "Insufficient privileges"}]})

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_unauth)),
    ):
        result = await sonarqube.fetch_issues("project-hub")

    assert result.status == "unreachable"
    assert result.issues == []


async def test_fetch_issues_malformed_json_unreachable() -> None:
    def _bad_shape(_request: httpx.Request) -> httpx.Response:
        # 200 but missing the "issues" key → KeyError must be isolated.
        return httpx.Response(200, json={"unexpected": "shape"})

    with (
        patch.object(sonarqube, "get_settings", return_value=_make_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_bad_shape)),
    ):
        result = await sonarqube.fetch_issues("project-hub")

    assert result.status == "unreachable"
    assert result.issues == []


def test_strip_project_prefix_variants() -> None:
    assert (
        sonarqube._strip_project_prefix("project-hub:backend/app/x.py", "project-hub")
        == "backend/app/x.py"
    )
    # project-level component (no colon) — unchanged.
    assert sonarqube._strip_project_prefix("project-hub", "project-hub") == "project-hub"
    # colon but a different/odd key — still strips the first segment.
    assert sonarqube._strip_project_prefix("other:path/y.py", "project-hub") == "path/y.py"


# ===========================================================================
# Endpoint layer — TestClient with DI overrides (membership-API pattern)
# ===========================================================================


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


async def _seed_board(session: AsyncSession, *, project_key: str | None) -> tuple[Board, Actor]:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    session.add(workflow)
    await session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    session.add(admin)
    await session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
        sonarqube_project_key=project_key,
    )
    session.add(board)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
    await session.commit()

    refreshed_board = (
        await session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.memberships))
        )
    ).scalar_one()
    refreshed_admin = (
        await session.execute(
            select(Actor).where(Actor.id == admin.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    return refreshed_board, refreshed_admin


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


async def test_endpoint_happy_path_shape_and_dashboard_url(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ISSUES_PAYLOAD)

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = "http://localhost:9000"
    settings.sonarqube_token = "super-secret-token"
    settings.sonarqube_enabled = True
    settings.sonarqube_project_key_map = ""

    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=settings),
            patch("app.api.boards.get_settings", return_value=settings),
            patch.object(httpx, "AsyncClient", _client_with(_handler)),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/issues?type=BUG&page_size=5")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 5
    assert len(data["issues"]) == 2
    item = data["issues"][0]
    for field_name in ("key", "rule", "severity", "type", "component", "line", "message", "hash"):
        assert field_name in item
    assert item["component"] == "backend/app/services/sonarqube.py"
    # dashboard_url is HOST-facing, built from scan-url, never compose-internal.
    assert data["dashboard_url"] == "http://localhost:9000/project/issues?id=project-hub"
    assert "sonarqube:9000" not in data["dashboard_url"]
    # No token anywhere in the serialized response.
    assert "super-secret-token" not in resp.text


async def test_endpoint_page_size_over_100_is_422(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        resp = client.get(f"/api/boards/{board.id}/sonarqube/issues?page_size=101")
    finally:
        _clear()
    assert resp.status_code == 422


async def test_endpoint_unreachable_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")

    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sonarqube down")

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = "http://localhost:9000"
    settings.sonarqube_token = "tok"
    settings.sonarqube_enabled = True
    settings.sonarqube_project_key_map = ""

    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=settings),
            patch("app.api.boards.get_settings", return_value=settings),
            patch.object(httpx, "AsyncClient", _client_with(_boom)),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/issues?type=BUG")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "unreachable"
    assert data["issues"] == []
    assert data["total"] == 0


async def test_endpoint_no_project_key_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key=None)

    settings = MagicMock()
    settings.sonarqube_enabled = True
    settings.sonarqube_project_key_map = ""

    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=settings),
            patch("app.api.boards.get_settings", return_value=settings),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/issues")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_project_key"
    assert data["issues"] == []
    assert data["dashboard_url"] is None


async def test_endpoint_not_configured_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")

    settings = MagicMock()
    settings.sonarqube_enabled = False
    settings.sonarqube_project_key_map = ""

    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=settings),
            patch("app.api.boards.get_settings", return_value=settings),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/issues")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_configured"
    assert data["issues"] == []
    assert data["dashboard_url"] is None


async def test_endpoint_missing_board_is_404(mem_session: AsyncSession) -> None:
    _, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        resp = client.get(f"/api/boards/{uuid4()}/sonarqube/issues")
    finally:
        _clear()
    # A genuinely missing board is a legit 404 — distinct from sonar degradation.
    assert resp.status_code == 404
