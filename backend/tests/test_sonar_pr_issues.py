"""PH-328 — SonarQube PR-review scope: fetch_issues(files, in_new_code_period) +
the sonar_pr_issues MCP tool.

Two layers, both network-free (mirrors test_sonarqube_issues.py):

  Service (services/sonarqube.fetch_issues) — httpx via ``httpx.MockTransport``.
  Covers the changed-file client-side intersection, the inNewCodePeriod passthrough,
  the empty/None files semantics, and that the new params still never-500.

  MCP tool (mcp/server._dispatch_tool "sonar_pr_issues") — the seed board + a real
  fetch_issues under a mocked httpx client. Covers the changed-file scope end-to-end,
  the member gate (403 non-member), and the never-500 status ladder (no_project_key /
  not_configured / unreachable).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import PermissionDenied
from app.db.models import Actor
from app.mcp.server import _dispatch_tool
from app.services import sonarqube

# asyncio_mode="auto" (pyproject) auto-detects async tests.

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_with(handler: Any) -> Any:
    """Factory that ignores ctor kwargs and yields a MockTransport-backed client."""

    def _factory(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(
            base_url="http://sonarqube:9000",
            transport=httpx.MockTransport(handler),
        )

    return _factory


# Three file-level issues across two changed files + one in an untouched file.
_SCOPED_PAYLOAD = {
    "total": 3,
    "p": 1,
    "ps": 100,
    "issues": [
        {
            "key": "iss-a",
            "rule": "python:S1481",
            "severity": "MINOR",
            "type": "CODE_SMELL",
            "component": "project-hub:backend/app/services/sonarqube.py",
            "line": 42,
            "message": "Remove this unused local variable.",
            "hash": "h1",
        },
        {
            "key": "iss-b",
            "rule": "python:S112",
            "severity": "MAJOR",
            "type": "BUG",
            "component": "project-hub:backend/app/cli.py",
            "line": 10,
            "message": "Define a dedicated exception.",
            "hash": "h2",
        },
        {
            "key": "iss-c",
            "rule": "python:S1234",
            "severity": "CRITICAL",
            "type": "VULNERABILITY",
            # an issue in a file the PR did NOT touch — must be filtered out.
            "component": "project-hub:backend/app/untouched.py",
            "line": 5,
            "message": "Unrelated pre-existing issue.",
            "hash": "h3",
        },
    ],
}


def _sonar_settings() -> MagicMock:
    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    settings.sonarqube_scan_url = "http://localhost:9000"
    settings.sonarqube_enabled = True
    settings.sonarqube_project_key_map = ""
    return settings


# ===========================================================================
# Service layer — fetch_issues(files=..., in_new_code_period=...)
# ===========================================================================


async def test_fetch_issues_changed_file_scope_intersects_client_side() -> None:
    """files=[changed] returns ONLY issues whose component is in that set; total becomes
    the filtered (PR-scoped) count. The server is NOT sent a `files=` param (portability)."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_SCOPED_PAYLOAD)

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues(
            "project-hub",
            files=["backend/app/services/sonarqube.py", "backend/app/cli.py"],
        )

    # No server-side files filter (avoids a 400→unreachable on Community builds).
    assert "files" not in captured["params"]
    assert result.status == "ok"
    # iss-c (untouched.py) filtered out; total reflects the scoped count.
    assert {i.key for i in result.issues} == {"iss-a", "iss-b"}
    assert result.total == 2
    assert all(
        i.component in {"backend/app/services/sonarqube.py", "backend/app/cli.py"}
        for i in result.issues
    )


async def test_fetch_issues_in_new_code_period_sends_param() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 0, "issues": []})

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        await sonarqube.fetch_issues("project-hub", in_new_code_period=True)

    assert captured["params"]["inNewCodePeriod"] == "true"


async def test_fetch_issues_new_code_param_omitted_by_default() -> None:
    """Default in_new_code_period=False must NOT send the param (back-compat with the
    PH-203 callers)."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 0, "issues": []})

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        await sonarqube.fetch_issues("project-hub")

    assert "inNewCodePeriod" not in captured["params"]


async def test_fetch_issues_empty_files_returns_empty() -> None:
    """files=[] (a PR that changed nothing sonar-relevant) → empty result even though the
    server returned issues. Distinct from files=None (no filter)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_SCOPED_PAYLOAD)

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues("project-hub", files=[])

    assert result.status == "ok"
    assert result.issues == []
    assert result.total == 0


