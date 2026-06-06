"""PH-193 — tests for the SonarQube httpx client (services/sonarqube.py).

httpx is mocked via httpx.MockTransport (no network, no respx dependency). We patch
``httpx.AsyncClient`` inside the module so fetch_board_metrics opens a client backed by
our transport. Covers:
  - success path: project_status + measures parse into a SonarSnapshot (typed columns)
  - error paths: connection error / 401 / malformed JSON → returns None, never raises
  - resolve_project_key: column wins, then project_key_map, then None (skip)
  - type coercion: a malformed measure degrades to None for that field
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from app.db.models import Board
from app.services import sonarqube

# asyncio_mode="auto" (pyproject) auto-detects async tests — no module mark needed
# (a module-level asyncio mark would falsely flag the sync resolve_* tests).


# ---------------------------------------------------------------------------
# MockTransport-backed AsyncClient helper
# ---------------------------------------------------------------------------


# Capture the real class BEFORE any patching so the factory below builds a genuine
# client (patching httpx.AsyncClient with a factory that itself called
# httpx.AsyncClient would recurse infinitely).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_with(handler: Any) -> Any:
    """Return a factory that ignores ctor kwargs and yields a MockTransport client.

    Used to patch httpx.AsyncClient so fetch_board_metrics' own ctor kwargs
    (base_url/auth/timeout) are accepted but routing is driven by our handler.
    """

    def _factory(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(
            base_url="http://sonarqube:9000",
            transport=httpx.MockTransport(handler),
        )

    return _factory


def _success_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/qualitygates/project_status":
        return httpx.Response(200, json={"projectStatus": {"status": "OK"}})
    if request.url.path == "/api/measures/component":
        return httpx.Response(
            200,
            json={
                "component": {
                    "measures": [
                        {"metric": "bugs", "value": "3"},
                        {"metric": "vulnerabilities", "value": "1"},
                        {"metric": "code_smells", "value": "42"},
                        {"metric": "coverage", "value": "87.5"},
                        {"metric": "duplicated_lines_density", "value": "2.10"},
                        {"metric": "ncloc", "value": "12345"},
                    ]
                }
            },
        )
    return httpx.Response(404)


# ---------------------------------------------------------------------------
# resolve_project_key
# ---------------------------------------------------------------------------


def _board(key: str = "PH", project_key: str | None = None) -> Board:
    b = MagicMock(spec=Board)
    b.key = key
    b.sonarqube_project_key = project_key
    return b


def test_resolve_project_key_column_wins() -> None:
    board = _board(project_key="explicit-key")
    assert sonarqube.resolve_project_key(board) == "explicit-key"


def test_resolve_project_key_from_map() -> None:
    board = _board(key="PH", project_key=None)
    settings = MagicMock()
    settings.sonarqube_project_key_map = json.dumps({"PH": "ph-sonar-key"})
    with patch.object(sonarqube, "get_settings", return_value=settings):
        assert sonarqube.resolve_project_key(board) == "ph-sonar-key"


def test_resolve_project_key_none_when_unmapped() -> None:
    board = _board(key="OTHER", project_key=None)
    settings = MagicMock()
    settings.sonarqube_project_key_map = json.dumps({"PH": "ph-sonar-key"})
    with patch.object(sonarqube, "get_settings", return_value=settings):
        assert sonarqube.resolve_project_key(board) is None


def test_resolve_project_key_bad_json_returns_none() -> None:
    board = _board(key="PH", project_key=None)
    settings = MagicMock()
    settings.sonarqube_project_key_map = "{not valid json"
    with patch.object(sonarqube, "get_settings", return_value=settings):
        assert sonarqube.resolve_project_key(board) is None


# ---------------------------------------------------------------------------
# fetch_board_metrics — success
# ---------------------------------------------------------------------------


async def test_fetch_board_metrics_success_parses_snapshot() -> None:
    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(httpx, "AsyncClient", _client_with(_success_handler)),
    ):
        snap = await sonarqube.fetch_board_metrics("ph-key")

    assert snap is not None
    assert snap.quality_gate_status == "OK"
    assert snap.bugs == 3
    assert snap.vulnerabilities == 1
    assert snap.code_smells == 42
    assert snap.coverage == Decimal("87.5")
    assert snap.duplicated_lines_density == Decimal("2.10")
    assert snap.ncloc == 12345
    # raw_measures preserves the verbatim map.
    assert snap.raw_measures["bugs"] == "3"
    assert snap.raw_measures["coverage"] == "87.5"


# ---------------------------------------------------------------------------
# fetch_board_metrics — error isolation (returns None, never raises)
# ---------------------------------------------------------------------------


async def test_fetch_board_metrics_connection_error_returns_none() -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sonarqube down")

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(httpx, "AsyncClient", _client_with(_boom)),
    ):
        assert await sonarqube.fetch_board_metrics("ph-key") is None


async def test_fetch_board_metrics_401_returns_none() -> None:
    def _unauth(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"msg": "Insufficient privileges"}]})

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "bad"
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(httpx, "AsyncClient", _client_with(_unauth)),
    ):
        assert await sonarqube.fetch_board_metrics("ph-key") is None


async def test_fetch_board_metrics_malformed_json_returns_none() -> None:
    def _bad_shape(request: httpx.Request) -> httpx.Response:
        # 200 but missing the expected projectStatus key → KeyError, must isolate.
        return httpx.Response(200, json={"unexpected": "shape"})

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(httpx, "AsyncClient", _client_with(_bad_shape)),
    ):
        assert await sonarqube.fetch_board_metrics("ph-key") is None


async def test_fetch_board_metrics_malformed_measure_degrades_field() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/qualitygates/project_status":
            return httpx.Response(200, json={"projectStatus": {"status": "ERROR"}})
        return httpx.Response(
            200,
            json={
                "component": {
                    "measures": [
                        {"metric": "bugs", "value": "not-a-number"},  # malformed
                        {"metric": "ncloc", "value": "100"},
                    ]
                }
            },
        )

    settings = MagicMock()
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_token = "tok"
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(httpx, "AsyncClient", _client_with(_handler)),
    ):
        snap = await sonarqube.fetch_board_metrics("ph-key")

    assert snap is not None
    assert snap.quality_gate_status == "ERROR"
    assert snap.bugs is None  # malformed → degraded, not a crash
    assert snap.ncloc == 100
