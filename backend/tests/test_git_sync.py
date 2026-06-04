"""Tests for app.git.sync — covers AC1-AC10 from PH-152.

Fixture strategy:
  - Reuses ``repo_fixture`` (session-scoped) from test_git_reader.py, which
    builds a real multi-commit, multi-branch git repo on disk.
  - A fresh SQLite in-memory DB is created per test via ``sync_session`` fixture
    (function-scoped) to guarantee isolation.
  - ``board_with_repo`` helper fixture creates a seeded Board + Repository row
    pointing at the fixture repo path.
  - EventBus.publish is monkeypatched to a list-collector so no Redis is needed.

All tests use pytest-asyncio (already wired in pytest.ini / pyproject.toml).
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    GitBranch,
    GitCommit,
    GitCommitFile,
    GitCommitTicket,
    Repository,
    Ticket,
    TicketHistory,
    Workflow,
)
from app.git.sync import SyncResult, sync_repo
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Reuse helpers from test_git_reader (copy the primitives we need)
# ---------------------------------------------------------------------------

_GIT = ["git", "-c", "user.email=t@t.local", "-c", "user.name=Test"]


def _run(*args: str, cwd: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_GIT, *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)


def _write_and_commit(repo_path: Path, files: dict[str, str], msg: str) -> str:
    for name, content in files.items():
        target = repo_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", name], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        [*_GIT, "commit", "-m", msg],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# Session fixture (function-scoped for test isolation)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sync_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Sync repo fixture — board + repo row pointing at a fresh git repo
# ---------------------------------------------------------------------------


class SyncFixture:
    """Everything sync tests need."""

    session: AsyncSession
    board: Board
    repo_row: Repository
    repo_path: Path
    root: Path  # repos_root


@pytest_asyncio.fixture
async def sync_fx(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> SyncFixture:
    """Create a Board + Repository pointing at a fresh git repo in tmp."""
    root = tmp_path_factory.mktemp("sync_repos")
    repo_path = root / "my-repo"
    repo_path.mkdir()
    _git_init(repo_path)

    # Patch repos_root so the reader's allowlist check passes.
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    # Create git commits
    sha1 = _write_and_commit(repo_path, {"file1.py": "line1\nline2\n"}, "initial commit")
    sha2 = _write_and_commit(repo_path, {"file2.py": "alpha\nbeta\n"}, "add file2")
    sha3 = _write_and_commit(repo_path, {"file1.py": "line1\nchanged\n"}, "modify file1")

    # Create a feature branch
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(repo_path), check=True, capture_output=True)
    sha4 = _write_and_commit(repo_path, {"feature.py": "feat\n"}, "feature work")
    # Return to main
    subprocess.run(["git", "checkout", "main"], cwd=str(repo_path), check=True, capture_output=True)

    # Seed DB: workflow + admin + board
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # Reload board with relationships
    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    fx = SyncFixture()
    fx.session = sync_session
    fx.board = board
    fx.repo_row = repo_row
    fx.repo_path = repo_path
    fx.root = root
    return fx


# ---------------------------------------------------------------------------
# Helper: null EventBus.publish
# ---------------------------------------------------------------------------


def _make_captured_publish() -> tuple[AsyncMock, list[Any]]:
    captured: list[Any] = []

    async def _mock_publish(envelope: Any) -> None:
        captured.append(envelope)

    return AsyncMock(side_effect=_mock_publish), captured


# ---------------------------------------------------------------------------
# AC1 — board without Repository → skipped, no DB writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_no_repository_skips(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: board without Repository row returns SyncResult(skipped=True)."""
    root = tmp_path_factory.mktemp("no_repo_root")
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="NR",
        name="NoRepo",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.commit()

    with patch("app.events.bus.EventBus.publish", new_callable=AsyncMock):
        result = await sync_repo(sync_session, board)

    assert result.skipped is True
    assert result.new_commits == 0
    assert result.linked_tickets == 0

    # Nothing should have been written
    commits = (await sync_session.execute(select(GitCommit))).scalars().all()
    assert len(commits) == 0


