"""PH-246 — per-repo SonarQube project keys + multi-repo scan plan + per-repo health.

Covers the PH-246 acceptance criteria (network-free + scanner-free, real in-memory
sqlite session matching the PH-236 test pattern):

  * ``derive_repo_project_key`` — explicit override wins; primary INHERITS the board key
    (no rename — KIM/PH/GXA-primary unchanged); siblings derive ``<primaryKey>-<slug>``.
  * ``build_scan_plans`` — N plans for a multi-repo board (GXA = 3), each with a per-repo
    ``project_key`` + ``container_source`` (from THAT repo's local_path) + ``repo_id``/
    ``repo_slug``; exactly 1 byte-identical plan for a single-repo / repo-less board; the
    kept ``build_scan_plan`` wrapper returns that same primary plan.
  * ``request_board_scan`` — one ``SonarScanJob`` per scannable repo, idempotent per
    ``(board_id, repo_id)`` (re-click does not stack); ``PendingScanItem`` carries
    ``repo_slug``.
  * per-repo health — ``poll_repo`` upserts one row per ``(board_id, repo_id)``;
    ``BoardResponse.repo_health`` breakdown + ``health`` = the PRIMARY repo's metric.
  * the NEW ``GET .../sonarqube/scan-plans`` (list) alongside the KEPT single-object
    ``/scan-plan``.
  * the migration backfill (driven on a REAL sqlite engine) — primary-repo key from the
    legacy board column + the relaxed ``(board_id, repo_id)`` unique constraint.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    Repository,
    SonarQubeMetric,
    SonarScanJob,
    Workflow,
)
from app.db.session import get_db_session
from app.main import app
from app.services import repo_paths, sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.serializers import board_response

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


def _settings(
    *, enabled: bool = True, dotnet_enabled: bool = False, root: str = "/Users/huseyinkanat"
) -> MagicMock:
    s = MagicMock()
    s.sonarqube_enabled = enabled
    # PH-257 — explicit bool (a bare MagicMock attr is truthy and would flip the csharp gate).
    s.sonar_dotnet_enabled = dotnet_enabled
    s.sonarqube_url = "http://sonarqube:9000"
    s.sonarqube_scan_url = "http://localhost:9000"
    s.sonarqube_token = _SECRET
    s.sonarqube_project_key_map = ""
    s.host_home = root
    s.repos_root = root
    return s


def _patch_all(settings: MagicMock):
    return (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(repo_paths, "get_settings", return_value=settings),
        patch("app.api.boards.get_settings", return_value=settings),
    )


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("// x\n")


async def _seed_multirepo_board(
    session: AsyncSession,
    *,
    key: str = "GXA",
    project_key: str | None = "GameX",
    primary_local_path: str,
    sibling_specs: list[tuple[str, str]] | None = None,  # (slug, local_path)
    member_role: str = "admin",
) -> tuple[Board, Actor]:
    """Seed a board with a primary repo + N siblings, all with distinct local_paths."""
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
        repos_path=primary_local_path,
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
            local_path=primary_local_path,
        )
    )
    for slug, local_path in sibling_specs or []:
        session.add(
            Repository(
                board_id=board.id,
                slug=slug,
                name=slug,
                is_primary=False,
                provider="local",
                default_branch="main",
                local_path=local_path,
            )
        )
    await session.commit()

    refreshed_board = (
        await session.execute(
            select(Board)
            .where(Board.id == board.id)
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
    refreshed_actor = (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    return refreshed_board, refreshed_actor


# ===========================================================================
# derive_repo_project_key — primary inherits, siblings <primaryKey>-<slug>
# ===========================================================================


def _repo(slug: str, *, is_primary: bool, key: str | None = None) -> Repository:
    return Repository(
        id=uuid4(),
        board_id=uuid4(),
        slug=slug,
        name=slug,
        is_primary=is_primary,
        provider="local",
        default_branch="main",
        local_path=f"/repos/{slug}",
        sonarqube_project_key=key,
    )


def test_derive_primary_inherits_board_key() -> None:
    board = Board(key="GXA", sonarqube_project_key="GameX")
    primary = _repo("gamexcore", is_primary=True)
    assert sonarqube.derive_repo_project_key(board, primary) == "GameX"


def test_derive_sibling_is_base_dash_slug() -> None:
    board = Board(key="GXA", sonarqube_project_key="GameX")
    assert (
        sonarqube.derive_repo_project_key(board, _repo("gamexsdk", is_primary=False))
        == "GameX-gamexsdk"
    )
    assert (
        sonarqube.derive_repo_project_key(
            board, _repo("gamexandroiddemoapp", is_primary=False)
        )
        == "GameX-gamexandroiddemoapp"
    )


def test_derive_explicit_key_wins() -> None:
    board = Board(key="GXA", sonarqube_project_key="GameX")
    pinned = _repo("gamexsdk", is_primary=False, key="custom-pinned-key")
    assert sonarqube.derive_repo_project_key(board, pinned) == "custom-pinned-key"


def test_derive_ph_primary_short_circuits_to_project_hub() -> None:
    """PH stays special-cased — primary inherits the PH-literal ``project-hub``."""
    board = Board(key="PH", sonarqube_project_key=None)
    primary = _repo("project-hub", is_primary=True)
    assert sonarqube.derive_repo_project_key(board, primary) == "project-hub"


def test_derive_primary_no_board_key_falls_back_to_default() -> None:
    """A non-PH board with no key + no repos_path → board.key.lower() default base."""
    board = Board(key="KIM", sonarqube_project_key=None, repos_path=None)
    primary = _repo("kims", is_primary=True)
    assert sonarqube.derive_repo_project_key(board, primary) == "kim"


# ===========================================================================
# build_scan_plans — N plans for GXA, 1 for single-repo; wrapper parity
# ===========================================================================


async def test_build_scan_plans_three_plans_for_gxa(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: GXA (3 repos) → 3 plans, distinct keys, per-repo container_source + repo ids."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    demo = os.path.join(str(tmp_path), "gamexandroiddemoapp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    _touch(os.path.join(demo, "src", "Demo.kt"))

    board, _ = await _seed_multirepo_board(
        mem_session,
        primary_local_path=core,
        sibling_specs=[("gamexsdk", sdk), ("gamexandroiddemoapp", demo)],
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plans = sonarqube.build_scan_plans(board)

    assert len(plans) == 3
    by_slug = {p.repo_slug: p for p in plans}
    # primary inherits the board key; siblings get <primaryKey>-<slug>.
    assert by_slug["gamexcore"].project_key == "GameX"
    assert by_slug["gamexsdk"].project_key == "GameX-gamexsdk"
    assert by_slug["gamexandroiddemoapp"].project_key == "GameX-gamexandroiddemoapp"
    # Each container_source is THAT repo's local_path (not the primary's).
    assert by_slug["gamexcore"].container_source == core
    assert by_slug["gamexsdk"].container_source == sdk
    assert by_slug["gamexandroiddemoapp"].container_source == demo
    # Each is self-describing (repo_id + repo_slug set) + scannable kotlin.
    for plan in plans:
        assert plan.repo_id is not None
        assert plan.repo_slug is not None
        assert plan.language == "kotlin"
        assert plan.supported is True
    # Primary-first ordering.
    assert plans[0].repo_slug == "gamexcore"


async def test_build_scan_plans_single_repo_matches_wrapper(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: a single-repo board → exactly 1 plan == build_scan_plan(board) output."""
    core = os.path.join(str(tmp_path), "kims")
    _touch(os.path.join(core, "app", "main.py"))
    board, _ = await _seed_multirepo_board(
        mem_session, key="KIM", project_key="kims", primary_local_path=core
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plans = sonarqube.build_scan_plans(board)
        wrapper = sonarqube.build_scan_plan(board)

    assert len(plans) == 1
    assert wrapper.project_key == plans[0].project_key == "kims"
    assert wrapper.container_source == plans[0].container_source == core
    assert wrapper == plans[0]  # wrapper returns the primary plan byte-for-byte


async def test_build_scan_plans_unscannable_sibling_kept_not_dropped(
    mem_session: AsyncSession, tmp_path
) -> None:
    """A sibling with no usable local_path → an UNSCANNABLE plan element (not dropped)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    # Sibling local_path points at a path NOT under repos_root → unscannable.
    board, _ = await _seed_multirepo_board(
        mem_session,
        primary_local_path=core,
        sibling_specs=[("gamexsdk", "/repos/does-not-exist-tree")],
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plans = sonarqube.build_scan_plans(board)
    assert len(plans) == 2  # sibling kept, not dropped
    sdk = next(p for p in plans if p.repo_slug == "gamexsdk")
    # local_path /repos/... is treated as container-form; the tree doesn't exist so
    # language is None and the plan is still emitted (the runner skips it).
    assert sdk.project_key == "GameX-gamexsdk"
    assert sdk.repo_id is not None


# ===========================================================================
# request_board_scan — one job per scannable repo, idempotent per (board, repo)
# ===========================================================================


async def test_request_scan_enqueues_one_job_per_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: GXA scan → one SonarScanJob per scannable repo, each with repo_id + repo_slug."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(
            mem_session, board, requested_by=actor.id
        )

    assert result.scan_status == "queued"
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 2
    keys = {j.project_key for j in jobs}
    assert keys == {"GameX", "GameX-gamexsdk"}
    for j in jobs:
        assert j.repo_id is not None
        assert j.repo_slug in {"gamexcore", "gamexsdk"}
    # per-repo outcome breakdown surfaced.
    assert {o.scan_status for o in result.repos} == {"queued"}
    assert {o.repo_slug for o in result.repos} == {"gamexcore", "gamexsdk"}


async def test_request_scan_idempotent_per_board_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: re-clicking Scan while GXA's jobs wait re-uses them (no stacking 4)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        await sonarqube.request_board_scan(mem_session, board, requested_by=actor.id)
        await sonarqube.request_board_scan(mem_session, board, requested_by=actor.id)

    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 2  # still 2 — re-used per (board_id, repo_id), not stacked to 4


async def test_pending_scans_carry_repo_slug(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: GET /scans/pending returns one item per queued repo, each with repo_slug."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        await sonarqube.request_board_scan(mem_session, board, requested_by=actor.id)
    pending = await sonarqube.list_pending_scans(mem_session)
    assert len(pending) == 2
    assert {p.repo_slug for p in pending} == {"gamexcore", "gamexsdk"}
    assert {p.project_key for p in pending} == {"GameX", "GameX-gamexsdk"}


# ===========================================================================
# per-repo health — poll_repo upserts (board_id, repo_id); repo_health breakdown
# ===========================================================================


def _snapshot(bugs: int) -> sonarqube.SonarSnapshot:
    return sonarqube.SonarSnapshot(
        quality_gate_status="OK",
        bugs=bugs,
        vulnerabilities=0,
        code_smells=1,
        coverage=Decimal("80.00"),
        duplicated_lines_density=Decimal("1.00"),
        ncloc=100,
        raw_measures={"bugs": str(bugs)},
    )


async def test_poll_board_upserts_one_metric_per_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: per-repo poll → one SonarQubeMetric row per (board_id, repo_id)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, _ = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with (
        p1,
        p2,
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(3))
        ),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        wrote = await sonarqube.poll_board(mem_session, board)

    assert wrote is True
    metrics = (
        await mem_session.execute(
            select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
        )
    ).scalars().all()
    assert len(metrics) == 2  # one per repo
    assert {m.project_key for m in metrics} == {"GameX", "GameX-gamexsdk"}
    assert all(m.repo_id is not None for m in metrics)


async def test_board_response_health_primary_repo_health_breakdown(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: health = PRIMARY repo metric (back-compat); repo_health = per-repo breakdown.

    PH-252: a 3rd linked-but-NEVER-polled sibling (gamexandroiddemoapp) is seeded but
    not polled → it must STILL produce a card (len 2→3), with null gate/metrics/fetched_at,
    is_primary False, a derived non-null project_key, and dashboard_url None (null-until-
    first-scan). Also asserts primary-first-then-slug ordering.
    """
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    demo = os.path.join(str(tmp_path), "gamexandroiddemoapp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    _touch(os.path.join(demo, "src", "Demo.kt"))
    board, _ = await _seed_multirepo_board(
        mem_session,
        primary_local_path=core,
        # gamexandroiddemoapp is linked but NEVER polled below → unscanned card.
        sibling_specs=[("gamexsdk", sdk), ("gamexandroiddemoapp", demo)],
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    # poll the primary (3 bugs) and the sibling (7 bugs) into distinct metric rows.
    repos = {r.slug: r for r in board.repositories}
    with (
        p1,
        p2,
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        with patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(3))
        ):
            await sonarqube.poll_repo(mem_session, board, repos["gamexcore"], "GameX")
        with patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(7))
        ):
            await sonarqube.poll_repo(
                mem_session, board, repos["gamexsdk"], "GameX-gamexsdk"
            )

    # Re-fetch in a FRESH session (clean identity map) with the board_response
    # eager-load options — this mirrors prod, where get_board loads a fresh board per
    # request (the poll session's stale empty collection never leaks in).
    fresh_factory = async_sessionmaker(mem_session.bind, expire_on_commit=False)
    async with fresh_factory() as fresh:
        refreshed = (
            await fresh.execute(
                select(Board)
                .where(Board.id == board.id)
                .options(
                    selectinload(Board.workflow),
                    selectinload(Board.repositories),
                    selectinload(Board.sonarqube_metrics).selectinload(
                        SonarQubeMetric.repository
                    ),
                )
            )
        ).scalar_one()
        with p1:  # _dashboard_url reads get_settings in the sonarqube module
            resp = board_response(refreshed)

    # health = the PRIMARY repo's metric (3 bugs), back-compat single object.
    assert resp.health is not None
    assert resp.health.bugs == 3
    # PH-252: repo_health enumerates ALL 3 LINKED repos (not just the 2 polled ones).
    assert len(resp.repo_health) == 3
    # Primary-first then siblings by slug ordering (mirrors build_scan_plans).
    assert [rh.repo_slug for rh in resp.repo_health] == [
        "gamexcore",
        "gamexandroiddemoapp",
        "gamexsdk",
    ]
    by_slug = {rh.repo_slug: rh for rh in resp.repo_health}
    assert by_slug["gamexcore"].bugs == 3
    assert by_slug["gamexcore"].project_key == "GameX"
    assert by_slug["gamexsdk"].bugs == 7
    assert by_slug["gamexsdk"].project_key == "GameX-gamexsdk"
    assert by_slug["gamexsdk"].dashboard_url is not None
    # PH-251: is_primary is carried on the un-gated repo_health[] surface so the FE
    # `primary` badge survives a /repositories 403 — primary True, sibling False.
    assert by_slug["gamexcore"].is_primary is True
    assert by_slug["gamexsdk"].is_primary is False
    # PH-252: the never-polled 3rd sibling renders an honest "No analysis yet" card —
    # real identity + derived project_key, but null gate/metrics/fetched_at + null url.
    unscanned = by_slug["gamexandroiddemoapp"]
    assert unscanned.quality_gate_status is None
    assert unscanned.bugs is None
    assert unscanned.fetched_at is None
    assert unscanned.is_primary is False
    assert unscanned.repo_name == "gamexandroiddemoapp"  # real identity, not null
    assert unscanned.project_key == "GameX-gamexandroiddemoapp"  # derived, non-null
    assert unscanned.dashboard_url is None  # null-until-first-scan (PH-252 DECISION)
    assert _SECRET not in str(resp.model_dump())


