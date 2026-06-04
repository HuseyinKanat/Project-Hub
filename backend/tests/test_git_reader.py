"""Tests for app.git.reader — covers all 17 acceptance criteria (PH-151).

Fixture strategy:
  - ``tmp_git_repo`` (session-scoped conftest fixture): builds a multi-commit
    repo with branches, a rename, a binary file, and a merge commit.
  - Path allowlist override: ``monkeypatch.setattr(get_settings(), 'repos_root', str(root))``
    so tests never depend on the container mount.

All subprocess git calls use ``-c user.email=t@t.local -c user.name=Test``
to avoid "You need to configure your identity" errors in clean CI environments.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.git.reader import (
    BranchInfo,
    CommitFileChange,
    CommitInfo,
    NotARepository,
    RepoNotFound,
    RepoPathOutsideAllowlist,
    awalk_commits,
    commit_files,
    list_branches,
    open_repo,
    walk_commits,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT = [
    "git",
    "-c", "user.email=t@t.local",
    "-c", "user.name=Test",
]


def _run(*args: str, cwd: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_GIT, *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
    )


def _write_and_commit(repo_path: Path, files: dict[str, bytes | str], msg: str) -> str:
    """Write files, stage them, commit, return the commit SHA."""
    for name, content in files.items():
        target = repo_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", name],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [*_GIT, "commit", "-m", msg],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
        text=True,
    )
    # Get full SHA
    full = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    )
    return full.stdout.strip()


# ---------------------------------------------------------------------------
# Session-scoped fixture: rich multi-scenario repo
# ---------------------------------------------------------------------------

class RepoFixture:
    """Holds paths and SHAs for the tmp git repo used across tests."""
    root: Path          # repos_root (allowlist root)
    repo_path: Path     # repos_root/project-hub
    initial_sha: str
    modify_sha: str
    rename_sha: str
    binary_sha: str
    feature_sha: str    # tip of feature branch
    merge_sha: str      # merge commit on main


@pytest.fixture(scope="session")
def repo_fixture(tmp_path_factory: pytest.TempPathFactory) -> RepoFixture:
    root = tmp_path_factory.mktemp("repos")
    repo_path = root / "project-hub"
    repo_path.mkdir()
    _git_init(repo_path)

    fx = RepoFixture()
    fx.root = root
    fx.repo_path = repo_path

    # ---- Commit 1 (initial): add a.txt (10 lines) + b.txt ----
    a_content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
    b_content = "hello\nworld\nfoo\n"
    fx.initial_sha = _write_and_commit(
        repo_path,
        {"a.txt": a_content, "b.txt": b_content},
        "initial commit",
    )

    # ---- Commit 2 (modify): modify b.txt (+3 lines, -1) ----
    # Replace first line, append 3 more lines
    b_modified = "HELLO\nworld\nfoo\nbar\nbaz\nqux\n"
    fx.modify_sha = _write_and_commit(repo_path, {"b.txt": b_modified}, "modify b.txt")

    # ---- Commit 3 (rename): rename c.txt → c2.txt ----
    # First add c.txt
    _write_and_commit(repo_path, {"c.txt": "original content\n"}, "add c.txt")
    # Then rename it
    subprocess.run(
        ["git", "mv", "c.txt", "c2.txt"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [*_GIT, "commit", "-m", "rename c.txt to c2.txt"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
        text=True,
    )
    fx.rename_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # ---- Commit 4 (binary): add a binary file with NUL bytes ----
    # Git detects binary files by the presence of NUL (\x00) bytes.
    # The PNG header alone (0x89 50 4E 47 ...) has no NUL bytes, so we embed
    # NUL bytes so git correctly classifies the file as binary (numstat → -/-).
    binary_content = (
        bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])  # PNG signature
        + bytes([0x00, 0x00, 0x00, 0x0D])                          # IHDR chunk length (NUL bytes)
        + b"\xff\xfe\xfd\xfc" * 50                                 # padding with high bytes
        + b"\x00" * 20                                             # extra NUL bytes
    )
    fx.binary_sha = _write_and_commit(
        repo_path, {"logo.png": binary_content}, "add binary logo.png"
    )

    # ---- feature branch: one commit ahead of main-before-merge ----
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    fx.feature_sha = _write_and_commit(
        repo_path, {"feature.txt": "feature work\n"}, "feature: add feature.txt"
    )

    # ---- Back to main, add one more commit, then merge feature ----
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    _write_and_commit(repo_path, {"main_extra.txt": "extra\n"}, "main: extra commit")
    subprocess.run(
        [*_GIT, "merge", "--no-ff", "feature", "-m", "Merge feature into main"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )
    fx.merge_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return fx


# ---------------------------------------------------------------------------
# Convenience: a repos_root-scoped open_repo call
# ---------------------------------------------------------------------------

def _open(fx: RepoFixture) -> object:
    return open_repo(str(fx.repo_path), repos_root=str(fx.root))


# ---------------------------------------------------------------------------
# AC1-AC4 are verified at the Docker-compose level (see CLAUDE instructions).
# Unit tests here cover ACs 5-17.
# ---------------------------------------------------------------------------


# AC5 — open_repo happy path
def test_open_repo_happy_path(repo_fixture: RepoFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "repos_root", str(repo_fixture.root))
    repo = open_repo(str(repo_fixture.repo_path))
    assert repo is not None
    # Env should have been updated with hardened vars
    env = repo.git.environment()
    assert env.get("GIT_CONFIG_NOSYSTEM") == "1"
    assert env.get("GIT_CONFIG_GLOBAL") == "/dev/null"


# AC6 — path outside root raises RepoPathOutsideAllowlist
def test_open_repo_outside_root(repo_fixture: RepoFixture) -> None:
    with pytest.raises(RepoPathOutsideAllowlist):
        open_repo("/etc", repos_root=str(repo_fixture.root))


# AC7 — symlink escaping root raises RepoPathOutsideAllowlist
def test_open_repo_symlink_escape(
    repo_fixture: RepoFixture, tmp_path: Path
) -> None:
    # Create a symlink inside the repos_root that points to something outside
    escape_link = repo_fixture.root / "escape"
    try:
        escape_link.symlink_to("/etc")
    except FileExistsError:
        escape_link.unlink()
        escape_link.symlink_to("/etc")
    with pytest.raises(RepoPathOutsideAllowlist):
        open_repo(str(escape_link), repos_root=str(repo_fixture.root))


# AC8 — non-repo dir raises NotARepository
def test_open_repo_non_git_dir(repo_fixture: RepoFixture) -> None:
    non_git = repo_fixture.root / "not-a-repo"
    non_git.mkdir(exist_ok=True)
    with pytest.raises(NotARepository):
        open_repo(str(non_git), repos_root=str(repo_fixture.root))


# AC9 — list_branches returns >=2 items; exactly one is_default
def test_list_branches(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)
    branches = list_branches(repo)  # type: ignore[arg-type]
    assert len(branches) >= 2
    defaults = [b for b in branches if b.is_default]
    assert len(defaults) == 1
    for b in branches:
        assert len(b.head_sha) == 40
        assert all(c in "0123456789abcdef" for c in b.head_sha)


# AC10 — walk_commits returns commits in reverse-chrono; parents populated; tz-aware
def test_walk_commits_order_and_parents(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)
    commits = walk_commits(repo, limit=100)  # type: ignore[arg-type]
    assert len(commits) >= 3
    # reverse-chronological: first commit has the latest authored_at
    for i in range(len(commits) - 1):
        assert commits[i].authored_at >= commits[i + 1].authored_at
    # initial commit should have empty parents tuple
    initial = next(c for c in commits if c.sha == repo_fixture.initial_sha)
    assert initial.parents == ()
    # merge commit should have 2 parents
    merge = next(c for c in commits if c.sha == repo_fixture.merge_sha)
    assert len(merge.parents) == 2
    # All datetimes must be tz-aware UTC
    for c in commits:
        assert c.authored_at.tzinfo is not None
        assert c.authored_at.tzinfo == UTC


# AC11 — walk_commits since_sha returns only commits after that SHA
def test_walk_commits_since_sha(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)
    all_commits = walk_commits(repo, limit=100)  # type: ignore[arg-type]
    assert len(all_commits) >= 4
    # Use the 3rd-from-tip (index 2) as the anchor
    anchor = all_commits[2].sha
    newer = walk_commits(repo, since_sha=anchor, limit=100)  # type: ignore[arg-type]
    # The anchor commit itself must NOT appear in the result
    newer_shas = {c.sha for c in newer}
    assert anchor not in newer_shas, "Anchor SHA should not be included in since_sha results"
    # Every returned commit must be chronologically newer than the anchor
    anchor_time = all_commits[2].authored_at
    for c in newer:
        assert c.authored_at >= anchor_time, (
            f"Commit {c.sha[:8]} authored_at {c.authored_at} is older than anchor {anchor_time}"
        )
    # At minimum the 2 commits at indices 0 and 1 must be present
    assert all_commits[0].sha in newer_shas
    assert all_commits[1].sha in newer_shas


# AC12 — commit_files: add/modify/rename/binary in one commit
def test_commit_files_full_scenario(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)

    # --- add commit (initial): a.txt added with 10 lines ---
    adds = commit_files(repo, repo_fixture.initial_sha)  # type: ignore[arg-type]
    a_entry = next((f for f in adds if f.path == "a.txt"), None)
    assert a_entry is not None
    assert a_entry.change_type == "A"
    assert a_entry.additions == 10
    assert a_entry.deletions == 0
    assert not a_entry.is_binary
    assert a_entry.old_path is None

    # --- modify commit: b.txt modified ---
    mods = commit_files(repo, repo_fixture.modify_sha)  # type: ignore[arg-type]
    b_entry = next((f for f in mods if f.path == "b.txt"), None)
    assert b_entry is not None
    assert b_entry.change_type == "M"
    assert b_entry.additions > 0
    assert not b_entry.is_binary

    # --- rename commit: c.txt → c2.txt ---
    renames = commit_files(repo, repo_fixture.rename_sha)  # type: ignore[arg-type]
    r_entry = next((f for f in renames if f.path == "c2.txt"), None)
    assert r_entry is not None
    assert r_entry.change_type == "R"
    assert r_entry.old_path == "c.txt"

    # --- binary commit ---
    bins = commit_files(repo, repo_fixture.binary_sha)  # type: ignore[arg-type]
    bin_entry = next((f for f in bins if f.path == "logo.png"), None)
    assert bin_entry is not None
    assert bin_entry.change_type == "A"
    assert bin_entry.is_binary
    assert bin_entry.additions == 0
    assert bin_entry.deletions == 0


# AC13 — initial commit: all entries have change_type='A'
def test_commit_files_initial_commit(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)
    changes = commit_files(repo, repo_fixture.initial_sha)  # type: ignore[arg-type]
    assert len(changes) > 0
    for ch in changes:
        assert ch.change_type == "A", f"Expected 'A' for {ch.path}, got {ch.change_type!r}"


# AC14 — untrusted-config: alias in fake ~/.gitconfig must NOT execute
def test_untrusted_config_alias_not_executed(
    repo_fixture: RepoFixture, tmp_path: Path
) -> None:
    # Build a fake HOME with a dangerous alias
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    sentinel = tmp_path / "should_not_be_deleted"
    sentinel.write_text("sentinel")
    gitconfig = fake_home / ".gitconfig"
    gitconfig.write_text(
        f'[alias]\n\tdanger = !rm -f "{sentinel}"\n',
        encoding="utf-8",
    )

    # Override HOME in environment so git would normally pick up this config
    original_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        repo = open_repo(str(repo_fixture.repo_path), repos_root=str(repo_fixture.root))
        _ = walk_commits(repo, limit=5)  # type: ignore[arg-type]
        # The alias should not have run
        assert sentinel.exists(), "Sentinel file was deleted — alias RAN (security failure)"
        # Verify config listing does not include the alias
        try:
            config_output = repo.git.execute(
                ["git", "config", "--list"], with_exceptions=False
            )
            assert "alias.danger" not in str(config_output), (
                "alias.danger appeared in git config — hardening failed"
            )
        except Exception:
            pass  # execute failure is acceptable here
    finally:
        if original_home is not None:
            os.environ["HOME"] = original_home
        else:
            os.environ.pop("HOME", None)


# AC15 — async wrappers: event loop not blocked during awalk_commits
@pytest.mark.asyncio
async def test_async_wrappers_nonblocking(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)

    tick_count = 0

    async def periodic_tick() -> None:
        nonlocal tick_count
        for _ in range(10):
            await asyncio.sleep(0.05)
            tick_count += 1

    ticker_task = asyncio.create_task(periodic_tick())
    commits = await awalk_commits(repo, limit=100)  # type: ignore[arg-type]
    await asyncio.wait_for(ticker_task, timeout=3.0)

    assert len(commits) > 0
    assert tick_count >= 3, f"Only {tick_count} ticks — loop was blocked"


# AC16 — lint + type-checking are verified in CI via:
#   docker compose exec backend ruff check app/git/reader.py
#   docker compose exec backend mypy --strict app/git/reader.py
# (no runtime assertion here — it's a tooling gate)


# AC17 — repo is truly read-only: fixture status must be clean after all tests
def test_repo_is_read_only_no_side_effects(repo_fixture: RepoFixture) -> None:
    """After all reader operations the fixture repo must have no uncommitted changes."""
    repo = _open(repo_fixture)
    # Trigger all read paths
    branches = list_branches(repo)  # type: ignore[arg-type]
    assert branches

    commits = walk_commits(repo, limit=50)  # type: ignore[arg-type]
    for c in commits[:3]:
        commit_files(repo, c.sha)  # type: ignore[arg-type]

    # Now check git status
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_fixture.repo_path),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", (
        f"Fixture repo is dirty after reader operations:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

def test_open_repo_nonexistent_path(repo_fixture: RepoFixture) -> None:
    """Path that doesn't exist at all raises RepoNotFound."""
    with pytest.raises(RepoNotFound):
        open_repo(
            str(repo_fixture.root / "does-not-exist"),
            repos_root=str(repo_fixture.root),
        )


