"""Git filesystem auto-detection — C2 (PH-222).

Read-only scan of the allowlisted mount root (``settings.repos_root``, default
``/repos/``) for git working copies, so the frontend can offer auto-detected
repositories when a user adds one (no manual path typing required).

Design — reuse, don't reinvent
-------------------------------
Every git interaction routes through the hardened reader (``app.git.reader``):
``open_repo`` validates the path under the allowlist (``_validate_under_root`` →
``realpath`` + ``relative_to(repos_root)``, so a symlink escaping the allowlist
is caught and skipped), applies the sanitised env + per-call ``-c`` safety flags,
and opens with ``search_parent_directories=False``. There is NO fresh
``subprocess.run`` here — detection inherits all of the reader's defense-in-depth
for free.

Safety / bounds
---------------
- **Allowlist**: candidate paths come ONLY from ``os.scandir`` under ``repos_root``;
  none are built from user input. The reader re-validates every open under the root.
- **Shallow walk**: depth ≤ 2 below the scan root. A dir that already IS a repo is
  not descended into (no ``node_modules/.git`` fan-out).
- **Result cap**: at most ``max_results`` candidates (default 100).
- **Wall-clock budget**: the scan stops after ``time_budget_seconds`` (default 5.0s)
  so a pathological tree can never hang the request.
- **Graceful**: a missing/empty ``repos_root``, a permission error mid-scan, a
  non-git dir, or any ``GitReaderError``/``git.GitError`` → skip that entry; the
  function never raises for those (callers return 200 with a partial/empty list).

Read-only: this module never mutates the filesystem nor creates ``Repository``
rows; it only reports candidates. The add happens via PH-221's ``POST /repositories``.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import git
from git import Repo
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Board
from app.git import reader
from app.git.reader import GitReaderError
from app.schemas import DetectedRepo, Provider
from app.services.repositories import list_repositories

logger = get_logger(__name__)

# Bounds (constants — overridable via the function signature for tests).
_DEFAULT_MAX_RESULTS = 100
_DEFAULT_TIME_BUDGET_SECONDS = 5.0
_MAX_DEPTH = 2  # directory levels below the scan root we are willing to descend


def _provider_guess(remote_url: str | None) -> Provider:
    """Infer the provider from a remote URL host (pure substring, no network).

    ``github.com`` → ``"github"``, ``gitlab`` → ``"gitlab"``, otherwise (or when
    there is no remote) → ``"local"``. Substring matching is sufficient and avoids
    any regex-backtracking risk; lines up with ``RepositoryCreate.provider``.
    """
    if not remote_url:
        return "local"
    lowered = remote_url.lower()
    if "github.com" in lowered:
        return "github"
    if "gitlab" in lowered:
        return "gitlab"
    return "local"


def _origin_url(repo: Repo) -> str | None:
    """Return the ``origin`` remote URL, else the first remote's URL, else None.

    Reads from the already-open hardened handle (GitPython parses ``.git/config``;
    no extra subprocess). Any access error → None (treated as "no remote").
    """
    try:
        remotes = repo.remotes
    except Exception:
        return None
    if not remotes:
        return None
    # Prefer the remote literally named 'origin'; fall back to the first remote.
    try:
        return str(repo.remotes["origin"].url)
    except Exception:
        pass
    try:
        return str(next(iter(remotes)).url)
    except Exception:
        return None


def _build_candidate(
    candidate_dir: Path,
    repos_root: str,
    linked_realpaths: set[str],
) -> DetectedRepo | None:
    """Open ``candidate_dir`` via the hardened reader and build a ``DetectedRepo``.

    Returns ``None`` (caller skips) when the directory is not a real repo, escapes
    the allowlist, or git raises — never propagates an error to the request path.
    """
    path_str = str(candidate_dir)
    try:
        repo = reader.open_repo(path_str, repos_root=repos_root)
    except (GitReaderError, git.GitError, OSError):
        # NotARepository / RepoPathOutsideAllowlist / RepoNotFound / any git error
        # → just not a candidate. Skip silently (do NOT 500).
        return None
    except Exception:  # pragma: no cover - defensive last-resort guard
        logger.debug("detect: unexpected error opening %s", path_str, exc_info=True)
        return None

    try:
        remote_url = _origin_url(repo)
        try:
            default_branch: str | None = reader._detect_default_branch(repo)
        except Exception:
            default_branch = None
    finally:
        # Release any cached git subprocess resources held by the handle.
        try:
            repo.close()
        except Exception:  # pragma: no cover - close is best-effort
            pass

    # already_linked: compare resolved candidate path against the board's
    # resolved Repository.local_path set.
    try:
        candidate_real = str(candidate_dir.resolve())
    except OSError:
        candidate_real = path_str
    already_linked = candidate_real in linked_realpaths

    return DetectedRepo(
        local_path=path_str,
        name=candidate_dir.name or path_str,
        is_git=True,
        remote_url=remote_url,
        default_branch=default_branch,
        provider_guess=_provider_guess(remote_url),
        already_linked=already_linked,
    )


def _has_git_entry(directory: Path) -> bool:
    """True if ``directory`` contains a ``.git`` entry (dir OR file/worktree)."""
    try:
        return (directory / ".git").exists()
    except OSError:
        return False


def _iter_child_dirs(directory: Path) -> list[Path]:
    """Return immediate sub-directories of ``directory``; [] on any scandir error.

    Symlinked dirs are included (the reader's allowlist check rejects any that
    resolve outside the root). Skips entries that raise mid-iteration.
    """
    children: list[Path] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        children.append(Path(entry.path))
                except OSError:
                    # Permission / broken symlink on this entry — skip it.
                    continue
    except OSError:
        return []
    return children


def _scan_sync(
    repos_root: str,
    linked_realpaths: set[str],
    *,
    max_results: int,
    time_budget_seconds: float,
) -> list[DetectedRepo]:
    """Synchronous shallow scan body (run off-loop via ``asyncio.to_thread``).

    Walks ``repos_root`` to depth ``_MAX_DEPTH``: an immediate child that holds a
    ``.git`` is a candidate; a child WITHOUT ``.git`` is descended one more level
    (a container dir holding several repos). A directory that already IS a repo is
    never descended into. Bounded by ``max_results`` and ``time_budget_seconds``.
    """
    root = Path(repos_root)
    if not root.is_dir():
        return []  # missing / empty / not-a-dir scan root → empty list (200)

    deadline = time.monotonic() + time_budget_seconds
    candidates: list[DetectedRepo] = []

    # BFS-ish stack of (dir, depth). Depth 1 = immediate child of repos_root.
    stack: list[tuple[Path, int]] = [(child, 1) for child in _iter_child_dirs(root)]

    while stack:
        if len(candidates) >= max_results or time.monotonic() >= deadline:
            break
        directory, depth = stack.pop()

        if _has_git_entry(directory):
            built = _build_candidate(directory, repos_root, linked_realpaths)
            if built is not None:
                candidates.append(built)
            # This dir is a repo → do NOT descend into it (no nested-.git fan-out).
            continue

        # No .git here: descend one more level if within the depth budget.
        if depth < _MAX_DEPTH:
            stack.extend((sub, depth + 1) for sub in _iter_child_dirs(directory))

    return candidates


async def detect_repositories(
    session: AsyncSession,
    board: Board,
    *,
    repos_root: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
) -> list[DetectedRepo]:
    """Scan the allowlisted root for git working copies (read-only).

    Args:
        session: Active DB session (used only to read this board's repositories).
        board: The board whose linked repos drive the ``already_linked`` flag.
        repos_root: Override the scan root; defaults to ``settings.repos_root``.
        max_results: Hard cap on returned candidates (DoS guard).
        time_budget_seconds: Wall-clock budget for the scan (hang guard).

    Returns:
        A list of ``DetectedRepo`` candidates (possibly empty). Never raises for
        missing/empty roots, permission errors, non-git dirs, or git failures.
    """
    root = repos_root if repos_root is not None else get_settings().repos_root

    # Resolve THIS board's linked repo paths once (realpath, so /repos/x,
    # /repos/x/, and a symlink to it all compare equal against candidates).
    repos = await list_repositories(session, board)
    linked_realpaths: set[str] = set()
    for repo_row in repos:
        try:
            linked_realpaths.add(str(Path(repo_row.local_path).resolve()))
        except OSError:
            linked_realpaths.add(repo_row.local_path)

    # The directory walk + per-candidate reader opens are blocking I/O; run the
    # whole scan body in a worker thread so the event loop is never blocked.
    return await asyncio.to_thread(
        _scan_sync,
        root,
        linked_realpaths,
        max_results=max_results,
        time_budget_seconds=time_budget_seconds,
    )