# ---------------------------------------------------------------------------
# AC2 — initial backfill imports all history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_initial_backfill_imports_history(sync_fx: SyncFixture) -> None:
    """AC2: fresh Repository with last_synced_sha=None imports all commits + branches + files."""
    mock_publish, captured = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_publish):
        result = await sync_repo(sync_fx.session, sync_fx.board)

    assert result.skipped is False
    assert result.new_commits >= 3  # at least initial + add file2 + modify file1

    # git_commits populated
    commits = (
        await sync_fx.session.execute(
            select(GitCommit).where(GitCommit.repo_id == sync_fx.repo_row.id)
        )
    ).scalars().all()
    assert len(commits) >= 3

    # git_branches: at least 2 branches (main + feature)
    branches = (
        await sync_fx.session.execute(
            select(GitBranch).where(GitBranch.repo_id == sync_fx.repo_row.id)
        )
    ).scalars().all()
    assert len(branches) >= 2
    defaults = [b for b in branches if b.is_default]
    assert len(defaults) == 1

    # git_commit_files: at least 1 row per commit
    files = (
        await sync_fx.session.execute(select(GitCommitFile))
    ).scalars().all()
    assert len(files) >= 3

    # last_synced_sha + last_synced_at set
    await sync_fx.session.refresh(sync_fx.repo_row)
    assert sync_fx.repo_row.last_synced_sha is not None
    assert sync_fx.repo_row.last_synced_at is not None

    # git_synced envelope published
    git_synced = [e for e in captured if e.type == "git_synced"]
    assert len(git_synced) == 1
    assert git_synced[0].board_id == str(sync_fx.board.id)
    assert git_synced[0].ticket_id == "system"


# ---------------------------------------------------------------------------
# AC3 — second sync is idempotent (zero inserts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_idempotent_second_run_zero_inserts(sync_fx: SyncFixture) -> None:
    """AC3: second sync returns new_commits==0, all counts unchanged."""
    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result1 = await sync_repo(sync_fx.session, sync_fx.board)

    assert result1.new_commits >= 3

    # Count rows after first sync
    count_commits = len(
        (await sync_fx.session.execute(
            select(GitCommit).where(GitCommit.repo_id == sync_fx.repo_row.id)
        )).scalars().all()
    )
    count_files = len((await sync_fx.session.execute(select(GitCommitFile))).scalars().all())
    count_links = len((await sync_fx.session.execute(select(GitCommitTicket))).scalars().all())
    count_history = len(
        (await sync_fx.session.execute(
            select(TicketHistory).where(TicketHistory.event_type == "git_commit_linked")
        )).scalars().all()
    )

    # Second sync
    mock_pub2, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub2):
        result2 = await sync_repo(sync_fx.session, sync_fx.board)

    assert result2.new_commits == 0
    assert result2.linked_tickets == 0

    # Row counts must be unchanged
    assert len(
        (await sync_fx.session.execute(
            select(GitCommit).where(GitCommit.repo_id == sync_fx.repo_row.id)
        )).scalars().all()
    ) == count_commits
    assert len(
        (await sync_fx.session.execute(select(GitCommitFile))).scalars().all()
    ) == count_files
    assert len(
        (await sync_fx.session.execute(select(GitCommitTicket))).scalars().all()
    ) == count_links
    assert len(
        (await sync_fx.session.execute(
            select(TicketHistory).where(TicketHistory.event_type == "git_commit_linked")
        )).scalars().all()
    ) == count_history


# ---------------------------------------------------------------------------
# AC4 — ticket linkage via commit message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_links_ticket_keys_from_messages(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: commit feat(PH-1): x → git_commit_tickets row + one git_commit_linked history."""
    root = tmp_path_factory.mktemp("link_repos")
    repo_path = root / "link-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    # Seed DB
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    # Create a PH-1 ticket
    ticket = Ticket(
        key="PH-1",
        board_id=board.id,
        type="task",
        title="Sample ticket",
        description="",
        state="to_do",
        reporter_id=admin.id,
        priority="medium",
        labels=[],
    )
    sync_session.add(ticket)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # Write commits — one with PH-1 reference
    _write_and_commit(repo_path, {"a.txt": "initial\n"}, "initial commit")
    commit_sha = _write_and_commit(repo_path, {"b.txt": "work\n"}, "feat(PH-1): add feature x")

    # Reload board
    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    mock_pub, captured = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result = await sync_repo(sync_session, board)

    assert result.linked_tickets >= 1

    # git_commit_tickets must have a row for PH-1
    links = (
        await sync_session.execute(
            select(GitCommitTicket).where(GitCommitTicket.ticket_id == ticket.id)
        )
    ).scalars().all()
    assert len(links) == 1

    # Exactly one git_commit_linked history row for PH-1
    history_rows = (
        await sync_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "git_commit_linked",
            )
        )
    ).scalars().all()
    assert len(history_rows) == 1

    h = history_rows[0]
    meta = h.event_metadata
    assert meta["sha"] == commit_sha
    assert "sha_short" in meta
    assert "message" in meta
    assert "author" in meta
    assert "url" in meta
    assert "commit_type" in meta
    assert "is_conventional" in meta
    assert "branch" in meta


