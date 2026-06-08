"""Tests for app.services.git_detect — git filesystem auto-detection (PH-222, C2).

Covers the AC:
  - detect returns DetectedRepo candidates for git working copies under the root
  - remote_url set for an origin-having repo, None for one without; provider_guess
  - already_linked flips true when a Repository row on THIS board matches the path
  - non-git dir skipped; missing/empty root → [] (never raises)
  - symlink resolving OUTSIDE the allowlist is skipped (reader _validate_under_root)
  - read-only: no Repository rows created, filesystem unchanged
  - result cap + depth bound respected; provider_guess host parsing

Fixture strategy mirrors test_git_reader.py: build real git working copies with
subprocess under a temp root, pass that root as ``repos_root=`` so tests never
touch the real ``/repos/`` mount. DB-side tests use the in-memory ``db_session``
+ ``seed`` fixtures (conftest) for the ``already_linked`` path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.config import get_settings
from app.db.models import Repository
from app.db.session import get_db_session
from app.main import app
from app.schemas import DetectedRepo
from app.services.git_detect import (
    _provider_guess,
    _scan_sync,
    detect_repositories,
)
from tests.conftest import Seed

# ---------------------------------------------------------------------------
# Git helpers (build real working copies)
# ---------------------------------------------------------------------------

_GIT_ID = ["-c", "user.email=t@t.local", "-c", "user.name=Test"]


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
    )


def _commit_one(path: Path, name: str = "README.md") -> None:
    (path / name).write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", *_GIT_ID, "commit", "-m", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def _make_repo(root: Path, name: str, *, origin: str | None = None) -> Path:
    """Create a committed git working copy ``root/name``; optionally add an origin."""
    repo = root / name
    _git_init(repo)
    _commit_one(repo)
    if origin is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
    return repo


# ---------------------------------------------------------------------------
# Pure helper: provider_guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:acme/app.git", "github"),
        ("https://github.com/acme/app.git", "github"),
        ("https://gitlab.com/acme/app.git", "gitlab"),
        ("git@gitlab.example.org:acme/app.git", "gitlab"),
        ("https://bitbucket.org/acme/app.git", "local"),
        ("/srv/git/app.git", "local"),
        (None, "local"),
        ("", "local"),
    ],
)
def test_provider_guess(url: str | None, expected: str) -> None:
    assert _provider_guess(url) == expected


# ---------------------------------------------------------------------------
# Service: detect_repositories (uses DB session for already_linked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_returns_candidates(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    _make_repo(root, "api", origin="git@github.com:acme/api.git")
    _make_repo(root, "web")  # no origin

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    by_name = {c.name: c for c in found}
    assert set(by_name) == {"api", "web"}

    api = by_name["api"]
    assert api.is_git is True
    assert api.remote_url == "git@github.com:acme/api.git"
    assert api.provider_guess == "github"
    assert api.default_branch == "main"
    assert api.already_linked is False
    assert api.local_path == str(root / "api")

    web = by_name["web"]
    assert web.remote_url is None
    assert web.provider_guess == "local"
    assert web.already_linked is False


@pytest.mark.asyncio
async def test_non_git_dir_skipped(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    _make_repo(root, "api")
    (root / "plain").mkdir(parents=True)  # no .git → not a candidate
    (root / "plain" / "file.txt").write_text("x", encoding="utf-8")

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    assert {c.name for c in found} == {"api"}


@pytest.mark.asyncio
async def test_already_linked_true_for_board_repo(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    api = _make_repo(root, "api")
    _make_repo(root, "web")

    # Link 'api' to THIS board (insert a Repository row pointing at its path).
    db_session.add(
        Repository(
            id=uuid.uuid4(),
            board_id=seed.board.id,
            slug="api",
            name="api",
            is_primary=True,
            provider="local",
            remote_url=None,
            default_branch="main",
            local_path=str(api),
        )
    )
    await db_session.commit()

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    by_name = {c.name: c for c in found}
    assert by_name["api"].already_linked is True
    assert by_name["web"].already_linked is False


@pytest.mark.asyncio
async def test_missing_root_returns_empty(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    missing = tmp_path / "does-not-exist"
    found = await detect_repositories(db_session, seed.board, repos_root=str(missing))
    assert found == []


@pytest.mark.asyncio
async def test_empty_root_returns_empty(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    assert found == []


@pytest.mark.asyncio
async def test_depth_two_descends_into_container_dir(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    # A container dir (no .git itself) holding a repo one level down.
    _make_repo(root / "group", "inner")
    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    assert {c.name for c in found} == {"inner"}


@pytest.mark.asyncio
async def test_does_not_descend_into_detected_repo(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    """A nested git dir inside an already-detected repo is NOT separately returned."""
    root = tmp_path / "repos"
    outer = _make_repo(root, "outer")
    # Simulate a vendored repo (like a submodule/node_modules clone) inside it.
    _make_repo(outer / "vendor", "nested")

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    # Only the outer repo is a candidate; we never recurse into a detected repo.
    assert {c.name for c in found} == {"outer"}


@pytest.mark.asyncio
async def test_no_rows_created_and_fs_unchanged(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    repo = _make_repo(root, "api")
    before_mtime = repo.stat().st_mtime
    before_entries = sorted(p.name for p in root.iterdir())

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    assert len(found) == 1

    # No Repository rows were created by detection.
    from sqlalchemy import func, select

    count = (
        await db_session.execute(
            select(func.count()).select_from(Repository).where(
                Repository.board_id == seed.board.id
            )
        )
    ).scalar_one()
    assert count == 0

    # Filesystem unchanged.
    assert sorted(p.name for p in root.iterdir()) == before_entries
    assert repo.stat().st_mtime == before_mtime


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
@pytest.mark.asyncio
async def test_symlink_outside_allowlist_skipped(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    # A real repo OUTSIDE the scan root.
    outside = _make_repo(tmp_path / "outside", "secret")
    # A symlink under the root pointing at it — resolves outside the allowlist.
    link = root / "escape"
    link.symlink_to(outside)

    found = await detect_repositories(db_session, seed.board, repos_root=str(root))
    # The escaping symlink must be skipped (reader._validate_under_root rejects it).
    assert found == []


# ---------------------------------------------------------------------------
# Bounds: result cap + wall-clock budget (sync core, no DB needed)
# ---------------------------------------------------------------------------


def test_result_cap_respected(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    for i in range(5):
        _make_repo(root, f"r{i}")
    out = _scan_sync(str(root), set(), max_results=2, time_budget_seconds=30.0)
    assert len(out) == 2
    assert all(isinstance(c, DetectedRepo) for c in out)


def test_time_budget_zero_returns_quickly(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    for i in range(3):
        _make_repo(root, f"r{i}")
    # A non-positive budget means the deadline is already passed → empty (no hang).
    out = _scan_sync(str(root), set(), max_results=100, time_budget_seconds=0.0)
    assert out == []


def test_permission_error_dir_does_not_raise(tmp_path: Path) -> None:
    """A directory that cannot be scandir'd is skipped, not fatal."""
    root = tmp_path / "repos"
    _make_repo(root, "ok")
    locked = root / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    try:
        out = _scan_sync(str(root), set(), max_results=100, time_budget_seconds=30.0)
        # 'ok' is still detected; 'locked' is silently skipped (no .git anyway).
        assert "ok" in {c.name for c in out}
    finally:
        os.chmod(locked, 0o755)  # restore so tmp cleanup can remove it


