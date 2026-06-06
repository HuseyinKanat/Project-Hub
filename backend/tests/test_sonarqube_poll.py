"""PH-193 — tests for the SonarQube poll cron tick + board-health serialization.

Uses a real in-memory sqlite session (mirrors conftest.db_session) so the upsert and
UniqueConstraint(board_id) are exercised for real, not mocked. fetch_board_metrics is
patched to return a fixed SonarSnapshot (or None) — we are testing the poller's
persistence + event + skip logic, not the httpx client (that's test_sonarqube_client).

Covers:
  - board WITH key → exactly one sonarqube_metrics row (assert columns) + EventBus.publish
    called once with type=="sonarqube_synced" on board:{id}
  - second tick UPDATES the same row (no duplicate — unique constraint holds)
  - board WITHOUT key → no row, no publish
  - client returns None → no row, no publish, no exception
  - board serialization: metric present → BoardResponse.health populated; absent → None
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, SonarQubeMetric, Workflow
from app.services import sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.sonarqube import SonarSnapshot

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


async def _make_board(session: AsyncSession, key: str, project_key: str | None) -> Board:
    workflow = Workflow(
        name=f"wf-{key}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()
    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    session.add(admin)
    await session.flush()
    board = Board(
        key=key,
        name=f"Board {key}",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
        sonarqube_project_key=project_key,
    )
    session.add(board)
    await session.commit()
    return board


async def _metric_rows(session: AsyncSession, board: Board) -> list[SonarQubeMetric]:
    result = await session.execute(
        select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
    )
    return list(result.scalars().all())


def _snapshot() -> SonarSnapshot:
    return SonarSnapshot(
        quality_gate_status="OK",
        bugs=3,
        vulnerabilities=1,
        code_smells=42,
        coverage=Decimal("87.50"),
        duplicated_lines_density=Decimal("2.10"),
        ncloc=12345,
        raw_measures={"bugs": "3", "coverage": "87.5", "ncloc": "12345"},
    )


# ---------------------------------------------------------------------------
# poll_board — success: one row + one publish
# ---------------------------------------------------------------------------


async def test_poll_board_with_key_upserts_row_and_publishes(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, "PH", "ph-key")

    with (
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())
        ),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()) as mock_publish,
    ):
        wrote = await sonarqube.poll_board(mem_session, board)

    assert wrote is True

    rows = await _metric_rows(mem_session, board)
    assert len(rows) == 1
    row = rows[0]
    assert row.project_key == "ph-key"
    assert row.quality_gate_status == "OK"
    assert row.bugs == 3
    assert row.vulnerabilities == 1
    assert row.code_smells == 42
    assert row.coverage == Decimal("87.50")
    assert row.duplicated_lines_density == Decimal("2.10")
    assert row.ncloc == 12345
    assert row.raw_measures["coverage"] == "87.5"
    assert row.fetched_at is not None

    assert mock_publish.await_count == 1
    envelope = mock_publish.await_args.args[0]
    assert envelope.type == "sonarqube_synced"
    assert envelope.board_id == str(board.id)
    assert envelope.ticket_id == ""  # board-level event sentinel
    assert envelope.payload["quality_gate_status"] == "OK"
    assert envelope.payload["bugs"] == 3
    assert envelope.payload["coverage"] == 87.5
    assert envelope.payload["ncloc"] == 12345


# ---------------------------------------------------------------------------
# poll_board — second tick updates the same row (no duplicate)
# ---------------------------------------------------------------------------


async def test_poll_board_second_tick_updates_in_place(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, "PH", "ph-key")

    first = _snapshot()
    second = SonarSnapshot(
        quality_gate_status="ERROR",
        bugs=9,
        vulnerabilities=2,
        code_smells=50,
        coverage=Decimal("80.00"),
        duplicated_lines_density=Decimal("3.00"),
        ncloc=13000,
        raw_measures={"bugs": "9"},
    )

    with (
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=first)),
    ):
        await sonarqube.poll_board(mem_session, board)
    with (
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=second)),
    ):
        await sonarqube.poll_board(mem_session, board)

    rows = await _metric_rows(mem_session, board)
    assert len(rows) == 1  # unique constraint holds — updated in place, no duplicate
    assert rows[0].quality_gate_status == "ERROR"
    assert rows[0].bugs == 9
    assert rows[0].ncloc == 13000


# ---------------------------------------------------------------------------
# poll_board — no key → skip (no row, no publish)
# ---------------------------------------------------------------------------


async def test_poll_board_without_key_skips(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, "NK", None)

    # Ensure no project_key_map mapping resolves this board.
    settings = MagicMock()
    settings.sonarqube_project_key_map = ""
    with (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock()) as mock_fetch,
        patch.object(sonarqube.EventBus, "publish", AsyncMock()) as mock_publish,
    ):
        wrote = await sonarqube.poll_board(mem_session, board)

    assert wrote is False
    mock_fetch.assert_not_called()
    mock_publish.assert_not_called()
    assert await _metric_rows(mem_session, board) == []


# ---------------------------------------------------------------------------
# poll_board — client returns None → no row, no publish, no exception
# ---------------------------------------------------------------------------


async def test_poll_board_client_none_no_row_no_publish(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, "PH", "ph-key")

    with (
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=None)),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()) as mock_publish,
    ):
        wrote = await sonarqube.poll_board(mem_session, board)

    assert wrote is False
    mock_publish.assert_not_called()
    assert await _metric_rows(mem_session, board) == []


# ---------------------------------------------------------------------------
# _poll_all_boards — one bad board does not kill the tick (error isolation)
# ---------------------------------------------------------------------------


async def test_poll_all_boards_isolated_per_board(mem_session: AsyncSession) -> None:
    good = await _make_board(mem_session, "PH", "ph-key")
    await _make_board(mem_session, "NK", None)  # skipped (no key)

    SessionLocalFactory = async_sessionmaker(
        mem_session.bind, expire_on_commit=False
    )

    with (
        patch.object(sonarqube, "SessionLocal", SessionLocalFactory),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()) as mock_publish,
    ):
        await sonarqube._poll_all_boards()

    # Only the keyed board produced a row + event.
    rows = (await mem_session.execute(select(SonarQubeMetric))).scalars().all()
    assert len(rows) == 1
    assert rows[0].board_id == good.id
    assert mock_publish.await_count == 1


# ---------------------------------------------------------------------------
# Board serialization — health populated / null
# ---------------------------------------------------------------------------


async def test_board_response_health_populated(mem_session: AsyncSession) -> None:
    from app.services.serializers import board_response

    board = await _make_board(mem_session, "PH", "ph-key")
    with (
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        await sonarqube.poll_board(mem_session, board)

    loaded = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.repository),
                selectinload(Board.sonarqube_metric),
            )
        )
    ).scalar_one()

    resp = board_response(loaded)
    assert resp.health is not None
    assert resp.health.quality_gate_status == "OK"
    assert resp.health.bugs == 3
    assert resp.health.coverage == 87.5  # Decimal → float
    assert resp.health.ncloc == 12345


async def test_board_response_health_null_when_no_metric(mem_session: AsyncSession) -> None:
    from app.services.serializers import board_response

    board = await _make_board(mem_session, "PH", "ph-key")
    loaded = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.repository),
                selectinload(Board.sonarqube_metric),
            )
        )
    ).scalar_one()

    resp = board_response(loaded)
    assert resp.health is None
