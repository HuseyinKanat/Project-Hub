"""G4 git read API tests — PH-153.

Covers:
  AC1  GET /git/graph shape (commits + branches + tags=[])
  AC2  GET /git/graph with branches filter
  AC3  GET /git/branches ahead/behind
  AC3a ahead/behind overflow → null
  AC4  GET /git/commits pagination (limit + before cursor)
  AC4a branch filter
  AC4b path filter
  AC5  GET /git/commits/{sha} full detail
  AC5a short sha lookup
  AC5b ambiguous short sha → 404
  AC5c unknown sha → 404
  AC6  not a board member → 403
  AC7  unknown board_key → 404
  AC8  no Repository row → 409 repo_not_configured
  AC9  empty repo (no commits/branches) → 200 empty arrays

Fixture strategy:
  - In-memory SQLite (aiosqlite) per-test via ``mem_session``.
  - ORM rows seeded directly (no git subprocess needed — shape checks only).
  - FastAPI DI overrides for current_actor + get_db_session.
  - TestClient (sync) wrapping the async app.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    GitBranch,
    GitCommit,
    GitCommitFile,
    GitCommitTicket,
    Repository,
    Ticket,
    Workflow,
)
from app.db.session import get_db_session
from app.git.reader import DiffResult
from app.git.reader import FileDiff as ReaderFileDiff
from app.main import app
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


def _sha(prefix: str, n: int = 40) -> str:
    """Pad prefix to 40 chars with zeros."""
    return (prefix + "0" * n)[:n]


# ---------------------------------------------------------------------------
# Session fixture
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


# ---------------------------------------------------------------------------
# Seed fixture: board + repo + commits + branches
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded(mem_session: AsyncSession) -> dict[str, Any]:
    """Seed a board with:
    - admin (board admin)
    - member (board_dev)
    - outsider (no membership)
    - Repository row
    - 3 commits on main (sha_a → sha_b → sha_c, newest first by committed_at)
    - 2 commits on feature/x: sha_b + sha_d (sha_d diverges from sha_a)
    - 2 branches: main (head=sha_c, default), feature/x (head=sha_d)
    - 2 files for sha_c, 0 files for others (except sha_b has 1 file)
    """
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
        key="GT",
        name="Git Read Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add_all([
        BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
        BoardMembership(board_id=board.id, actor_id=member.id, role="backend_dev"),
    ])
    await mem_session.flush()

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/git-read-test",
        provider="local",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.flush()

    # SHAs: deterministic 40-hex strings
    sha_a = _sha("aaa")  # root commit (oldest)
    sha_b = _sha("bbb")  # second commit (main)
    sha_c = _sha("ccc")  # third commit (main HEAD)
    sha_d = _sha("ddd")  # feature/x HEAD (diverges from sha_a)

    commit_a = GitCommit(
        repo_id=repo.id,
        sha=sha_a,
        short_sha="aaaaaaaa",
        parents=[],
        author_name="Alice",
        author_email="alice@test.local",
        authored_at=_utc(2026, 1, 1),
        committer_name="Alice",
        committer_email="alice@test.local",
        committed_at=_utc(2026, 1, 1),
        summary="initial commit",
        body="",
        is_conventional=False,
        commit_type=None,
        ticket_keys=[],
    )
    commit_b = GitCommit(
        repo_id=repo.id,
        sha=sha_b,
        short_sha="bbbbbbbb",
        parents=[sha_a],
        author_name="Bob",
        author_email="bob@test.local",
        authored_at=_utc(2026, 1, 2),
        committer_name="Bob",
        committer_email="bob@test.local",
        committed_at=_utc(2026, 1, 2),
        summary="feat(GT-1): add feature",
        body="",
        is_conventional=True,
        commit_type="feat",
        ticket_keys=["GT-1"],
    )
    commit_c = GitCommit(
        repo_id=repo.id,
        sha=sha_c,
        short_sha="cccccccc",
        parents=[sha_b],
        author_name="Alice",
        author_email="alice@test.local",
        authored_at=_utc(2026, 1, 3),
        committer_name="Alice",
        committer_email="alice@test.local",
        committed_at=_utc(2026, 1, 3),
        summary="fix(GT-2): fix bug",
        body="",
        is_conventional=True,
        commit_type="fix",
        ticket_keys=["GT-2"],
    )
    commit_d = GitCommit(
        repo_id=repo.id,
        sha=sha_d,
        short_sha="dddddddd",
        parents=[sha_a],  # diverges from sha_a (shares base with main)
        author_name="Carol",
        author_email="carol@test.local",
        authored_at=_utc(2026, 1, 4),
        committer_name="Carol",
        committer_email="carol@test.local",
        committed_at=_utc(2026, 1, 4),
        summary="feat(GT-3): feature branch work",
        body="Feature branch body.",
        is_conventional=True,
        commit_type="feat",
        ticket_keys=["GT-3"],
    )
    mem_session.add_all([commit_a, commit_b, commit_c, commit_d])
    await mem_session.flush()

    # Files: commit_c has 2 files; commit_b has 1 file touching reader.py
    file_c1 = GitCommitFile(
        commit_id=commit_c.id,
        path="backend/app/core/config.py",
        old_path=None,
        change_type="M",
        additions=5,
        deletions=2,
        is_binary=False,
    )
    file_c2 = GitCommitFile(
        commit_id=commit_c.id,
        path="backend/app/core/exceptions.py",
        old_path=None,
        change_type="A",
        additions=20,
        deletions=0,
        is_binary=False,
    )
    file_b1 = GitCommitFile(
        commit_id=commit_b.id,
        path="backend/app/git/reader.py",
        old_path=None,
        change_type="A",
        additions=100,
        deletions=0,
        is_binary=False,
    )
    mem_session.add_all([file_c1, file_c2, file_b1])
    await mem_session.flush()

    # Branches
    branch_main = GitBranch(
        repo_id=repo.id,
        name="main",
        head_sha=sha_c,
        is_default=True,
        ticket_key=None,
    )
    # feature/x is 1 ahead (sha_d) and 2 behind (sha_b, sha_c) relative to main
    branch_feature = GitBranch(
        repo_id=repo.id,
        name="feature/x",
        head_sha=sha_d,
        is_default=False,
        ticket_key="GT-3",
    )
    mem_session.add_all([branch_main, branch_feature])
    await mem_session.commit()

    # Reload board with relationships
    refreshed_board = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repository))
        )
    ).scalar_one()

    return {
        "board": refreshed_board,
        "admin": admin,
        "member": member,
        "outsider": outsider,
        "repo": repo,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "sha_c": sha_c,
        "sha_d": sha_d,
        "session": mem_session,
    }


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def make_client(actor: Actor, session: AsyncSession) -> TestClient:
    async def _fake_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# AC1 — GET /git/graph shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_git_graph_shape(seeded: dict[str, Any]) -> None:
    """Response has commits[], branches[], tags=[]; commit has required fields."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/graph?limit=50")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "commits" in data
        assert "branches" in data
        assert data["tags"] == []

        # At least one commit
        assert len(data["commits"]) > 0
        c = data["commits"][0]
        assert "sha" in c
        assert "short_sha" in c
        assert "parents" in c
        assert "author_name" in c
        assert "author_email" in c
        assert "committed_at" in c
        assert "summary" in c
        assert "is_conventional" in c
        assert "commit_type" in c
        assert "ticket_keys" in c
        assert "refs" in c

        # sha_c should appear with refs=["main"] (most recent commit, head of main)
        sha_c_entries = [e for e in data["commits"] if e["sha"] == seeded["sha_c"]]
        assert len(sha_c_entries) == 1
        assert "main" in sha_c_entries[0]["refs"]

        # sha_c's parents should include sha_b
        assert seeded["sha_b"] in sha_c_entries[0]["parents"]

        # branch entry shape
        assert len(data["branches"]) > 0
        b = data["branches"][0]
        assert "name" in b
        assert "head_sha" in b
        assert "is_default" in b
        assert "ticket_key" in b
        assert "ahead" in b
        assert "behind" in b
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC2 — GET /git/graph branches filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_git_graph_branch_filter(seeded: dict[str, Any]) -> None:
    """branches=main filter returns only main branch in branches[]."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/graph?branches=main&limit=200")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Only main branch in branches[]
        branch_names = [b["name"] for b in data["branches"]]
        assert "main" in branch_names
        assert "feature/x" not in branch_names
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC3 — GET /git/branches ahead/behind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_git_branches_ahead_behind(seeded: dict[str, Any]) -> None:
    """feature/x is 1 ahead and 2 behind main; main has 0/0."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/branches")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        branches = {b["name"]: b for b in data["branches"]}

        assert "main" in branches
        assert branches["main"]["ahead"] == 0
        assert branches["main"]["behind"] == 0
        assert branches["main"]["is_default"] is True

        assert "feature/x" in branches
        fx = branches["feature/x"]
        assert fx["is_default"] is False
        assert fx["ticket_key"] == "GT-3"
        # feature/x has sha_d (ahead by 1: sha_d not in main)
        # behind by 2: sha_b and sha_c in main but not in feature/x
        assert fx["ahead"] == 1
        assert fx["behind"] == 2
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC3a — ahead/behind overflow → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3a_ahead_behind_overflow(
    seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BFS limit is 1, ahead/behind returns None for non-default branch."""
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.get_settings(), "git_backfill_limit", 1)
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/branches")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        branches = {b["name"]: b for b in data["branches"]}

        # feature/x should overflow with limit=1
        fx = branches.get("feature/x")
        assert fx is not None
        # With limit=1 the BFS for either head will overflow
        assert fx["ahead"] is None
        assert fx["behind"] is None
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC4 — GET /git/commits pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4_commits_pagination(seeded: dict[str, Any]) -> None:
    """First page (limit=2) + second page via before cursor; no overlap."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        # First page: 2 most recent commits
        resp1 = client.get("/api/boards/GT/git/commits?limit=2")
        assert resp1.status_code == 200, resp1.text
        page1 = resp1.json()["commits"]
        assert len(page1) == 2

        # Commits ordered newest-first
        assert page1[0]["committed_at"] >= page1[1]["committed_at"]

        last_sha = page1[-1]["sha"]
        resp2 = client.get(f"/api/boards/GT/git/commits?limit=2&before={last_sha}")
        assert resp2.status_code == 200, resp2.text
        page2 = resp2.json()["commits"]

        # No overlap between pages
        shas_p1 = {c["sha"] for c in page1}
        shas_p2 = {c["sha"] for c in page2}
        assert shas_p1 & shas_p2 == set()

        # page2 commits are older
        for c in page2:
            assert c["committed_at"] <= page1[-1]["committed_at"]
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC4a — branch filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4a_commits_branch_filter(seeded: dict[str, Any]) -> None:
    """branch=feature/x returns commits reachable from that branch."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/commits?branch=feature/x&limit=100")
        assert resp.status_code == 200, resp.text
        commits = resp.json()["commits"]

        shas = {c["sha"] for c in commits}
        # sha_d and sha_a are reachable from feature/x (sha_d → sha_a)
        assert seeded["sha_d"] in shas
        assert seeded["sha_a"] in shas
        # sha_c and sha_b are NOT reachable from feature/x
        assert seeded["sha_c"] not in shas
        assert seeded["sha_b"] not in shas
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC4b — path filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4b_commits_path_filter(seeded: dict[str, Any]) -> None:
    """path=backend/app/git/reader.py returns only commit_b."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get(
            "/api/boards/GT/git/commits?path=backend/app/git/reader.py&limit=100"
        )
        assert resp.status_code == 200, resp.text
        commits = resp.json()["commits"]
        assert len(commits) == 1
        assert commits[0]["sha"] == seeded["sha_b"]
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC5 — GET /git/commits/{sha} full detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5_commit_detail(seeded: dict[str, Any]) -> None:
    """Full detail for sha_c: 2 files, correct fields."""
    sha_c = seeded["sha_c"]
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get(f"/api/boards/GT/git/commits/{sha_c}")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["sha"] == sha_c
        assert "short_sha" in data
        assert "parents" in data
        assert "author_name" in data
        assert "author_email" in data
        assert "committer_name" in data
        assert "committer_email" in data
        assert "body" in data
        assert "files" in data
        assert len(data["files"]) == 2

        file_paths = {f["path"] for f in data["files"]}
        assert "backend/app/core/config.py" in file_paths
        assert "backend/app/core/exceptions.py" in file_paths

        for f in data["files"]:
            assert "change_type" in f
            assert "additions" in f
            assert "deletions" in f
            assert "is_binary" in f
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC5a — short sha lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5a_short_sha(seeded: dict[str, Any]) -> None:
    """First 8 chars of sha_c resolves to the full commit."""
    sha_c = seeded["sha_c"]
    short = sha_c[:8]
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get(f"/api/boards/GT/git/commits/{short}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["sha"] == sha_c
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC5b — ambiguous short sha → 404
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_collision(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with 2 commits whose shas share the first 4 chars: aaaa1... and aaaa2..."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin2", token_hash="y", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="COL",
        name="Collision Board",
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
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/collision-test",
        provider="local",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.flush()

    # Two commits whose sha starts with "aaaa"
    sha1 = "aaaa1" + "0" * 35
    sha2 = "aaaa2" + "0" * 35
    ts = _utc(2026, 2, 1)

    for sha in (sha1, sha2):
        c = GitCommit(
            repo_id=repo.id,
            sha=sha,
            short_sha=sha[:8],
            parents=[],
            author_name="X",
            author_email="x@x",
            authored_at=ts,
            committer_name="X",
            committer_email="x@x",
            committed_at=ts,
            summary="collision test",
            body="",
            is_conventional=False,
            commit_type=None,
            ticket_keys=[],
        )
        mem_session.add(c)

    await mem_session.commit()

    refreshed_board = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repository))
        )
    ).scalar_one()

    return {"board": refreshed_board, "admin": admin, "session": mem_session}


