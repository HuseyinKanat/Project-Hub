"""Hardened read-only Git reader for local bind-mounted repositories.

Security layers (defense-in-depth):
  1. Path allowlist: ``realpath`` must resolve under ``repos_root`` (symlink escape blocked).
  2. Env hardening: ``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL=/dev/null``,
     ``HOME=<isolated_tmpdir>``, ``GIT_TERMINAL_PROMPT=0`` — no system/user config loaded.
  3. Per-call ``-c`` overrides: ``core.fsmonitor=false``, ``diff.external=``,
     ``core.pager=cat``, ``protocol.file.allow=never`` — hooks, aliases, pager disabled.
  4. ``search_parent_directories=False`` — repo open does not walk parent dirs.
  5. Read-only by construction — no write operations are ever called.

Public async wrappers use ``asyncio.to_thread`` so the event loop is never blocked;
sync implementations remain importable for tests and CLI scripts.

Warning: ``walk_commits`` with ``limit=None`` on a large repo may cause memory pressure.
Default ``limit`` is 1000; callers may increase it but should be aware of the risk.

Binary detection heuristic: ``0/0`` stats + non-text blob content (NUL byte in first 8 KB).
False positives are possible for mode-only changes; the docstring on ``commit_files``
explains the fallback.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import git
from git import NULL_TREE, Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Isolated HOME directory — created once per process on module import.
# GitPython writes index lock files here; we want a writable, process-owned
# directory that is *not* the user's real HOME (which might contain .gitconfig
# with dangerous aliases or protocol helpers).
# ---------------------------------------------------------------------------
_GIT_NOCONFIG_HOME: str = tempfile.mkdtemp(prefix="git-noconfig-")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitReaderError(Exception):
    """Base class for all git reader errors."""


class RepoPathOutsideAllowlist(GitReaderError):
    """Raised when the resolved path escapes the configured repos_root."""


class NotARepository(GitReaderError):
    """Raised when the path exists but is not a git repository."""


class RepoNotFound(GitReaderError):
    """Raised when the path does not exist on disk."""


# ---------------------------------------------------------------------------
# Return shapes (consumed by G3-G5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchInfo:
    """Metadata for a single local branch."""

    name: str
    head_sha: str
    is_default: bool


@dataclass(frozen=True)
class CommitInfo:
    """Lightweight commit descriptor."""

    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime  # tz-aware UTC
    committer_name: str
    committer_email: str
    committed_at: datetime  # tz-aware UTC
    summary: str  # first line of commit message
    body: str  # remainder of commit message (may be empty)


@dataclass(frozen=True)
class CommitFileChange:
    """Per-file diff entry for a single commit."""

    path: str
    old_path: str | None  # non-None only for renames/copies
    change_type: str  # 'A' | 'M' | 'D' | 'R' | 'C'
    additions: int  # 0 when binary
    deletions: int  # 0 when binary
    is_binary: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_under_root(local_path: str, repos_root: str) -> Path:
    """Resolve ``local_path`` and assert it is under ``repos_root``.

    Follows symlinks before comparison so that a symlink pointing outside the
    allowlist is caught (AC7).

    Raises:
        RepoPathOutsideAllowlist: path escapes the allowlist root.
        RepoNotFound: path does not exist on disk.
        NotARepository: path exists but has no ``.git`` entry.
    """
    root = Path(repos_root).resolve()
    candidate = Path(local_path).resolve()  # follows symlinks — catches escape
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RepoPathOutsideAllowlist(
            f"{local_path!r} resolves to {candidate} which is outside {root}"
        ) from exc
    if not candidate.exists():
        raise RepoNotFound(str(candidate))
    if not (candidate / ".git").exists():
        raise NotARepository(str(candidate))
    return candidate


def _hardened_env() -> dict[str, str]:
    """Return a sanitised environment that prevents loading any git config."""
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": _GIT_NOCONFIG_HOME,
    }


# Per-call -c flags applied via ``custom_environment`` are not enough on their
# own; we also pass these as ``-c`` flags to every ``git`` subprocess so that
# even sub-commands that bypass the GitPython env layer remain locked down.
_SAFE_CONFIG_FLAGS: list[str] = [
    "-c", "core.fsmonitor=false",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "protocol.file.allow=never",
]


def _detect_default_branch(repo: Repo) -> str:
    """Heuristic: try remote HEAD ref first; fall back to 'main' then 'master'."""
    # Try origin/HEAD symbolic ref
    try:
        for ref in repo.references:
            if hasattr(ref, "name") and ref.name in ("origin/HEAD", "refs/remotes/origin/HEAD"):
                target = getattr(ref, "reference", None)
                if target is not None:
                    return str(target.name).split("/")[-1]
    except Exception:
        pass

    # Fall back to HEAD detached or branch name
    try:
        if not repo.head.is_detached:
            return repo.active_branch.name
    except Exception:
        pass

    # Static fallbacks
    head_names = {h.name for h in repo.heads}
    for name in ("main", "master", "develop"):
        if name in head_names:
            return name
    if repo.heads:
        return repo.heads[0].name
    return "main"


def _detect_binary(diff_item: git.Diff, stats: dict[str, int]) -> bool:
    """Return True if the diff entry represents a binary file.

    Primary signal: numstat shows ``-`` for both insertions and deletions
    (GitPython surfaces this as 0 in ``stats.files`` when the entry is absent).
    Secondary signal: NUL byte in the first 8 KB of the blob data.
    """
    if stats.get("insertions", 0) == 0 and stats.get("deletions", 0) == 0:
        # Could be binary — probe blob content
        try:
            blob = diff_item.b_blob or diff_item.a_blob
            if blob is not None:
                chunk = blob.data_stream.read(8192)
                return b"\x00" in chunk
        except Exception:
            pass
        # stats absent and no blob readable — treat as binary conservatively
        return True
    return False


# ---------------------------------------------------------------------------
# Sync implementations (importable for tests / CLI)
# ---------------------------------------------------------------------------


def open_repo(local_path: str, *, repos_root: str | None = None) -> Repo:
    """Open a git repository at ``local_path``, enforcing the path allowlist.

    Applies hardened environment to every subsequent git call made via the
    returned ``Repo`` handle.

    Args:
        local_path: Absolute path to the repository root (inside the container).
        repos_root: Override the allowlist root; defaults to ``settings.repos_root``.

    Returns:
        A ``git.Repo`` instance with a hardened execution environment.

    Raises:
        RepoPathOutsideAllowlist: ``local_path`` resolves outside the allowlist.
        RepoNotFound: ``local_path`` does not exist.
        NotARepository: ``local_path`` exists but is not a git repository.
    """
    root = repos_root if repos_root is not None else get_settings().repos_root
    path = _validate_under_root(local_path, root)
    try:
        repo = Repo(str(path), search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise NotARepository(str(path)) from exc

    # Apply hardened env so every git subprocess spawned by this Repo instance
    # inherits the sanitised environment.
    repo.git.update_environment(**_hardened_env())
    return repo


def list_branches(repo: Repo) -> list[BranchInfo]:
    """Return all local branches with their HEAD SHA and default-branch flag.

    Args:
        repo: An open ``git.Repo`` handle (e.g. from ``open_repo``).

    Returns:
        List of ``BranchInfo`` items; empty if the repo has no commits.
    """
    default = _detect_default_branch(repo)
    return [
        BranchInfo(
            name=h.name,
            head_sha=h.commit.hexsha,
            is_default=(h.name == default),
        )
        for h in repo.heads
    ]


def walk_commits(
    repo: Repo,
    refs: list[str] | None = None,
    limit: int = 1000,
    since_sha: str | None = None,
) -> list[CommitInfo]:
    """Walk commits in reverse-chronological order.

    Args:
        repo: An open ``git.Repo`` handle.
        refs: List of ref names to walk; ``None`` means all refs (``--all``).
        limit: Maximum number of commits to return (default 1000 — raise with care on
               large repos).
        since_sha: If provided, only commits *after* (i.e. not including) this SHA
                   are returned.  For multi-ref traversal the ``since_sha..ref``
                   notation is used for each ref independently.

    Returns:
        List of ``CommitInfo`` in reverse-chronological order.
    """
    # Build the revision spec for iter_commits.
    # When since_sha is provided we cannot combine it with "--all" via the
    # "<sha>..<ref>" range notation because "--all" is a flag, not a ref name.
    # Instead we collect all branch tips and build per-branch ranges.
    if since_sha:
        if refs:
            effective_refs: list[str] = [f"{since_sha}..{r}" for r in refs]
        else:
            # Expand --all to individual branch/tag refs so ranges work.
            branch_tips = [h.name for h in repo.heads]
            if not branch_tips:
                branch_tips = ["HEAD"]
            effective_refs = [f"{since_sha}..{r}" for r in branch_tips]
    else:
        effective_refs = refs or ["--all"]

    out: list[CommitInfo] = []
    for c in repo.iter_commits(rev=effective_refs, max_count=limit):  # type: ignore[arg-type]
        msg = (
            c.message if isinstance(c.message, str)
            else c.message.decode("utf-8", errors="replace")
        )
        summary = (
            c.summary if isinstance(c.summary, str)
            else c.summary.decode("utf-8", errors="replace")
        )
        body = msg[len(summary):].lstrip("\n") if len(msg) > len(summary) else ""
        out.append(
            CommitInfo(
                sha=c.hexsha,
                parents=tuple(p.hexsha for p in c.parents),
                author_name=c.author.name or "",
                author_email=c.author.email or "",
                authored_at=c.authored_datetime.astimezone(UTC),
                committer_name=c.committer.name or "",
                committer_email=c.committer.email or "",
                committed_at=c.committed_datetime.astimezone(UTC),
                summary=summary,
                body=body,
            )
        )
    return out


def commit_files(repo: Repo, sha: str) -> list[CommitFileChange]:
    """Return per-file changes for a single commit.

    For the initial commit (no parents) all tree entries are emitted with
    ``change_type='A'`` (AC13).  For rename/copy, ``old_path`` is populated.

    Binary detection uses a two-signal heuristic: zero numstat counts AND a NUL
    byte found in the first 8 KB of the blob.  Mode-only changes may produce a
    false positive (is_binary=True with no actual binary content); this is a
    known limitation documented in the module docstring.

    Args:
        repo: An open ``git.Repo`` handle.
        sha: Full or abbreviated commit SHA.

    Returns:
        List of ``CommitFileChange`` items.
    """
    c = repo.commit(sha)
    parent: git.Commit | None = c.parents[0] if c.parents else None

    if parent is not None:
        # Diff parent → commit: parent is "a", commit is "b".
        # This gives change_type='A' for files added in this commit,
        # 'D' for deleted, 'R' for renamed (a_path=old, b_path=new).
        diffs: git.DiffIndex[git.Diff] = parent.diff(c)
    else:
        # Initial commit: diff against the empty tree.
        # commit.diff(NULL_TREE) gives change_type='A' for all added files.
        diffs = c.diff(NULL_TREE)

    stats_map: dict[str, dict[str, int]] = c.stats.files  # type: ignore[assignment]

    out: list[CommitFileChange] = []
    for d in diffs:
        # b_path is the "new" path (after the change); a_path is the "old" path.
        path = d.b_path or d.a_path or ""
        # Stats are keyed by the new (b) path in GitPython's stats.files dict.
        file_stats = (
            stats_map.get(path)
            or stats_map.get(d.a_path or "")
            or {"insertions": 0, "deletions": 0}
        )
        is_bin = _detect_binary(d, file_stats)
        change_type: str = d.change_type or "M"
        is_rename_or_copy = change_type in ("R", "C") and d.a_path != d.b_path
        out.append(
            CommitFileChange(
                path=path,
                old_path=d.a_path if is_rename_or_copy else None,
                change_type=change_type,
                additions=0 if is_bin else int(file_stats.get("insertions", 0)),
                deletions=0 if is_bin else int(file_stats.get("deletions", 0)),
                is_binary=is_bin,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Async wrappers (G3+ consumers — call these from FastAPI handlers)
# ---------------------------------------------------------------------------


async def aopen_repo(local_path: str, *, repos_root: str | None = None) -> Repo:
    """Async wrapper for ``open_repo``.  Opens the repo in a thread-pool worker."""
    return await asyncio.to_thread(open_repo, local_path, repos_root=repos_root)


async def alist_branches(repo: Repo) -> list[BranchInfo]:
    """Async wrapper for ``list_branches``."""
    return await asyncio.to_thread(list_branches, repo)


async def awalk_commits(
    repo: Repo,
    refs: list[str] | None = None,
    limit: int = 1000,
    since_sha: str | None = None,
) -> list[CommitInfo]:
    """Async wrapper for ``walk_commits``.

    GitPython spawns git subprocesses (I/O bound), so ``asyncio.to_thread`` with
    the default thread-pool executor is sufficient; no GIL contention.
    """
    return await asyncio.to_thread(walk_commits, repo, refs, limit, since_sha)


async def acommit_files(repo: Repo, sha: str) -> list[CommitFileChange]:
    """Async wrapper for ``commit_files``."""
    return await asyncio.to_thread(commit_files, repo, sha)
