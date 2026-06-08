"""Git filesystem auto-detection — C2 (PH-222 base, PH-229 per-board root).

Read-only scan for git working copies, so the frontend can offer auto-detected
repositories when a user adds one (no manual path typing required).

Scan root — per board (PH-229)
------------------------------
The scan START is now THE REQUESTING BOARD's path, not the global ``/repos/``
mount root. ``Board.repos_path`` holds the board's HOST path (e.g.
``/Users/huseyinkanat/Documents/kims``); ``repo_paths.to_container_path`` swaps
the ``HOST_HOME`` prefix for ``repos_root`` (``/repos``) so the walk starts at the
board's own subtree (e.g. ``/repos/Documents/kims``). A board with a null path, a
``RepoPathError`` (path outside ``HOST_HOME`` / contains ``..``), or a container
root that does not exist (typo / not mounted) → ``[]`` (200, NEVER 500, NEVER a
fall-back to project-hub). Each board therefore scans ONLY its own path.

Two roots, one allowlist — the key correctness point (PH-229)
-------------------------------------------------------------
``_scan_sync`` splits two distinct roots that PH-222 conflated:

- ``scan_root``      — the WALK START (the board's container path). May be deep.
- ``allowlist_root`` — the hardened reader's security boundary, which STAYS
  ``settings.repos_root`` (``/repos``). Every ``reader.open_repo(path,
  repos_root=allowlist_root)`` is validated against ``/repos`` exactly as before,
  so ``_validate_under_root`` is UNCHANGED — a board path is NOT its own allowlist
  root (that would weaken the guard). We scan a sub-tree but keep the boundary at
  the mount root.

Root-as-candidate vs children (PH-229)
--------------------------------------
A board path may itself BE a repo (``/repos/Documents/kims``) OR be a parent dir
holding several repos. So: if ``scan_root`` itself has a ``.git`` → it is the sole
candidate and we do NOT descend (mirrors the no-nested-fan-out rule). Otherwise we
shallow-scan its immediate children (depth ≤ 2), the PH-222 behaviour.

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
- **Allowlist**: candidate paths come ONLY from ``os.scandir`` under ``scan_root``
  (itself under the mount); none are built from user input. The reader re-validates
  every open under ``allowlist_root`` (``/repos``).
- **Shallow walk**: depth ≤ 2 below the scan root. A dir that already IS a repo is
  not descended into (no ``node_modules/.git`` fan-out).
- **Result cap**: at most ``max_results`` candidates (default 100).
- **Wall-clock budget**: the scan stops after ``time_budget_seconds`` (default 5.0s)
  so a pathological tree can never hang the request.
- **Graceful**: a missing/empty ``scan_root``, a permission error mid-scan, a
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
from app.services.repo_paths import RepoPathError, to_container_path
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
    allowlist_root: str,
    linked_realpaths: set[str],
) -> DetectedRepo | None:
    """Open ``candidate_dir`` via the hardened reader and build a ``DetectedRepo``.

    ``allowlist_root`` is the reader's security boundary (``settings.repos_root`` =
    ``/repos``), NOT the scan start. ``_validate_under_root`` rejects any candidate
    whose realpath escapes it (e.g. a symlink), exactly as in PH-222.

    Returns ``None`` (caller skips) when the directory is not a real repo, escapes
    the allowlist, or git raises — never propagates an error to the request path.
    """
    path_str = str(candidate_dir)
    try:
        repo = reader.open_repo(path_str, repos_root=allowlist_root)
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


def _walk_children(
    root: Path,
    reader_root: str,
    linked_realpaths: set[str],
    *,
    max_results: int,
    deadline: float,
) -> list[DetectedRepo]:
    """Shallow depth ≤ ``_MAX_DEPTH`` walk of ``root``'s descendants.

    A directory holding a ``.git`` is a candidate and is NOT descended into (no
    nested-``.git`` fan-out); a directory WITHOUT ``.git`` is descended one more
    level (a container dir holding several repos), until the depth budget is hit.
    Bounded by ``max_results`` and the monotonic ``deadline``. Extracted from
    ``_scan_sync`` (PH-229) so each function stays within the cognitive-complexity
    budget — behaviour is identical to the inlined PH-222 loop.
    """
    candidates: list[DetectedRepo] = []
    # BFS-ish stack of (dir, depth). Depth 1 = immediate child of the scan root.
    stack: list[tuple[Path, int]] = [(child, 1) for child in _iter_child_dirs(root)]

    while stack:
        if len(candidates) >= max_results or time.monotonic() >= deadline:
            break
        directory, depth = stack.pop()

        if _has_git_entry(directory):
            built = _build_candidate(directory, reader_root, linked_realpaths)
            if built is not None:
                candidates.append(built)
            # This dir is a repo → do NOT descend into it (no nested-.git fan-out).
            continue

        # No .git here: descend one more level if within the depth budget.
        if depth < _MAX_DEPTH:
            stack.extend((sub, depth + 1) for sub in _iter_child_dirs(directory))

    return candidates


def _scan_sync(
    scan_root: str,
    linked_realpaths: set[str],
    *,
    max_results: int,
    time_budget_seconds: float,
    allowlist_root: str | None = None,
) -> list[DetectedRepo]:
    """Synchronous shallow scan body (run off-loop via ``asyncio.to_thread``).

    Two roots (PH-229):
    - ``scan_root``      — the WALK START (the board's container path).
    - ``allowlist_root`` — the hardened reader's security boundary; defaults to
      ``scan_root`` for back-compat but in production is ALWAYS the mount root
      (``settings.repos_root`` = ``/repos``), so a board sub-tree scan never
      weakens ``_validate_under_root``.

    Behaviour:
    - **Root-as-candidate**: if ``scan_root`` itself holds a ``.git`` it IS the
      repo → it is the sole candidate and we do NOT descend (a board path like
      ``/repos/Documents/kims`` is the repo, not a container of repos).
    - **Children scan**: otherwise walk to depth ``_MAX_DEPTH`` via
      ``_walk_children`` — an immediate child that holds a ``.git`` is a candidate;
      a child WITHOUT ``.git`` is descended one more level (a container dir holding
      several repos). A directory that already IS a repo is never descended into.

    Bounded by ``max_results`` and ``time_budget_seconds``.
    """
    root = Path(scan_root)
    if not root.is_dir():
        return []  # missing / empty / not-a-dir scan root → empty list (200)

    # The reader allowlist stays the mount root; only the WALK START is the board
    # path. (None → back-compat: the scan root doubles as the allowlist, used by
    # the bounds unit tests that pass a self-contained temp dir.)
    reader_root = allowlist_root if allowlist_root is not None else scan_root

    # Root-as-candidate: the board path IS a repo → return it once, do NOT descend
    # (mirrors the no-nested-.git-fan-out rule applied to children below).
    if _has_git_entry(root):
        built = _build_candidate(root, reader_root, linked_realpaths)
        return [built] if built is not None else []

    return _walk_children(
        root,
        reader_root,
        linked_realpaths,
        max_results=max_results,
        deadline=time.monotonic() + time_budget_seconds,
    )


def _resolve_scan_root(board: Board) -> str | None:
    """Translate ``board.repos_path`` (HOST) → in-container scan start, or None.

    Returns ``None`` (caller → ``[]``) when the board has no path, or when
    ``to_container_path`` raises ``RepoPathError`` (path outside ``HOST_HOME`` or
    containing ``..``). NEVER raises — degrades gracefully, no fall-back to the
    global ``/repos`` root (each board scans ONLY its own path).
    """
    host_path = board.repos_path
    if not host_path:
        return None
    try:
        return to_container_path(host_path)
    except RepoPathError:
        logger.debug(
            "detect: board=%s repos_path=%r unresolvable (RepoPathError) → []",
            board.key,
            host_path,
        )
        return None


async def detect_repositories(
    session: AsyncSession,
    board: Board,
    *,
    repos_root: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
) -> list[DetectedRepo]:
    """Scan THE BOARD's path for git working copies (read-only).

    The scan START is the board's container path (``board.repos_path`` translated
    HOST→container via ``to_container_path``); the reader allowlist STAYS
    ``settings.repos_root`` (``/repos``). A null path, a ``RepoPathError``, or a
    non-existent container root all degrade to ``[]`` (200, never 500, never a
    fall-back to project-hub).

    Args:
        session: Active DB session (used only to read this board's repositories).
        board: The board whose ``repos_path`` drives the scan root and whose linked
            repos drive the ``already_linked`` flag.
        repos_root: TEST override — when given, this is the scan start AND the
            allowlist (a self-contained temp dir), bypassing ``board.repos_path``
            translation. Production callers omit it so the board drives the root.
        max_results: Hard cap on returned candidates (DoS guard).
        time_budget_seconds: Wall-clock budget for the scan (hang guard).

    Returns:
        A list of ``DetectedRepo`` candidates (possibly empty). Never raises for
        missing/empty roots, permission errors, non-git dirs, or git failures.
    """
    scan_root: str
    if repos_root is not None:
        # Test override: scan_root == allowlist_root (self-contained temp tree).
        scan_root = repos_root
        allowlist_root = repos_root
    else:
        resolved = _resolve_scan_root(board)
        # Null path / RepoPathError → no scan, empty list (NO project-hub fallback).
        if resolved is None:
            return []
        scan_root = resolved
        # Security boundary stays the mount root regardless of the board sub-tree.
        allowlist_root = get_settings().repos_root

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
        scan_root,
        linked_realpaths,
        max_results=max_results,
        time_budget_seconds=time_budget_seconds,
        allowlist_root=allowlist_root,
    )
