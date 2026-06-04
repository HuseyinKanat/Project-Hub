"""Tests for G7 — connect_repository CLI command + install-git-hook.sh.

Covers AC G7.1 (hook idempotency + worktree-safety) and G7.2 (CLI row + secret + backfill).

Fixture strategy:
  - Fresh in-memory SQLite DB per test (same pattern as test_git_sync.py).
  - Real git repos built with subprocess (same helpers as test_git_sync.py).
  - connect_repository() called directly (not via argparse) to avoid subprocess overhead.
  - install-git-hook.sh tested with subprocess via bash in a real temp git repo
    (including worktree variant).
  - EventBus.publish monkeypatched to avoid Redis dependency.
"""

from __future__ import annotations

import os
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

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Actor, Board, Repository, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Git helpers (copied from test_git_sync.py)
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
# DB session fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    LocalSession = async_sessionmaker(engine, expire_on_commit=False)
    async with LocalSession() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Board + repo-path fixture
# ---------------------------------------------------------------------------


class _ConnectFx:
    session: AsyncSession
    board: Board
    repo_path: Path
    repos_root: Path


@pytest_asyncio.fixture
async def connect_fx(
    db_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> _ConnectFx:
    """Build a real git repo + a PH board in the in-memory DB."""
    repos_root = tmp_path_factory.mktemp("connect_repos")
    repo_path = repos_root / "project-hub"
    repo_path.mkdir()
    _git_init(repo_path)

    # Patch repos_root allowlist so the G2 reader's path check passes.
    monkeypatch.setattr(get_settings(), "repos_root", str(repos_root))

    # Seed a few commits
    _write_and_commit(repo_path, {"app.py": "# hello\n"}, "feat(PH-1): initial")
    _write_and_commit(repo_path, {"app.py": "# updated\n"}, "fix(PH-2): update")

    # Seed DB: workflow + admin + board
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    db_session.add(workflow)
    await db_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    db_session.add(admin)
    await db_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=dict(DEFAULT_WEB_ROLES),
        created_by=admin.id,
    )
    db_session.add(board)
    await db_session.commit()

    fx = _ConnectFx()
    fx.session = db_session
    fx.board = board
    fx.repo_path = repo_path
    fx.repos_root = repos_root
    return fx


# ---------------------------------------------------------------------------
# Null EventBus.publish helper
# ---------------------------------------------------------------------------


def _null_bus() -> Any:
    return patch("app.events.bus.EventBus.publish", new_callable=AsyncMock)


def _bypass_path_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch app.schemas.RepositoryUpsert._validate_constraints to allow non-/repos/ paths.

    This is a test-only bypass for the container-side path allowlist guard.
    The guard is tested separately via the actual API tests.

    Because Pydantic v2 validators are compiled, we patch at the app.services.repositories
    level — specifically replacing upsert_repository to skip schema construction
    and directly build the ORM row (the same thing upsert_repository does internally).
    """
    import app.services.repositories as _repo_svc

    original_get = _repo_svc.get_repository

    async def _patched_upsert(session: AsyncSession, board: Board, payload: Any) -> Any:
        from app.db.models import Repository

        existing = await original_get(session, board)
        if existing is None:
            repo = Repository(
                id=uuid.uuid4(),
                board_id=board.id,
                provider=getattr(payload, "provider", "local"),
                remote_url=getattr(payload, "remote_url", None),
                default_branch=getattr(payload, "default_branch", "main"),
                local_path=getattr(payload, "local_path", ""),
            )
            session.add(repo)
        else:
            existing.provider = getattr(payload, "provider", "local")
            existing.remote_url = getattr(payload, "remote_url", None)
            existing.default_branch = getattr(payload, "default_branch", "main")
            existing.local_path = getattr(payload, "local_path", "")
            repo = existing
        await session.flush()
        await session.refresh(repo)
        return repo

    # Patch the RepositoryUpsert schema to a simple namespace class
    class _SimpleUpsert:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    # Patch both the schema (for instantiation in cli.py) and the service
    import app.schemas as _schemas
    monkeypatch.setattr(_schemas, "RepositoryUpsert", _SimpleUpsert)
    monkeypatch.setattr(_repo_svc, "upsert_repository", _patched_upsert)


# ---------------------------------------------------------------------------
# connect_repository tests (G7.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_creates_repo_row_and_secret(
    connect_fx: _ConnectFx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G7.2 — First call: creates Repository row, mints refresh_secret, returns new_commits > 0."""
    from app.cli import connect_repository

    # Bypass /repos/ path prefix guard for test tmp paths
    _bypass_path_validation(monkeypatch)

    # Patch SessionLocal to use in-memory DB session
    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return connect_fx.session

        async def __aexit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("app.cli.SessionLocal", lambda: _SessionCtx())

    local_path = str(connect_fx.repo_path)

    with _null_bus():
        result = await connect_repository(
            "PH",
            local_path,
            default_branch="main",
            provider="local",
            rotate_secret=True,
        )

    assert result.board_key == "PH"
    assert result.repo_id is not None
    # Secret minted on first call with rotate_secret=True
    assert result.refresh_secret is not None
    assert len(result.refresh_secret) == 48  # token_hex(24) = 48 hex chars

    # backfill ran — at least the 2 seeded commits
    assert result.new_commits >= 2
    assert result.last_synced_sha is not None

    # DB: Repository row exists
    repo_row = (
        await connect_fx.session.execute(
            select(Repository).where(Repository.board_id == connect_fx.board.id)
        )
    ).scalar_one_or_none()
    assert repo_row is not None
    assert repo_row.local_path == local_path

    # DB: board.roles["refresh_secret"] set
    board = (
        await connect_fx.session.execute(select(Board).where(Board.key == "PH"))
    ).scalar_one()
    assert board.roles is not None
    assert board.roles.get("refresh_secret") == result.refresh_secret