async def test_repo_health_enumerates_unscanned_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-252: a board with 1 scanned + 1 NEVER-scanned linked repo → 2 entries.

    The metric-driven enumeration used to drop the unscanned repo; now it emits an
    honest null-metric card with real identity + derived project_key + null dashboard_url.
    """
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, _ = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    repos = {r.slug: r for r in board.repositories}
    # Poll ONLY the primary — gamexsdk stays unscanned (no metric row).
    with (
        p1,
        p2,
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(2))
        ),
    ):
        await sonarqube.poll_repo(mem_session, board, repos["gamexcore"], "GameX")

    fresh_factory = async_sessionmaker(mem_session.bind, expire_on_commit=False)
    async with fresh_factory() as fresh:
        refreshed = (
            await fresh.execute(
                select(Board)
                .where(Board.id == board.id)
                .options(
                    selectinload(Board.workflow),
                    selectinload(Board.repositories),
                    selectinload(Board.sonarqube_metrics).selectinload(
                        SonarQubeMetric.repository
                    ),
                )
            )
        ).scalar_one()
        with p1:
            resp = board_response(refreshed)

    assert len(resp.repo_health) == 2
    by_slug = {rh.repo_slug: rh for rh in resp.repo_health}
    # Scanned primary — real metrics.
    assert by_slug["gamexcore"].bugs == 2
    assert by_slug["gamexcore"].fetched_at is not None
    # Unscanned sibling — real identity, null metrics, null freshness + url.
    unscanned = by_slug["gamexsdk"]
    assert unscanned.repo_id == repos["gamexsdk"].id
    assert unscanned.repo_name == "gamexsdk"
    assert unscanned.is_primary is False
    assert unscanned.project_key == "GameX-gamexsdk"
    assert unscanned.quality_gate_status is None
    assert unscanned.bugs is None
    assert unscanned.ncloc is None
    assert unscanned.fetched_at is None
    assert unscanned.dashboard_url is None
    assert _SECRET not in str(resp.model_dump())


async def test_repo_health_single_repo_unchanged(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-252: a single-repo (primary, scanned) board → exactly 1 scanned entry.

    Guards the production single-repo boards (PH/KIM/FN): the unscanned `else` branch
    never fires, so repo_health is byte-identical to today, and `resp.health` still
    mirrors the primary metric.
    """
    core = os.path.join(str(tmp_path), "kims")
    _touch(os.path.join(core, "app", "main.py"))
    board, _ = await _seed_multirepo_board(
        mem_session, key="KIM", project_key="kims", primary_local_path=core
    )
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    repos = {r.slug: r for r in board.repositories}
    with (
        p1,
        p2,
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(5))
        ),
    ):
        await sonarqube.poll_repo(mem_session, board, repos["gamexcore"], "kims")

    fresh_factory = async_sessionmaker(mem_session.bind, expire_on_commit=False)
    async with fresh_factory() as fresh:
        refreshed = (
            await fresh.execute(
                select(Board)
                .where(Board.id == board.id)
                .options(
                    selectinload(Board.workflow),
                    selectinload(Board.repositories),
                    selectinload(Board.sonarqube_metrics).selectinload(
                        SonarQubeMetric.repository
                    ),
                )
            )
        ).scalar_one()
        with p1:
            resp = board_response(refreshed)

    # Exactly 1 entry, scanned, primary-badged — unchanged from today.
    assert len(resp.repo_health) == 1
    only = resp.repo_health[0]
    assert only.repo_slug == "gamexcore"
    assert only.is_primary is True
    assert only.bugs == 5
    assert only.fetched_at is not None
    assert only.quality_gate_status == "OK"
    # health (separate primary_sonarqube_metric path) mirrors the metric byte-identical.
    assert resp.health is not None
    assert resp.health.bugs == only.bugs
    assert resp.health.quality_gate_status == only.quality_gate_status
    assert resp.health.ncloc == only.ncloc