# ---------------------------------------------------------------------------
# AC5 — no double-link when junction row pre-seeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_does_not_double_link_webhook_already_linked(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: if git_commit_tickets row already exists, sync skips history write."""
    root = tmp_path_factory.mktemp("dedup_repos")
    repo_path = root / "dedup-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    ticket = Ticket(
        key="PH-1",
        board_id=board.id,
        type="task",
        title="Ticket one",
        description="",
        state="to_do",
        reporter_id=admin.id,
        priority="medium",
        labels=[],
    )
    sync_session.add(ticket)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # Create repo with PH-1 commit
    _write_and_commit(repo_path, {"a.txt": "init\n"}, "feat(PH-1): first")

    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    # First sync — establishes the link
    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result1 = await sync_repo(sync_session, board)

    assert result1.linked_tickets == 1

    history_count_before = len(
        (
            await sync_session.execute(
                select(TicketHistory).where(
                    TicketHistory.ticket_id == ticket.id,
                    TicketHistory.event_type == "git_commit_linked",
                )
            )
        ).scalars().all()
    )
    assert history_count_before == 1

    # Second sync — must NOT add another history row
    mock_pub2, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub2):
        result2 = await sync_repo(sync_session, board)

    assert result2.linked_tickets == 0

    history_count_after = len(
        (
            await sync_session.execute(
                select(TicketHistory).where(
                    TicketHistory.ticket_id == ticket.id,
                    TicketHistory.event_type == "git_commit_linked",
                )
            )
        ).scalars().all()
    )
    assert history_count_after == 1, "History row count must not grow on second sync"


# ---------------------------------------------------------------------------
# AC6 — git_synced envelope emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_emits_git_synced_envelope(sync_fx: SyncFixture) -> None:
    """AC6: exactly one git_synced envelope with correct shape after sync."""
    mock_pub, captured = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result = await sync_repo(sync_fx.session, sync_fx.board)

    git_synced_envelopes = [e for e in captured if e.type == "git_synced"]
    assert len(git_synced_envelopes) == 1

    env = git_synced_envelopes[0]
    assert env.board_id == str(sync_fx.board.id)
    assert env.ticket_id == "system"

    payload = env.payload
    assert "new_commit_shas" in payload
    assert "updated_branches" in payload
    assert "deleted_branches" in payload
    assert "commits_count" in payload
    assert "last_synced_sha" in payload
    assert "last_synced_at" in payload
    assert len(payload["new_commit_shas"]) == result.new_commits


# ---------------------------------------------------------------------------
# AC7 — deleted branch removed from git_branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_handles_deleted_branch(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: stale git_branches row for a vanished branch is removed."""
    root = tmp_path_factory.mktemp("del_repos")
    repo_path = root / "del-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # Build repo with a branch
    _write_and_commit(repo_path, {"a.txt": "x\n"}, "initial")
    subprocess.run(
        ["git", "checkout", "-b", "old-feature"],
        cwd=str(repo_path), check=True, capture_output=True
    )
    _write_and_commit(repo_path, {"feat.txt": "y\n"}, "feature commit")
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo_path), check=True, capture_output=True
    )

    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    # First sync — picks up both branches
    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result1 = await sync_repo(sync_session, board)

    branches_after_first = (
        await sync_session.execute(
            select(GitBranch).where(GitBranch.repo_id == repo_row.id)
        )
    ).scalars().all()
    branch_names_first = {b.name for b in branches_after_first}
    assert "old-feature" in branch_names_first

    # Delete the branch from the git repo
    subprocess.run(
        ["git", "branch", "-D", "old-feature"],
        cwd=str(repo_path), check=True, capture_output=True
    )

    # Second sync — must remove the stale row
    mock_pub2, captured2 = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub2):
        result2 = await sync_repo(sync_session, board)

    assert result2.deleted_branches >= 1

    branches_after_second = (
        await sync_session.execute(
            select(GitBranch).where(GitBranch.repo_id == repo_row.id)
        )
    ).scalars().all()
    branch_names_second = {b.name for b in branches_after_second}
    assert "old-feature" not in branch_names_second

    # deleted_branches in payload
    git_synced_envelopes = [e for e in captured2 if e.type == "git_synced"]
    assert len(git_synced_envelopes) == 1
    assert "old-feature" in git_synced_envelopes[0].payload["deleted_branches"]