@pytest.mark.asyncio
async def test_ac5b_ambiguous_short_sha(seeded_collision: dict[str, Any]) -> None:
    """Prefix 'aaaa' matches 2 commits → 404."""
    client = make_client(seeded_collision["admin"], seeded_collision["session"])
    try:
        resp = client.get("/api/boards/COL/git/commits/aaaa")
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "not_found"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC5c — unknown sha → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5c_unknown_sha(seeded: dict[str, Any]) -> None:
    """All-zeros sha returns 404."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/commits/" + "0" * 40)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"] == "not_found"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC6 — not a board member → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac6_not_member_403(seeded: dict[str, Any]) -> None:
    """Outsider gets 403 on all 4 endpoints."""
    outsider = seeded["outsider"]
    session = seeded["session"]

    endpoints = [
        "/api/boards/GT/git/graph",
        "/api/boards/GT/git/branches",
        "/api/boards/GT/git/commits",
        f"/api/boards/GT/git/commits/{seeded['sha_c']}",
    ]
    for url in endpoints:
        client = make_client(outsider, session)
        try:
            resp = client.get(url)
            assert resp.status_code == 403, f"Expected 403 on {url}, got {resp.status_code}"
            assert resp.json()["error"] == "permission_denied"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC7 — unknown board_key → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac7_unknown_board_404(seeded: dict[str, Any]) -> None:
    """Unknown board_key ZZZ returns 404 on all 4 endpoints."""
    session = seeded["session"]
    member = seeded["member"]

    endpoints = [
        "/api/boards/ZZZ/git/graph",
        "/api/boards/ZZZ/git/branches",
        "/api/boards/ZZZ/git/commits",
        "/api/boards/ZZZ/git/commits/" + "a" * 40,
    ]
    for url in endpoints:
        client = make_client(member, session)
        try:
            resp = client.get(url)
            assert resp.status_code == 404, f"Expected 404 on {url}, got {resp.status_code}"
            assert resp.json()["error"] == "not_found"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC8 — no Repository row → 409
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def board_no_repo(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with member but no Repository row."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin3", token_hash="z", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="NR",
        name="No Repo Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
    await mem_session.commit()

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repository))
        )
    ).scalar_one()

    return {"board": refreshed, "admin": admin, "session": mem_session}


@pytest.mark.asyncio
async def test_ac8_no_repo_409(board_no_repo: dict[str, Any]) -> None:
    """All 4 endpoints return 409 repo_not_configured when no Repository row."""
    admin = board_no_repo["admin"]
    session = board_no_repo["session"]

    endpoints = [
        "/api/boards/NR/git/graph",
        "/api/boards/NR/git/branches",
        "/api/boards/NR/git/commits",
        "/api/boards/NR/git/commits/" + "a" * 40,
    ]
    for url in endpoints:
        client = make_client(admin, session)
        try:
            resp = client.get(url)
            assert resp.status_code == 409, f"Expected 409 on {url}, got {resp.status_code}"
            assert resp.json()["error"] == "repo_not_configured"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# AC9 — empty repo (no commits / branches) → 200 empty
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def empty_repo_board(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with Repository row but no git_commits / git_branches rows."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin4", token_hash="w", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="ER",
        name="Empty Repo Board",
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
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/empty-test",
        provider="local",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.commit()

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repository))
        )
    ).scalar_one()

    return {"board": refreshed, "admin": admin, "session": mem_session}


@pytest.mark.asyncio
async def test_ac9_empty_repo_200(empty_repo_board: dict[str, Any]) -> None:
    """Empty repo returns 200 with empty arrays — never 409."""
    admin = empty_repo_board["admin"]
    session = empty_repo_board["session"]

    client = make_client(admin, session)
    try:
        # graph
        r = client.get("/api/boards/ER/git/graph")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["commits"] == []
        assert data["branches"] == []
        assert data["tags"] == []
    finally:
        clear_overrides()

    client = make_client(admin, session)
    try:
        # branches
        r = client.get("/api/boards/ER/git/branches")
        assert r.status_code == 200, r.text
        assert r.json()["branches"] == []
    finally:
        clear_overrides()

    client = make_client(admin, session)
    try:
        # commits list
        r = client.get("/api/boards/ER/git/commits")
        assert r.status_code == 200, r.text
        assert r.json()["commits"] == []
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# AC5 extra: commits/{sha} with 0 files (commit_a)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_detail_zero_files(seeded: dict[str, Any]) -> None:
    """commit_a has 0 files — files[] is empty list, not error."""
    sha_a = seeded["sha_a"]
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get(f"/api/boards/GT/git/commits/{sha_a}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["sha"] == sha_a
        assert data["files"] == []
        assert data["parents"] == []  # root commit
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# Shape: commits list has refs populated correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commits_list_refs(seeded: dict[str, Any]) -> None:
    """sha_c (main HEAD) appears in commits list with refs=['main']."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/GT/git/commits?limit=10")
        assert resp.status_code == 200, resp.text
        commits = resp.json()["commits"]
        sha_c_entries = [c for c in commits if c["sha"] == seeded["sha_c"]]
        assert len(sha_c_entries) == 1
        assert "main" in sha_c_entries[0]["refs"]
    finally:
        clear_overrides()