@pytest.mark.asyncio
async def test_connect_idempotent_no_rotate(
    connect_fx: _ConnectFx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G7.2 — Second call WITHOUT --rotate-secret: refresh_secret unchanged, no re-print."""
    from app.cli import connect_repository

    _bypass_path_validation(monkeypatch)

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return connect_fx.session

        async def __aexit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("app.cli.SessionLocal", lambda: _SessionCtx())
    local_path = str(connect_fx.repo_path)

    with _null_bus():
        first = await connect_repository("PH", local_path, rotate_secret=True)

    original_secret = first.refresh_secret
    assert original_secret is not None

    with _null_bus():
        second = await connect_repository("PH", local_path, rotate_secret=False)

    # Secret must NOT change and must NOT be returned (security: once-only print)
    assert second.refresh_secret is None  # not re-printed

    board = (
        await connect_fx.session.execute(select(Board).where(Board.key == "PH"))
    ).scalar_one()
    assert board.roles.get("refresh_secret") == original_secret  # unchanged in DB


@pytest.mark.asyncio
async def test_connect_rotate_secret_changes_secret(
    connect_fx: _ConnectFx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G7.2 — --rotate-secret replaces the existing secret."""
    from app.cli import connect_repository

    _bypass_path_validation(monkeypatch)

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return connect_fx.session

        async def __aexit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("app.cli.SessionLocal", lambda: _SessionCtx())
    local_path = str(connect_fx.repo_path)

    with _null_bus():
        first = await connect_repository("PH", local_path, rotate_secret=True)
    original_secret = first.refresh_secret

    with _null_bus():
        second = await connect_repository("PH", local_path, rotate_secret=True)
    new_secret = second.refresh_secret

    assert new_secret is not None
    assert new_secret != original_secret

    board = (
        await connect_fx.session.execute(select(Board).where(Board.key == "PH"))
    ).scalar_one()
    assert board.roles.get("refresh_secret") == new_secret


@pytest.mark.asyncio
async def test_connect_no_backfill_skips_sync(
    connect_fx: _ConnectFx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G7.2 — --no-backfill: repo row created but new_commits=0."""
    from app.cli import connect_repository

    _bypass_path_validation(monkeypatch)

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return connect_fx.session

        async def __aexit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("app.cli.SessionLocal", lambda: _SessionCtx())
    local_path = str(connect_fx.repo_path)

    with _null_bus():
        result = await connect_repository("PH", local_path, rotate_secret=True, no_backfill=True)

    assert result.new_commits == 0
    # Repo row still created
    repo_row = (
        await connect_fx.session.execute(
            select(Repository).where(Repository.board_id == connect_fx.board.id)
        )
    ).scalar_one_or_none()
    assert repo_row is not None


# ---------------------------------------------------------------------------
# install-git-hook.sh tests (G7.1)
# ---------------------------------------------------------------------------

# The script lives in the project root's scripts/ directory.
# In the Docker container: /repos/project-hub/scripts/install-git-hook.sh
# Detected via the repo mount path.
_PROJECT_ROOT = Path("/repos/project-hub")
if not _PROJECT_ROOT.exists():
    # Fallback: resolve relative to test file (host-side test run)
    _PROJECT_ROOT = Path(__file__).parents[2]
SCRIPT = _PROJECT_ROOT / "scripts" / "install-git-hook.sh"
HOOK_NAMES = ["post-commit", "post-merge", "post-checkout", "post-rewrite"]
MARKER_BEGIN = "# === BEGIN projecthub-refresh"
MARKER_END = "# === END projecthub-refresh ==="


def _run_install(
    repo_path: Path,
    board_key: str = "PH",
    backend_url: str = "http://localhost:8000",
    secret: str = "dummy-secret",
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "bash",
        str(SCRIPT),
        str(repo_path),
        board_key,
        backend_url,
        secret,
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def _hooks_dir(repo_path: Path) -> Path:
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if common.startswith("/"):
        return Path(common) / "hooks"
    return repo_path / common / "hooks"


def test_install_creates_hooks_with_marker(tmp_path: Path) -> None:
    """G7.1 — Fresh install: all 4 hook files contain BEGIN/END marker + curl line."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    result = _run_install(repo)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    hooks_dir = _hooks_dir(repo)
    for hook in HOOK_NAMES:
        hook_file = hooks_dir / hook
        assert hook_file.exists(), f"{hook} not created"
        content = hook_file.read_text()
        assert MARKER_BEGIN in content, f"{hook} missing BEGIN marker"
        assert MARKER_END in content, f"{hook} missing END marker"
        assert "X-Git-Refresh-Token: dummy-secret" in content
        assert "http://localhost:8000/api/boards/PH/git/refresh" in content
        # Must be executable
        assert os.access(hook_file, os.X_OK), f"{hook} not executable"


def test_install_idempotent_same_args(tmp_path: Path) -> None:
    """G7.1 — Re-run with same args → 'already installed (matched)', file unchanged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    _run_install(repo)  # first install
    hooks_dir = _hooks_dir(repo)

    # Snapshot sha of post-commit
    hook_file = hooks_dir / "post-commit"
    content_before = hook_file.read_text()

    result2 = _run_install(repo)  # second install
    assert result2.returncode == 0
    assert "already installed (matched)" in result2.stdout

    content_after = hook_file.read_text()
    assert content_before == content_after, "Hook file changed on idempotent re-run"


def test_install_updates_changed_secret(tmp_path: Path) -> None:
    """G7.1 — Re-run with different secret → block updated, surrounding content preserved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    # First install with secret1
    _run_install(repo, secret="secret-one")
    hooks_dir = _hooks_dir(repo)
    hook_file = hooks_dir / "post-commit"

    # Add custom content before/after to simulate a pre-existing hook
    original_custom = "# my-custom-hook-step\necho 'running'\n"
    with hook_file.open("a") as f:
        f.write(original_custom)

    content_mid = hook_file.read_text()
    assert "secret-one" in content_mid

    # Re-install with secret2
    result2 = _run_install(repo, secret="secret-two")
    assert result2.returncode == 0
    assert "updated" in result2.stdout

    content_after = hook_file.read_text()
    assert "secret-two" in content_after
    assert "secret-one" not in content_after
    # Custom content must be preserved
    assert "my-custom-hook-step" in content_after


def test_install_appends_to_existing_hook(tmp_path: Path) -> None:
    """G7.1 — Existing hook without marker → block appended, original content preserved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    hooks_dir = _hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create a hook with custom content
    pre_hook = hooks_dir / "post-commit"
    pre_hook.write_text("#!/bin/sh\n# existing custom hook\necho 'existing'\n")
    pre_hook.chmod(0o755)

    result = _run_install(repo)
    assert result.returncode == 0
    assert "appended" in result.stdout

    content = pre_hook.read_text()
    assert "existing custom hook" in content  # preserved
    assert MARKER_BEGIN in content
    assert "X-Git-Refresh-Token: dummy-secret" in content


def test_install_worktree_safe(tmp_path: Path) -> None:
    """G7.1 — Installing from a worktree path writes to the main repo's hooks dir."""
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    _git_init(main_repo)
    _write_and_commit(main_repo, {"f.txt": "a\n"}, "initial")

    # Create a separate branch for the worktree (can't add worktree for checked-out branch)
    subprocess.run(
        [*_GIT, "checkout", "-b", "wt-branch"],
        cwd=str(main_repo),
        check=True,
        capture_output=True,
    )
    _write_and_commit(main_repo, {"g.txt": "b\n"}, "branch commit")
    # Switch back to main
    subprocess.run(
        [*_GIT, "checkout", "main"],
        cwd=str(main_repo),
        check=True,
        capture_output=True,
    )

    # Add a worktree pointing at wt-branch
    wt_path = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "wt-branch"],
        cwd=str(main_repo),
        check=True,
        capture_output=True,
    )

    # Install from worktree path — should write to main repo's hooks
    result = _run_install(wt_path)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # Hooks must be in the MAIN repo's .git/hooks/
    main_hooks = main_repo / ".git" / "hooks"
    for hook in HOOK_NAMES:
        hook_file = main_hooks / hook
        assert hook_file.exists(), f"{hook} not found in main repo hooks (worktree install failed)"
        content = hook_file.read_text()
        assert MARKER_BEGIN in content


def test_install_bare_repo_exits_with_error(tmp_path: Path) -> None:
    """G7.1 — Bare repos are not supported; script exits with code 1."""
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    result = _run_install(bare)
    assert result.returncode == 1
    assert "bare" in result.stderr.lower() or "not supported" in result.stderr.lower()


def test_install_missing_repo_exits_with_error(tmp_path: Path) -> None:
    """G7.1 — Non-existent repo path exits with error."""
    result = _run_install(tmp_path / "nonexistent")
    assert result.returncode != 0


def test_install_hooks_dir_override(tmp_path: Path) -> None:
    """G7.1 — --hooks-dir override installs to specified directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    custom_hooks = tmp_path / "custom-hooks"
    custom_hooks.mkdir()

    result = _run_install(repo, extra_args=["--hooks-dir", str(custom_hooks)])
    assert result.returncode == 0

    # Hooks in custom dir
    for hook in HOOK_NAMES:
        hook_file = custom_hooks / hook
        assert hook_file.exists()
        assert MARKER_BEGIN in hook_file.read_text()