async def test_repo_health_repoless_aggregate_fallback(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-252: a legacy repo-less board (a repo_id IS NULL aggregate metric, NO
    Repository rows) still emits the aggregate row via the `not board.repositories`
    fallback branch — freshest-first, exactly as before this change."""
    # Seed a bare board with NO repositories and one board-level aggregate metric.
    workflow = Workflow(
        name=f"wf-LEG-{uuid4().hex[:6]}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    mem_session.add(workflow)
    await mem_session.flush()
    actor = Actor(kind="human", display_name="A", token_hash="x", is_active=True)
    mem_session.add(actor)
    await mem_session.flush()
    board = Board(
        key="LEG",
        name="LEG",
        description="legacy repo-less board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=actor.id,
        sonarqube_project_key="legacy",
    )
    mem_session.add(board)
    await mem_session.flush()
    mem_session.add(
        SonarQubeMetric(
            board_id=board.id,
            repo_id=None,  # aggregate, no linked Repository
            project_key="legacy",
            quality_gate_status="OK",
            bugs=4,
            vulnerabilities=0,
            code_smells=2,
            coverage=Decimal("70.00"),
            duplicated_lines_density=Decimal("1.50"),
            ncloc=500,
            fetched_at=datetime(2026, 6, 10, tzinfo=UTC),
        )
    )
    await mem_session.commit()

    s = _settings(root=str(tmp_path))
    p1, _, _ = _patch_all(s)
    fresh_factory = async_sessionmaker(mem_session.bind, expire_on_commit=False)
    async with fresh_factory() as fresh:
        refreshed = (
            await fresh.execute(
                select(Board)
                .where(Board.id == board.id)
                .options(
                    selectinload(Board.workflow),
                    selectinload(Board.repositories),
                    selectinload(Board.sonarqube_metrics).selectinload(
                        SonarQubeMetric.repository
                    ),
                )
            )
        ).scalar_one()
        with p1:
            resp = board_response(refreshed)

    # The aggregate row is still emitted via the fallback branch (no Repository rows).
    assert len(resp.repo_health) == 1
    agg = resp.repo_health[0]
    assert agg.repo_id is None
    assert agg.repo_slug is None  # aggregate identity
    assert agg.is_primary is False
    assert agg.bugs == 4
    assert agg.fetched_at is not None  # scanned aggregate keeps its freshness
    assert _SECRET not in str(resp.model_dump())


def test_repo_health_is_primary_primary_sibling_aggregate() -> None:
    """PH-251: serializer maps repository.is_primary onto RepoHealth.is_primary —
    True for the primary repo's metric, False for a non-primary repo, and False for a
    legacy board-level aggregate row (repo_id IS NULL → metric.repository is None)."""
    from app.services.serializers import repo_health

    def _metric(repo: Repository | None, *, project_key: str) -> SonarQubeMetric:
        m = MagicMock(spec=SonarQubeMetric)
        m.repository = repo
        m.repo_id = repo.id if repo is not None else None
        m.project_key = project_key
        m.quality_gate_status = "OK"
        m.bugs = 0
        m.vulnerabilities = 0
        m.code_smells = 0
        m.coverage = Decimal("90.00")
        m.duplicated_lines_density = Decimal("0.00")
        m.ncloc = 10
        m.fetched_at = datetime(2026, 6, 10, tzinfo=UTC)
        return m

    primary = _repo("gamexcore", is_primary=True)
    sibling = _repo("gamexsdk", is_primary=False)

    # repo_health calls _dashboard_url → sonarqube.get_settings; patch it (p1).
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        assert repo_health(_metric(primary, project_key="GameX")).is_primary is True
        assert (
            repo_health(_metric(sibling, project_key="GameX-gamexsdk")).is_primary
            is False
        )
        # Legacy board-level aggregate row: repo_id IS NULL → no repository → False.
        assert (
            repo_health(_metric(None, project_key="GameX")).is_primary is False
        )


# ===========================================================================
# Endpoints — KEPT single /scan-plan + NEW /scan-plans list
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


async def test_endpoint_scan_plans_list_three_elements(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: NEW GET /scan-plans returns list[SonarScanPlanResponse] (7 frozen + repo fields)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    demo = os.path.join(str(tmp_path), "gamexandroiddemoapp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    _touch(os.path.join(demo, "src", "Demo.kt"))
    board, admin = await _seed_multirepo_board(
        mem_session,
        primary_local_path=core,
        sibling_specs=[("gamexsdk", sdk), ("gamexandroiddemoapp", demo)],
    )
    client = _make_client(admin, mem_session)
    s = _settings(root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/scan-plans")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) == 3
    frozen = {
        "project_key",
        "container_source",
        "host_source",
        "language",
        "supported",
        "reason",
        "exclusions",
    }
    for element in data:
        assert frozen <= set(element.keys())
        assert "repo_id" in element and "repo_slug" in element
    assert {e["project_key"] for e in data} == {
        "GameX",
        "GameX-gamexsdk",
        "GameX-gamexandroiddemoapp",
    }
    assert _SECRET not in resp.text


async def test_endpoint_single_scan_plan_kept_primary(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: the EXISTING single-object /scan-plan is PRESERVED (primary repo, back-compat)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_multirepo_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    client = _make_client(admin, mem_session)
    s = _settings(root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/scan-plan")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    # single OBJECT (not a list) = the PRIMARY repo's plan.
    assert isinstance(data, dict)
    assert data["project_key"] == "GameX"
    assert data["container_source"] == core
    assert data["repo_slug"] == "gamexcore"


# ===========================================================================
# Migration backfill — driven on a REAL sqlite engine (KIM-snapshot shape)
# ===========================================================================


def _load_migration():
    base = "app/db/migrations/versions/"
    spec = importlib.util.spec_from_file_location(
        "ph246_mig", base + "b1468dc15870_ph_246_per_repo_sonar_project_keys.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_backfill_and_relaxed_constraint_on_sqlite() -> None:
    """AC: migration backfills primary key from the legacy board col + relaxes the metric
    unique constraint to (board_id, repo_id), on a REAL sqlite engine (KIM-snapshot path).
    """
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        # PRE-246 schema slice the migration touches.
        conn.execute(
            sa.text(
                "CREATE TABLE boards (id TEXT PRIMARY KEY, key TEXT, "
                "sonarqube_project_key TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE repositories (id TEXT PRIMARY KEY, board_id TEXT, "
                "slug TEXT, is_primary INTEGER)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE sonarqube_metrics (id TEXT PRIMARY KEY, board_id TEXT, "
                "project_key TEXT, CONSTRAINT uq_sonarqube_metric_board "
                "UNIQUE (board_id))"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE sonar_scan_jobs (id TEXT PRIMARY KEY, board_id TEXT, "
                "project_key TEXT)"
            )
        )
        # GXA (3 repos, primary gamexcore key=GameX) + KIM (no repos).
        conn.execute(
            sa.text(
                "INSERT INTO boards VALUES ('b-gxa','GXA','GameX'),('b-kim','KIM','kims')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO repositories VALUES "
                "('r1','b-gxa','gamexcore',1),('r2','b-gxa','gamexsdk',0),"
                "('r3','b-gxa','gamexandroiddemoapp',0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO sonarqube_metrics VALUES "
                "('m1','b-gxa','GameX'),('m2','b-kim','kims')"
            )
        )
        conn.execute(
            sa.text("INSERT INTO sonar_scan_jobs VALUES ('j1','b-gxa','GameX')")
        )
        conn.commit()

        ctx = MigrationContext.configure(conn)
        assert type(ctx.impl).__name__ == "SQLiteImpl"
        with Operations.context(ctx):
            mig.upgrade()
        conn.commit()

        # Primary repo inherited the board key verbatim; siblings stay NULL.
        repo_keys = dict(
            conn.execute(
                sa.text("SELECT slug, sonarqube_project_key FROM repositories")
            ).all()
        )
        assert repo_keys["gamexcore"] == "GameX"  # no rename
        assert repo_keys["gamexsdk"] is None
        assert repo_keys["gamexandroiddemoapp"] is None

        # Metric repo_id backfilled to the primary repo (KIM has no primary → NULL).
        metric_repo = dict(
            conn.execute(
                sa.text("SELECT board_id, repo_id FROM sonarqube_metrics")
            ).all()
        )
        assert metric_repo["b-gxa"] == "r1"
        assert metric_repo["b-kim"] is None

        # Scan job repo_id + repo_slug backfilled.
        job = conn.execute(
            sa.text("SELECT repo_id, repo_slug FROM sonar_scan_jobs")
        ).one()
        assert job == ("r1", "gamexcore")

        # Relaxed constraint: a 2nd metric for the SAME board, DIFFERENT repo is allowed.
        conn.execute(
            sa.text(
                "INSERT INTO sonarqube_metrics (id, board_id, repo_id, project_key) "
                "VALUES ('m3','b-gxa','r2','GameX-gamexsdk')"
            )
        )
        conn.commit()
        n = conn.execute(
            sa.text("SELECT COUNT(*) FROM sonarqube_metrics WHERE board_id='b-gxa'")
        ).scalar_one()
        assert n == 2

        # A duplicate (board_id, repo_id) is still rejected (constraint really applied).
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO sonarqube_metrics (id, board_id, repo_id, project_key) "
                    "VALUES ('m4','b-gxa','r1','GameX')"
                )
            )
        conn.rollback()

        # Downgrade reverses (drop the extra sibling row first so the single-board uq
        # can be restored).
        conn.execute(sa.text("DELETE FROM sonarqube_metrics WHERE id='m3'"))
        conn.commit()
        with Operations.context(ctx):
            mig.downgrade()
        conn.commit()
        cols = [
            r[1]
            for r in conn.execute(sa.text("PRAGMA table_info(sonarqube_metrics)")).all()
        ]
        assert "repo_id" not in cols