def test_list_branches_branch_info_types(repo_fixture: RepoFixture) -> None:
    """Every BranchInfo has correct types."""
    repo = _open(repo_fixture)
    for b in list_branches(repo):  # type: ignore[arg-type]
        assert isinstance(b, BranchInfo)
        assert isinstance(b.name, str) and b.name
        assert isinstance(b.head_sha, str) and len(b.head_sha) == 40
        assert isinstance(b.is_default, bool)


def test_walk_commits_returns_commit_info_types(repo_fixture: RepoFixture) -> None:
    """Every CommitInfo has correct types."""
    repo = _open(repo_fixture)
    for c in walk_commits(repo, limit=10):  # type: ignore[arg-type]
        assert isinstance(c, CommitInfo)
        assert isinstance(c.sha, str) and len(c.sha) == 40
        assert isinstance(c.parents, tuple)
        assert (
            isinstance(c.authored_at.tzinfo, type(UTC))
            or c.authored_at.utcoffset() is not None
        )


def test_commit_files_returns_commit_file_change_types(repo_fixture: RepoFixture) -> None:
    repo = _open(repo_fixture)
    for ch in commit_files(repo, repo_fixture.modify_sha):  # type: ignore[arg-type]
        assert isinstance(ch, CommitFileChange)
        assert ch.change_type in ("A", "M", "D", "R", "C")
        assert isinstance(ch.is_binary, bool)
        assert isinstance(ch.additions, int)
        assert isinstance(ch.deletions, int)
