"""PH-266 — git graph topological ordering guard.

The DAG payload served by ``graph_payload`` was previously ordered by
``committed_at DESC, sha DESC``. That ordering is NOT topological: on real
history a first parent can share/invert a child's timestamp and therefore sort
ABOVE its child. The frontend lane layout then isolates the inverted commit into
its own single-row span and can never inherit the "merged" classification,
rendering a fully-merged branch tip as a false "open/unmerged" ring (the
user-visible bug — real commit 945e4cd4 on board PH).

These tests pin the fixed contract:
  - For every returned first-parent edge whose parent is IN-WINDOW, the parent's
    index is GREATER than the child's index (parent appears AFTER/below child).
    0 violations.
  - The default-branch head is ``commits[0]``.
  - Full-set-then-truncate: with a small ``limit`` the returned set is the
    topologically-newest ``limit`` commits (parent-after-child holds within the
    truncated window), NOT an arbitrary committed_at slice.

Fixture strategy mirrors ``test_git_read_api.py``: in-memory SQLite, ORM rows
seeded directly, ``graph_payload`` called as a pure service function (no HTTP).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    GitBranch,
    GitCommit,
    Repository,
    Workflow,
)
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.git_queries import graph_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


def _sha(prefix: str, n: int = 40) -> str:
    return (prefix + "0" * n)[:n]


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


async def _seed_base(mem_session: AsyncSession) -> tuple[Board, Repository, Actor]:
    """Seed an empty board + primary repo and return (board, repo, admin)."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="TopoAdmin", token_hash="t", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="TOPO",
        name="Topo Order Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
    await mem_session.flush()

    repo = Repository(
        slug="topo-test",
        name="topo-test",
        is_primary=True,
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/topo-test",
        provider="local",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.flush()
    return board, repo, admin


def _commit(
    repo_id: uuid.UUID,
    sha: str,
    parents: list[str],
    committed_at: datetime,
) -> GitCommit:
    return GitCommit(
        repo_id=repo_id,
        sha=sha,
        short_sha=sha[:8],
        parents=parents,
        author_name="X",
        author_email="x@x",
        authored_at=committed_at,
        committer_name="X",
        committer_email="x@x",
        committed_at=committed_at,
        summary="c",
        body="",
        is_conventional=False,
        commit_type=None,
        ticket_keys=[],
    )


async def _reload_board(mem_session: AsyncSession, board_id: uuid.UUID) -> Board:
    return (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board_id)
            .options(selectinload(Board.workflow), selectinload(Board.repositories))
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Inversion fixture: reproduce the real bug shape
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_inversion(mem_session: AsyncSession) -> dict[str, Any]:
    """Reproduce the PH-266 bug shape.

    DAG (main is the default branch):

        ROOT  <- BASE  <- MERGE (main HEAD)
                   ^
                  TIP   (side-branch tip; first parent of BASE)

    BASE has parents [ROOT, TIP] (a merge: TIP merged into the line). TIP's
    ``committed_at`` is set EQUAL to BASE's and TIP's sha sorts ABOVE BASE's, so
    under the legacy ``committed_at DESC, sha DESC`` ordering TIP (the PARENT)
    appears ABOVE BASE (its CHILD) — the exact inversion that isolated the merged
    tip into its own span. A correct topological order must place BASE before
    TIP.
    """
    board, repo, admin = await _seed_base(mem_session)

    sha_root = _sha("a000")
    sha_tip = _sha("ffff")   # high sha → sorts above BASE on the timestamp tie
    sha_base = _sha("b000")
    sha_merge = _sha("c000")

    t = _utc(2026, 1, 1, 10, 0)

    root = _commit(repo.id, sha_root, [], _utc(2026, 1, 1, 9, 0))
    tip = _commit(repo.id, sha_tip, [sha_root], t)          # parent of base
    base = _commit(repo.id, sha_base, [sha_tip, sha_root], t)  # child; same ts as tip
    merge = _commit(repo.id, sha_merge, [sha_base], _utc(2026, 1, 1, 11, 0))
    mem_session.add_all([root, tip, base, merge])
    await mem_session.flush()

    mem_session.add(
        GitBranch(
            repo_id=repo.id,
            name="main",
            head_sha=sha_merge,
            is_default=True,
            ticket_key=None,
        )
    )
    await mem_session.commit()

    return {
        "board": await _reload_board(mem_session, board.id),
        "session": mem_session,
        "sha_root": sha_root,
        "sha_tip": sha_tip,
        "sha_base": sha_base,
        "sha_merge": sha_merge,
    }


def _first_parent_violations(commits: list[Any]) -> list[tuple[str, str]]:
    """Return (child_sha, parent_sha) pairs where the FIRST parent (in-window)
    appears ABOVE/BEFORE its child — i.e. a topological violation."""
    index = {c.sha: i for i, c in enumerate(commits)}
    violations: list[tuple[str, str]] = []
    for c in commits:
        if not c.parents:
            continue
        first_parent = c.parents[0]
        if first_parent in index and index[first_parent] < index[c.sha]:
            violations.append((c.sha, first_parent))
    return violations


def _all_parent_violations(commits: list[Any]) -> list[tuple[str, str]]:
    """Return (child_sha, parent_sha) pairs for ANY in-window parent edge that is
    topologically inverted (parent before child)."""
    index = {c.sha: i for i, c in enumerate(commits)}
    violations: list[tuple[str, str]] = []
    for c in commits:
        for parent in c.parents:
            if parent in index and index[parent] < index[c.sha]:
                violations.append((c.sha, parent))
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_parent_edges_have_zero_topo_violations(
    seeded_inversion: dict[str, Any],
) -> None:
    """Every returned first-parent edge (parent in-window) has the parent AFTER
    the child. 0 violations — the legacy ordering produced 1 on this shape."""
    resp = await graph_payload(
        seeded_inversion["session"], seeded_inversion["board"], limit=200
    )
    fp_violations = _first_parent_violations(resp.commits)
    assert fp_violations == [], f"first-parent topo violations: {fp_violations}"
    # And no ANY-parent inversion either (the merge's second parent included).
    all_violations = _all_parent_violations(resp.commits)
    assert all_violations == [], f"any-parent topo violations: {all_violations}"


@pytest.mark.asyncio
async def test_default_head_is_first(seeded_inversion: dict[str, Any]) -> None:
    """The default-branch head (main HEAD = MERGE) is commits[0] so the frontend
    assignLanes lane-0 seed still anchors main."""
    resp = await graph_payload(
        seeded_inversion["session"], seeded_inversion["board"], limit=200
    )
    assert resp.commits[0].sha == seeded_inversion["sha_merge"]


@pytest.mark.asyncio
async def test_base_appears_before_its_parent_tip(
    seeded_inversion: dict[str, Any],
) -> None:
    """Direct assertion of the fixed inversion: BASE (child) precedes TIP (its
    first parent) even though they share committed_at and TIP's sha sorts higher
    (which is exactly what the legacy DESC ordering got wrong)."""
    resp = await graph_payload(
        seeded_inversion["session"], seeded_inversion["board"], limit=200
    )
    order = [c.sha for c in resp.commits]
    assert order.index(seeded_inversion["sha_base"]) < order.index(
        seeded_inversion["sha_tip"]
    )


@pytest.mark.asyncio
async def test_full_set_then_truncate(mem_session: AsyncSession) -> None:
    """With a small ``limit`` the returned set is the topologically-NEWEST
    ``limit`` commits (full-set-then-truncate), NOT an arbitrary committed_at
    slice. Parent-after-child must hold WITHIN the truncated window, and the
    truncated head must remain the default head.

    Chain (linear, default branch main): c5 -> c4 -> c3 -> c2 -> c1 (newest ->
    oldest by committed_at). We additionally invert c2/c3 timestamps so a naive
    truncate-then-sort window would drop the wrong commit; the topo order must
    still yield the 3 newest in child-before-parent order.
    """
    board, repo, _admin = await _seed_base(mem_session)

    shas = [_sha(f"e{i}") for i in range(1, 6)]  # e1..e5
    # parents: each points at the previous (e1 is root).
    # committed_at: mostly increasing, but e3 (index 2) and e2 (index 1) are tied
    # at the same minute with e3.sha > e2.sha to stress the tie-break.
    times = [
        _utc(2026, 2, 1, 8),   # e1 root
        _utc(2026, 2, 1, 9),   # e2
        _utc(2026, 2, 1, 9),   # e3 (tie with e2; e3 sha sorts above e2)
        _utc(2026, 2, 1, 10),  # e4
        _utc(2026, 2, 1, 11),  # e5 (HEAD)
    ]
    commits = []
    for i, (sha, ts) in enumerate(zip(shas, times, strict=True)):
        parents = [shas[i - 1]] if i > 0 else []
        commits.append(_commit(repo.id, sha, parents, ts))
    mem_session.add_all(commits)
    await mem_session.flush()

    mem_session.add(
        GitBranch(
            repo_id=repo.id,
            name="main",
            head_sha=shas[4],  # e5
            is_default=True,
            ticket_key=None,
        )
    )
    await mem_session.commit()

    board = await _reload_board(mem_session, board.id)

    resp = await graph_payload(mem_session, board, limit=3)
    returned = [c.sha for c in resp.commits]

    # Exactly 3 returned (truncated).
    assert len(returned) == 3
    # The 3 topologically-newest commits are e5, e4, e3 (child-before-parent).
    assert returned == [shas[4], shas[3], shas[2]]
    # Default head is first.
    assert returned[0] == shas[4]
    # Parent-after-child within the truncated window (no inversion).
    assert _first_parent_violations(resp.commits) == []


@pytest.mark.asyncio
async def test_empty_repo_returns_empty(mem_session: AsyncSession) -> None:
    """No commits → empty commits list (topo sort of [] is [])."""
    board, _repo, _admin = await _seed_base(mem_session)
    await mem_session.commit()
    board = await _reload_board(mem_session, board.id)

    resp = await graph_payload(mem_session, board, limit=50)
    assert resp.commits == []