# ===========================================================================
# G5 tests — diff API + ticket commits (PH-154)
# ===========================================================================

# ---------------------------------------------------------------------------
# G5 fixture: board with ticket + git_commit_tickets linkage
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_g5(mem_session: AsyncSession) -> dict[str, Any]:
    """Seed a board with:
    - admin (board admin)
    - member (backend_dev)
    - outsider (no membership)
    - Repository row
    - 2 commits + branches (reusing sha_a, sha_b from G4 style)
    - 1 Ticket with branch_name='ph-5-my-feature'
    - 2 git_commit_tickets linking both commits to the ticket
    - 2 GitCommitFile rows for sha_b (50 additions, 10 deletions)
    """
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="G5Admin", token_hash="g5a", is_active=True)
    member = Actor(kind="human", display_name="G5Member", token_hash="g5m", is_active=True)
    outsider = Actor(kind="human", display_name="G5Out", token_hash="g5o", is_active=True)
    mem_session.add_all([admin, member, outsider])
    await mem_session.flush()

    board = Board(
        key="G5",
        name="G5 Test Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add_all([
        BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
        BoardMembership(board_id=board.id, actor_id=member.id, role="backend_dev"),
    ])
    await mem_session.flush()

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/g5-test",
        provider="local",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.flush()

    sha_a = _sha("aaa")
    sha_b = _sha("bbb")

    commit_a = GitCommit(
        repo_id=repo.id,
        sha=sha_a,
        short_sha="aaaaaaaa",
        parents=[],
        author_name="Alice",
        author_email="a@test.local",
        authored_at=_utc(2026, 1, 1),
        committer_name="Alice",
        committer_email="a@test.local",
        committed_at=_utc(2026, 1, 1),
        summary="initial commit",
        body="",
        is_conventional=False,
        commit_type=None,
        ticket_keys=["G5-1"],
    )
    commit_b = GitCommit(
        repo_id=repo.id,
        sha=sha_b,
        short_sha="bbbbbbbb",
        parents=[sha_a],
        author_name="Bob",
        author_email="b@test.local",
        authored_at=_utc(2026, 1, 2),
        committer_name="Bob",
        committer_email="b@test.local",
        committed_at=_utc(2026, 1, 2),
        summary="feat(G5-1): add feature",
        body="",
        is_conventional=True,
        commit_type="feat",
        ticket_keys=["G5-1"],
    )
    mem_session.add_all([commit_a, commit_b])
    await mem_session.flush()

    # 2 files for commit_b
    file_b1 = GitCommitFile(
        commit_id=commit_b.id,
        path="app/feature.py",
        old_path=None,
        change_type="A",
        additions=50,
        deletions=0,
        is_binary=False,
    )
    file_b2 = GitCommitFile(
        commit_id=commit_b.id,
        path="app/utils.py",
        old_path=None,
        change_type="M",
        additions=0,
        deletions=10,
        is_binary=False,
    )
    mem_session.add_all([file_b1, file_b2])
    await mem_session.flush()

    branch_main = GitBranch(
        repo_id=repo.id,
        name="main",
        head_sha=sha_b,
        is_default=True,
        ticket_key=None,
    )
    mem_session.add(branch_main)
    await mem_session.flush()

    # Ticket
    ticket = Ticket(
        key="G5-1",
        board_id=board.id,
        type="feature",
        title="My G5 Feature",
        description="",
        state="in_progress",
        priority="medium",
        reporter_id=admin.id,
        branch_name="ph-5-my-feature",
    )
    mem_session.add(ticket)
    await mem_session.flush()

    # Link both commits to the ticket
    link_a = GitCommitTicket(commit_id=commit_a.id, ticket_id=ticket.id)
    link_b = GitCommitTicket(commit_id=commit_b.id, ticket_id=ticket.id)
    mem_session.add_all([link_a, link_b])
    await mem_session.commit()

    refreshed_board = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repository))
        )
    ).scalar_one()

    refreshed_ticket = (
        await mem_session.execute(
            select(Ticket).where(Ticket.id == ticket.id)
        )
    ).scalar_one()

    return {
        "board": refreshed_board,
        "admin": admin,
        "member": member,
        "outsider": outsider,
        "repo": repo,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "ticket": refreshed_ticket,
        "session": mem_session,
    }