async def test_fetch_issues_files_none_applies_no_filter() -> None:
    """files=None preserves the PH-203 behavior: the full server set, server total."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_SCOPED_PAYLOAD)

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await sonarqube.fetch_issues("project-hub", files=None)

    assert result.total == 3
    assert len(result.issues) == 3


async def test_fetch_issues_scope_still_never_500_on_connect_error() -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sonarqube down")

    with (
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_boom)),
    ):
        result = await sonarqube.fetch_issues(
            "project-hub", files=["backend/app/cli.py"], in_new_code_period=True
        )

    assert result.status == "unreachable"
    assert result.issues == []
    assert result.total == 0


# ===========================================================================
# MCP tool — _dispatch_tool("sonar_pr_issues")
# ===========================================================================


async def _outsider(db_session) -> Actor:
    """An agent with no membership on the seed board (loaded with memberships eager)."""
    actor = Actor(
        kind="agent", display_name="jarwis-pr-reviewer@stranger", token_hash="x", is_active=True
    )
    db_session.add(actor)
    await db_session.commit()
    return (
        await db_session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_sonar_pr_issues_member_changed_file_scope(db_session, seed) -> None:
    """A board member gets issues scoped to the PR's changed files, with a HOST-facing
    dashboard_url and no token in the payload."""
    seed.board.sonarqube_project_key = "project-hub"
    await db_session.commit()

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_SCOPED_PAYLOAD)

    with (
        patch("app.mcp.server.get_settings", return_value=_sonar_settings()),
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        result = await _dispatch_tool(
            "sonar_pr_issues",
            {"board": "PH", "files": ["backend/app/cli.py"], "new_code_only": True},
            seed.admin,
            db_session,
        )

    assert result["status"] == "ok"
    assert {i["key"] for i in result["issues"]} == {"iss-b"}
    assert result["total"] == 1
    assert result["dashboard_url"] == "http://localhost:9000/project/issues?id=project-hub"
    assert "tok" not in str(result)


@pytest.mark.asyncio
async def test_sonar_pr_issues_non_member_forbidden(db_session, seed) -> None:
    seed.board.sonarqube_project_key = "project-hub"
    await db_session.commit()
    outsider = await _outsider(db_session)

    with pytest.raises(PermissionDenied):
        await _dispatch_tool(
            "sonar_pr_issues",
            {"board": "PH", "files": ["backend/app/cli.py"]},
            outsider,
            db_session,
        )


@pytest.mark.asyncio
async def test_sonar_pr_issues_no_project_key_graceful(db_session, seed) -> None:
    """seed board carries no sonarqube_project_key → status=no_project_key, never raises."""
    with (
        patch("app.mcp.server.get_settings", return_value=_sonar_settings()),
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
    ):
        result = await _dispatch_tool(
            "sonar_pr_issues",
            {"board": "PH", "files": ["backend/app/cli.py"]},
            seed.admin,
            db_session,
        )

    assert result["status"] == "no_project_key"
    assert result["issues"] == []
    assert result["dashboard_url"] is None


@pytest.mark.asyncio
async def test_sonar_pr_issues_disabled_not_configured(db_session, seed) -> None:
    seed.board.sonarqube_project_key = "project-hub"
    await db_session.commit()

    settings = _sonar_settings()
    settings.sonarqube_enabled = False

    with (
        patch("app.mcp.server.get_settings", return_value=settings),
        patch.object(sonarqube, "get_settings", return_value=settings),
    ):
        result = await _dispatch_tool(
            "sonar_pr_issues",
            {"board": "PH", "files": ["backend/app/cli.py"]},
            seed.admin,
            db_session,
        )

    assert result["status"] == "not_configured"
    assert result["issues"] == []


@pytest.mark.asyncio
async def test_sonar_pr_issues_unreachable_graceful(db_session, seed) -> None:
    """SonarQube down → the tool maps to status=unreachable (a pr-reviewer bridges this to
    verification_tool_unavailable + decision=blocked — never a false PASS), never 500."""
    seed.board.sonarqube_project_key = "project-hub"
    await db_session.commit()

    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sonarqube down")

    with (
        patch("app.mcp.server.get_settings", return_value=_sonar_settings()),
        patch.object(sonarqube, "get_settings", return_value=_sonar_settings()),
        patch.object(httpx, "AsyncClient", _client_with(_boom)),
    ):
        result = await _dispatch_tool(
            "sonar_pr_issues",
            {"board": "PH", "files": ["backend/app/cli.py"], "new_code_only": True},
            seed.admin,
            db_session,
        )

    assert result["status"] == "unreachable"
    assert result["issues"] == []
    assert result["total"] == 0