# ---------------------------------------------------------------------------
# API route: GET /api/boards/{key}/repositories/detect
# ---------------------------------------------------------------------------


def _make_client(actor: object, session: AsyncSession) -> TestClient:
    async def _fake_current_actor() -> object:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.asyncio
async def test_route_member_detect_200(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    root = tmp_path / "repos"
    _make_repo(root, "api", origin="https://gitlab.com/acme/api.git")

    # Point the scan root at the temp dir for the duration of the request.
    settings = get_settings()
    original = settings.repos_root
    settings.repos_root = str(root)
    client = _make_client(seed.backend, db_session)
    try:
        resp = client.get(f"/api/boards/{seed.board.key}/repositories/detect")
    finally:
        settings.repos_root = original
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert "repositories" in body
    names = {r["name"] for r in body["repositories"]}
    assert "api" in names
    api = next(r for r in body["repositories"] if r["name"] == "api")
    assert api["provider_guess"] == "gitlab"
    assert api["already_linked"] is False


@pytest.mark.asyncio
async def test_route_empty_root_200_empty_list(
    tmp_path: Path, db_session: AsyncSession, seed: Seed
) -> None:
    settings = get_settings()
    original = settings.repos_root
    settings.repos_root = str(tmp_path / "nope")  # missing → empty, never 500
    client = _make_client(seed.backend, db_session)
    try:
        resp = client.get(f"/api/boards/{seed.board.key}/repositories/detect")
    finally:
        settings.repos_root = original
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"repositories": []}