# ---------------------------------------------------------------------------
# G5-AC8 — GET /api/tickets/{key}/commits shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac8_ticket_commits_shape(seeded_g5: dict[str, Any]) -> None:
    """Ticket commits returns correct shape with branch_name + commits list."""
    client = make_client(seeded_g5["member"], seeded_g5["session"])
    ticket = seeded_g5["ticket"]
    try:
        resp = client.get(f"/api/tickets/{ticket.key}/commits")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "branch_name" in data
        assert data["branch_name"] == "ph-5-my-feature"
        assert "commits" in data
        assert len(data["commits"]) == 2  # 2 linked commits

        # Newest-first ordering
        ts = [c["committed_at"] for c in data["commits"]]
        assert ts == sorted(ts, reverse=True)

        # Shape of each commit entry
        for c in data["commits"]:
            assert "sha" in c
            assert "short_sha" in c
            assert "summary" in c
            assert "authored_at" in c
            assert "committed_at" in c
            assert "author_name" in c
            assert "additions" in c
            assert "deletions" in c
            assert "files_changed" in c
            # No patch text inlined
            assert "patch" not in c
            assert "files" not in c

        # sha_b should have 2 files_changed, 50 additions, 10 deletions
        sha_b_entry = next(
            (c for c in data["commits"] if c["sha"] == seeded_g5["sha_b"]), None
        )
        assert sha_b_entry is not None
        assert sha_b_entry["files_changed"] == 2
        assert sha_b_entry["additions"] == 50
        assert sha_b_entry["deletions"] == 10
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_ac8_ticket_commits_zero_linkage(seeded: dict[str, Any]) -> None:
    """Ticket with no git_commit_tickets returns empty commits list (200, not 404)."""
    # Use admin from main seeded fixture; create a new ticket with no linkage
    session = seeded["session"]
    board = seeded["board"]

    # Create a ticket with no commit linkage
    ticket_no_commits = Ticket(
        key="GT-99",
        board_id=board.id,
        type="feature",
        title="No Commits Ticket",
        description="",
        state="backlog",
        priority="low",
        reporter_id=seeded["admin"].id,
        branch_name=None,
    )
    session.add(ticket_no_commits)
    await session.flush()
    await session.commit()

    client = make_client(seeded["admin"], session)
    try:
        resp = client.get(f"/api/tickets/{ticket_no_commits.key}/commits")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["commits"] == []
        assert data["branch_name"] is None
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC9 — 409 repo_not_configured for ticket commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac9_ticket_commits_no_repo_409(board_no_repo: dict[str, Any]) -> None:
    """Ticket on board without repo returns 409 repo_not_configured."""
    board = board_no_repo["board"]
    admin = board_no_repo["admin"]
    session = board_no_repo["session"]

    # Create a ticket on the no-repo board
    ticket = Ticket(
        key="NR-1",
        board_id=board.id,
        type="task",
        title="Task on no-repo board",
        description="",
        state="backlog",
        priority="low",
        reporter_id=admin.id,
        branch_name=None,
    )
    session.add(ticket)
    await session.flush()
    await session.commit()

    client = make_client(admin, session)
    try:
        resp = client.get(f"/api/tickets/{ticket.key}/commits")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "repo_not_configured"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC10 — Permission gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac10_ticket_commits_outsider_403(seeded_g5: dict[str, Any]) -> None:
    """Non-member gets 403 on ticket commits endpoint."""
    ticket = seeded_g5["ticket"]
    outsider = seeded_g5["outsider"]
    session = seeded_g5["session"]

    client = make_client(outsider, session)
    try:
        resp = client.get(f"/api/tickets/{ticket.key}/commits")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "permission_denied"
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_ac10_commit_diff_outsider_403(seeded: dict[str, Any]) -> None:
    """Non-member gets 403 on commit diff endpoint."""
    outsider = seeded["outsider"]
    session = seeded["session"]
    sha_c = seeded["sha_c"]

    client = make_client(outsider, session)
    try:
        resp = client.get(f"/api/boards/GT/git/commits/{sha_c}/diff")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "permission_denied"
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_ac10_range_diff_outsider_403(seeded: dict[str, Any]) -> None:
    """Non-member gets 403 on range diff endpoint."""
    outsider = seeded["outsider"]
    session = seeded["session"]

    client = make_client(outsider, session)
    try:
        resp = client.get("/api/boards/GT/git/diff?base=main&head=feature/x")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "permission_denied"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC9 — 409 for commit diff and range diff when no repo configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac9_commit_diff_no_repo_409(board_no_repo: dict[str, Any]) -> None:
    """Commit diff returns 409 when no repo configured."""
    admin = board_no_repo["admin"]
    session = board_no_repo["session"]

    client = make_client(admin, session)
    try:
        resp = client.get(f"/api/boards/NR/git/commits/{'a' * 40}/diff")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "repo_not_configured"
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_ac9_range_diff_no_repo_409(board_no_repo: dict[str, Any]) -> None:
    """Range diff returns 409 when no repo configured."""
    admin = board_no_repo["admin"]
    session = board_no_repo["session"]

    client = make_client(admin, session)
    try:
        resp = client.get("/api/boards/NR/git/diff?base=main&head=feature")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "repo_not_configured"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC1 — commit diff happy path (mocked reader)