# ---------------------------------------------------------------------------
# AC8 — last_synced_sha updated to default HEAD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_updates_last_synced_sha_to_default_head(sync_fx: SyncFixture) -> None:
    """AC8: after sync, last_synced_sha equals default branch HEAD SHA."""
    # Get current HEAD of main
    head_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=str(sync_fx.repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        await sync_repo(sync_fx.session, sync_fx.board)

    await sync_fx.session.refresh(sync_fx.repo_row)
    assert sync_fx.repo_row.last_synced_sha == head_sha


# ---------------------------------------------------------------------------
# AC9 — force-push (unreachable since_sha) falls back to full backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_handles_force_push_unreachable_since_sha(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9: when last_synced_sha is unreachable, sync falls back to full backfill."""
    root = tmp_path_factory.mktemp("fp_repos")
    repo_path = root / "fp-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # Create initial commits
    sha_a = _write_and_commit(repo_path, {"a.txt": "a\n"}, "commit A")
    sha_b = _write_and_commit(repo_path, {"b.txt": "b\n"}, "commit B")

    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    # First sync — establishes last_synced_sha = sha_b
    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result1 = await sync_repo(sync_session, board)

    assert result1.new_commits == 2

    # Simulate force-push: set last_synced_sha to a fake/unreachable SHA
    repo_row.last_synced_sha = "deadbeef" * 5  # 40 chars, non-existent
    await sync_session.commit()

    # Add a new commit on top
    sha_c = _write_and_commit(repo_path, {"c.txt": "c\n"}, "commit C")

    # Second sync with bad since_sha — should fall back and not raise
    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    mock_pub2, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub2):
        result2 = await sync_repo(sync_session, board)

    # Should have synced without raising; repo is consistent
    assert result2.skipped is False
    commits_in_db = (
        await sync_session.execute(
            select(GitCommit).where(GitCommit.repo_id == repo_row.id)
        )
    ).scalars().all()
    # All 3 commits should be present (upserts are idempotent)
    shas_in_db = {c.sha for c in commits_in_db}
    assert sha_a in shas_in_db
    assert sha_b in shas_in_db
    assert sha_c in shas_in_db


# ---------------------------------------------------------------------------
# AC10 — backfill cap respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_backfill_cap_respected(
    sync_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: git_backfill_limit=2 on a 5-commit repo → only 2 commits cached."""
    root = tmp_path_factory.mktemp("cap_repos")
    repo_path = root / "cap-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))
    monkeypatch.setattr(get_settings(), "git_backfill_limit", 2)

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sync_session.add(workflow)
    await sync_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sync_session.add(admin)
    await sync_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    sync_session.add(board)
    await sync_session.flush()

    repo_row = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path=str(repo_path),
        provider="local",
        default_branch="main",
    )
    sync_session.add(repo_row)
    await sync_session.commit()

    # 5 commits
    shas = []
    for i in range(1, 6):
        sha = _write_and_commit(repo_path, {f"f{i}.txt": f"content {i}\n"}, f"commit {i}")
        shas.append(sha)

    board = (
        await sync_session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repository))
        )
    ).scalar_one()

    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result = await sync_repo(sync_session, board)

    # Cap enforced: at most 2 commits cached
    assert result.new_commits == 2

    # last_synced_sha still set to HEAD (most recent commit)
    await sync_session.refresh(repo_row)
    assert repo_row.last_synced_sha is not None
    # HEAD must be the last commit we made
    head = shas[-1]
    assert repo_row.last_synced_sha == head