# ---------------------------------------------------------------------------


def _make_mock_diff_result(
    path: str = "app/feature.py",
    patch: str = "@@ -0,0 +1 @@\n+hello\n",
    is_binary: bool = False,
    truncated: bool = False,
) -> DiffResult:
    """Build a DiffResult for mocking adiff_text / arange_diff."""
    file_diff = ReaderFileDiff(
        path=path,
        old_path=None,
        change_type="A",
        additions=1,
        deletions=0,
        is_binary=is_binary,
        patch=None if is_binary else patch,
        truncated=truncated,
    )
    return DiffResult(files=(file_diff,), truncated=truncated)


@pytest.mark.asyncio
async def test_ac1_commit_diff_happy_path(seeded: dict[str, Any]) -> None:
    """Commit diff 200 with correct shape when reader returns data."""
    sha_c = seeded["sha_c"]
    mock_result = _make_mock_diff_result()

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.adiff_text", new_callable=AsyncMock) as mock_diff:
        mock_open.return_value = MagicMock()
        mock_diff.return_value = mock_result

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get(f"/api/boards/GT/git/commits/{sha_c}/diff")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["sha"] == sha_c
            assert "files" in data
            assert "truncated" in data
            assert len(data["files"]) == 1

            f = data["files"][0]
            assert f["path"] == "app/feature.py"
            assert f["change_type"] == "A"
            assert "patch" in f
            assert "is_binary" in f
            assert "additions" in f
            assert "deletions" in f
            assert "truncated" in f
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC4 — binary file in commit diff (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4_commit_diff_binary(seeded: dict[str, Any]) -> None:
    """Binary file entry has is_binary=True and patch=null."""
    sha_c = seeded["sha_c"]
    mock_result = _make_mock_diff_result(
        path="logo.png", patch="", is_binary=True, truncated=False
    )

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.adiff_text", new_callable=AsyncMock) as mock_diff:
        mock_open.return_value = MagicMock()
        mock_diff.return_value = mock_result

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get(f"/api/boards/GT/git/commits/{sha_c}/diff")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            f = data["files"][0]
            assert f["is_binary"] is True
            assert f["patch"] is None
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC5 — truncated response (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5_commit_diff_truncated(seeded: dict[str, Any]) -> None:
    """Truncated diff has top-level truncated=True and file-level truncated=True."""
    sha_c = seeded["sha_c"]
    # Build a DiffResult with truncated=True at top level
    truncated_result = DiffResult(
        files=(
            ReaderFileDiff(
                path="large_file.py",
                old_path=None,
                change_type="M",
                additions=1000,
                deletions=0,
                is_binary=False,
                patch="@@ partial patch",
                truncated=True,
            ),
        ),
        truncated=True,
    )

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.adiff_text", new_callable=AsyncMock) as mock_diff:
        mock_open.return_value = MagicMock()
        mock_diff.return_value = truncated_result

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get(f"/api/boards/GT/git/commits/{sha_c}/diff")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["truncated"] is True
            assert data["files"][0]["truncated"] is True
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC6 — range diff happy path (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac6_range_diff_happy_path(seeded: dict[str, Any]) -> None:
    """Range diff returns 200 with base/head/files/truncated."""
    mock_result = _make_mock_diff_result(path="feature.txt", patch="@@ -0,0 +1 @@\n+work\n")

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.arange_diff", new_callable=AsyncMock) as mock_range:
        mock_open.return_value = MagicMock()
        mock_range.return_value = mock_result

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get("/api/boards/GT/git/diff?base=main&head=feature/x")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["base"] == "main"
            assert data["head"] == "feature/x"
            assert "files" in data
            assert "truncated" in data
            assert len(data["files"]) == 1
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC7 — range diff unknown ref → 404 (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac7_range_diff_unknown_ref(seeded: dict[str, Any]) -> None:
    """Unknown ref causes range diff to return 404."""
    import git as gitpython

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.arange_diff", new_callable=AsyncMock) as mock_range:
        mock_open.return_value = MagicMock()
        mock_range.side_effect = gitpython.GitCommandError("git diff", 128)

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get("/api/boards/GT/git/diff?base=main&head=nonexistent")
            assert resp.status_code == 404, resp.text
            assert resp.json()["error"] == "not_found"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC2 — path filter on commit diff: 404 when path not in commit (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_commit_diff_path_not_found(seeded: dict[str, Any]) -> None:
    """Path filter for file not in commit yields 404."""
    sha_c = seeded["sha_c"]
    empty_result = DiffResult(files=(), truncated=False)

    with patch("app.api.repositories.aopen_repo", new_callable=AsyncMock) as mock_open, \
         patch("app.api.repositories.adiff_text", new_callable=AsyncMock) as mock_diff:
        mock_open.return_value = MagicMock()
        mock_diff.return_value = empty_result

        client = make_client(seeded["member"], seeded["session"])
        try:
            resp = client.get(f"/api/boards/GT/git/commits/{sha_c}/diff?path=nonexistent.py")
            assert resp.status_code == 404, resp.text
            assert resp.json()["error"] == "not_found"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# G5-AC14 — OpenAPI operation_id presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac14_openapi_operation_ids(seeded: dict[str, Any]) -> None:
    """The three G5 operation_ids appear in the OpenAPI schema."""
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200, resp.text
        openapi = resp.json()
        ops: set[str] = set()
        for _path, methods in openapi.get("paths", {}).items():
            for _method, op in methods.items():
                if "operationId" in op:
                    ops.add(op["operationId"])

        assert "api_git_commit_diff" in ops, f"api_git_commit_diff missing from: {ops}"
        assert "api_git_range_diff" in ops, f"api_git_range_diff missing from: {ops}"
        assert "api_ticket_commits" in ops, f"api_ticket_commits missing from: {ops}"
    finally:
        clear_overrides()
