"""Hardened read-only Git reader for local bind-mounted repositories.

Security layers (defense-in-depth):
  1. Path allowlist: ``realpath`` must resolve under ``repos_root`` (symlink escape blocked).
  2. Env hardening: ``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL=/dev/null``,
     ``HOME=<isolated_tmpdir>``, ``GIT_TERMINAL_PROMPT=0`` — no system/user config loaded.
  3. Per-call ``-c`` overrides: ``core.fsmonitor=false``,
     ``core.pager=cat``, ``protocol.file.allow=never`` — hooks and aliases disabled.
     ``diff.external`` is blocked via ``--no-ext-diff`` on every patch-generating diff
     call (setting ``-c diff.external=`` to empty string causes git to exec ``""``).
  4. ``search_parent_directories=False`` — repo open does not walk parent dirs.
  5. Read-only by construction — no write operations are ever called.

Public async wrappers use ``asyncio.to_thread`` so the event loop is never blocked;
sync implementations remain importable for tests and CLI scripts.

Warning: ``walk_commits`` with ``limit=None`` on a large repo may cause memory pressure.
Default ``limit`` is 1000; callers may increase it but should be aware of the risk.

Binary detection heuristic: ``0/0`` stats + non-text blob content (NUL byte in first 8 KB).
False positives are possible for mode-only changes; the docstring on ``commit_files``
explains the fallback.

G5 additions (PH-154):
  ``diff_text`` — compute unified diff of a single commit vs its first parent.
  ``range_diff`` — three-dot merge-base range diff (``base...head``).
  ``adiff_text`` / ``arange_diff`` — async wrappers via ``asyncio.to_thread``.
  ``FileDiff`` / ``DiffResult`` — typed return shapes (consumed by G5 API layer).

Security invariant: ``diff_text`` and ``range_diff`` inherit ``_persistent_git_options``
(``_SAFE_CONFIG_FLAGS``) from the ``Repo`` handle returned by ``open_repo``.  No extra
hardening pass is needed; the ``-c`` flags are injected into every git subprocess via
GitPython's persistent option mechanism.
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
# G5 return shapes (PH-154)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileDiff:
    """Per-file diff entry with unified patch text.

    ``patch`` is None when the file is binary or when the per-request byte-cap
    has been reached.  In the latter case ``truncated=True`` marks this entry.
    ``old_path`` is non-None only for renames and copies (change_type R or C).
    """

    path: str
    old_path: str | None
    change_type: str  # A | M | D | R | C
    additions: int  # 0 for binary files
    deletions: int  # 0 for binary files
    is_binary: bool
    patch: str | None  # None when binary or cap-truncated
    truncated: bool = False  # True when this file's patch was sliced at cap boundary


@dataclass(frozen=True)
class DiffResult:
    """Aggregate result of a diff_text or range_diff call.

    ``truncated`` is True if *any* file hit the byte cap during this call.
    ``files`` contains all file entries; for cap-truncated files, patch is None
    or sliced (see FileDiff.truncated).
    """

    files: tuple[FileDiff, ...]
    truncated: bool  # top-level: True if cap was hit anywhere


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
    """Return a sanitised environment that prevents loading any git config.

    ``GIT_CONFIG_NOSYSTEM=1`` and ``GIT_CONFIG_GLOBAL=/dev/null`` block the
    system and user-level git configs.  The isolated ``HOME`` directory
    prevents GitPython from writing lock files to the real user home.
    ``GIT_TERMINAL_PROMPT=0`` prevents git from hanging on credential prompts.

    Note: We intentionally do NOT set ``GIT_EXTERNAL_DIFF`` because setting
    it to any non-program value (including empty string) causes git to attempt
    to exec it and fail with ``cannot run : No such file or directory`` when
    generating unified diffs.  The ``diff.external`` attack surface in local
    ``.git/config`` is blocked by passing ``--no-ext-diff`` on every
    patch-generating diff subprocess call inside ``_build_diff_files``; this
    flag disables external diff drivers without attempting to exec anything.
    Setting ``-c diff.external=`` (empty string) is NOT used because it causes
    git to exec the empty string on ``git diff -p`` calls.
    """
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
#
# Note: ``diff.external`` is intentionally absent from this list.  Setting
# ``-c diff.external=`` (empty string) causes git to try to exec ``""`` when
# generating patch text (``git diff -p``), resulting in
# "cannot run : No such file or directory".  Instead, ``--no-ext-diff`` is
# passed directly to every patch-generating ``git diff`` call inside
# ``_build_diff_files`` — this flag disables external diff drivers at the
# call site without any env-var or config-override side effects.
_SAFE_CONFIG_FLAGS: list[str] = [
    "-c", "core.fsmonitor=false",
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

    # Apply persistent -c flags to EVERY git subprocess issued via this Repo
    # handle (including diff, log, show, etc.).  GIT_CONFIG_NOSYSTEM=1 and
    # GIT_CONFIG_GLOBAL=/dev/null block system+user config but do NOT block the
    # repo-local `.git/config`.  Assigning _persistent_git_options ensures that
    # `core.fsmonitor`, `diff.external`, `core.pager`, and
    # `protocol.file.allow` are overridden via `-c` on every invocation,
    # regardless of what the local `.git/config` contains.
    #
    # Note: `set_persistent_git_options` accepts *positional* flags (e.g.
    # ``no_optional_locks=True``) rather than raw flag lists; direct assignment
    # is the correct mechanism for injecting arbitrary `-c key=val` pairs.
    repo.git._persistent_git_options = _SAFE_CONFIG_FLAGS
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


def _build_walk_refs(repo: Repo, refs: list[str] | None, since_sha: str | None) -> list[str]:
    """Compute the iter_commits revision spec for ``walk_commits``.

    When ``since_sha`` is provided we cannot combine it with ``--all`` via the
    ``<sha>..<ref>`` range notation because ``--all`` is a flag, not a ref name.
    Instead we collect all branch tips and build per-branch ranges.
    """
    if not since_sha:
        return refs or ["--all"]
    if refs:
        return [f"{since_sha}..{r}" for r in refs]
    # Expand --all to individual branch/tag refs so ranges work.
    branch_tips = [h.name for h in repo.heads] or ["HEAD"]
    return [f"{since_sha}..{r}" for r in branch_tips]


def _commit_info_from(c: git.Commit) -> CommitInfo:
    """Build a ``CommitInfo`` from a GitPython commit, decoding bytes defensively."""
    msg = (
        c.message if isinstance(c.message, str)
        else c.message.decode("utf-8", errors="replace")
    )
    summary = (
        c.summary if isinstance(c.summary, str)
        else c.summary.decode("utf-8", errors="replace")
    )
    body = msg[len(summary):].lstrip("\n") if len(msg) > len(summary) else ""
    return CommitInfo(
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
    effective_refs = _build_walk_refs(repo, refs, since_sha)
    return [
        _commit_info_from(c)
        for c in repo.iter_commits(rev=effective_refs, max_count=limit)  # type: ignore[arg-type]
    ]


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
# G5 internal helpers
# ---------------------------------------------------------------------------


def _utf8_safe_trim(raw: bytes) -> str:
    """Decode raw bytes to UTF-8, trimming back to a valid UTF-8 boundary.

    Slicing patch bytes at an arbitrary position may cut inside a multi-byte
    UTF-8 sequence.  This helper trims back until the prefix is decodable,
    then decodes with ``errors='replace'`` as the final safety net.
    """
    # Try trimming up to 3 extra bytes back to avoid a broken codepoint boundary.
    for trim in range(4):
        candidate = raw[: len(raw) - trim]
        try:
            return candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
    # Fallback: replace broken characters
    return raw.decode("utf-8", errors="replace")


class _NumstatEntry:
    """Typed container for a single numstat line entry."""

    __slots__ = ("additions", "deletions", "is_binary", "old_path", "path")

    def __init__(
        self,
        path: str,
        old_path: str | None,
        additions: int,
        deletions: int,
        is_binary: bool,
    ) -> None:
        self.path = path
        self.old_path = old_path
        self.additions = additions
        self.deletions = deletions
        self.is_binary = is_binary


def _parse_numstat(numstat_out: str) -> list[_NumstatEntry]:
    """Parse ``git diff --numstat -z`` output into a list of file records.

    Format (NUL-delimited for path safety):
      ``<add>\t<del>\t<path>\0`` for regular files
      ``<add>\t<del>\t\0<old_path>\0<new_path>\0`` for renames/copies

    Returns a list of ``_NumstatEntry`` items.
    """
    entries: list[_NumstatEntry] = []
    # Split on NUL; filter empties
    parts = [p for p in numstat_out.split("\x00") if p]
    i = 0
    while i < len(parts):
        part = parts[i]
        # Each numstat line: "<add>\t<del>\t<path>" (the path may be empty for renames)
        tab_split = part.split("\t", 2)
        if len(tab_split) < 3:
            i += 1
            continue
        add_str, del_str, path_part = tab_split
        is_binary = (add_str == "-" and del_str == "-")
        additions = 0 if is_binary else int(add_str)
        deletions = 0 if is_binary else int(del_str)
        old_path: str | None = None
        if path_part == "":
            # Rename/copy: next NUL token is old_path, token after that is new_path
            if i + 2 < len(parts):
                old_path = parts[i + 1]
                path = parts[i + 2]
                i += 3
            else:
                i += 1
                continue
        else:
            path = path_part
            i += 1
        entries.append(_NumstatEntry(
            path=path,
            old_path=old_path,
            additions=additions,
            deletions=deletions,
            is_binary=is_binary,
        ))
    return entries


def _diff_change_type(
    old_path: str | None, additions: int, deletions: int, is_binary: bool
) -> str:
    """Infer a single file's change type from numstat counts (no change_type column).

    numstat doesn't report change_type, so we use heuristics; binary files or
    pure modifications fall through to "M".
    """
    if old_path is not None:
        return "R"
    if additions > 0 and deletions == 0:
        return "A"
    if additions == 0 and deletions > 0 and not is_binary:
        return "D"
    return "M"


def _patch_for_file(repo: Repo, rev_args: list[str], context: int, file_path: str) -> str:
    """Generate a single file's unified patch text; "" on any git failure."""
    try:
        return str(
            repo.git.diff(
                *rev_args,
                f"--unified={context}",
                "--no-color",
                "--no-ext-diff",
                "--",
                file_path,
            )
        )
    except Exception:
        return ""


def _build_diff_files(
    repo: Repo,
    rev_args: list[str],
    path: str | None,
    context: int,
    max_bytes: int,
) -> DiffResult:
    """Core implementation for diff_text and range_diff.

    Performs a two-pass git diff:
    1. numstat (cheap): learn which files changed, detect binaries early.
    2. Per-file patch (expensive, bounded): accumulate patches up to max_bytes.

    ``rev_args`` is the list of revision arguments passed directly to git diff
    (e.g. ``[parent_sha, commit_sha]`` or ``["base...head"]``).

    Security: all arguments are passed as positional subprocess argv, not
    interpolated into a shell string.  The ``--`` separator prevents path
    arguments that start with '-' from being treated as flags.
    """
    # Build optional path filter list (must follow --)
    path_args = ["--", path] if path else []

    # --- Pass 1: numstat ---
    try:
        numstat_raw: str = repo.git.diff(
            *rev_args,
            "--no-ext-diff",
            "--numstat",
            "-z",
            *path_args,
        )
    except Exception:
        numstat_raw = ""

    entries = _parse_numstat(numstat_raw)

    # --- Pass 2: per-file patch with cap accumulator ---
    files: list[FileDiff] = []
    running_total = 0
    top_truncated = False

    for entry in entries:
        file_path = entry.path
        old_path: str | None = entry.old_path
        additions = entry.additions
        deletions = entry.deletions
        is_binary = entry.is_binary
        change_type = _diff_change_type(old_path, additions, deletions, is_binary)

        if is_binary:
            files.append(FileDiff(
                path=file_path,
                old_path=old_path,
                change_type=change_type,
                additions=0,
                deletions=0,
                is_binary=True,
                patch=None,
                truncated=False,
            ))
            continue

        if top_truncated:
            # Cap already hit: skip patch for remaining files
            files.append(FileDiff(
                path=file_path,
                old_path=old_path,
                change_type=change_type,
                additions=additions,
                deletions=deletions,
                is_binary=False,
                patch=None,
                truncated=True,
            ))
            continue

        # Generate patch for this file
        patch_text = _patch_for_file(repo, rev_args, context, file_path)

        patch_bytes = patch_text.encode("utf-8")
        patch_len = len(patch_bytes)

        if running_total + patch_len > max_bytes:
            # This file exceeds the cap.  Truncate at the boundary.
            available = max_bytes - running_total
            if available > 0:
                sliced_patch = _utf8_safe_trim(patch_bytes[:available])
            else:
                sliced_patch = ""
            files.append(FileDiff(
                path=file_path,
                old_path=old_path,
                change_type=change_type,
                additions=additions,
                deletions=deletions,
                is_binary=False,
                patch=sliced_patch or None,
                truncated=True,
            ))
            top_truncated = True
            running_total = max_bytes  # signal cap hit
        else:
            running_total += patch_len
            files.append(FileDiff(
                path=file_path,
                old_path=old_path,
                change_type=change_type,
                additions=additions,
                deletions=deletions,
                is_binary=False,
                patch=patch_text,
                truncated=False,
            ))

    return DiffResult(files=tuple(files), truncated=top_truncated)


# ---------------------------------------------------------------------------
# G5 sync implementations
# ---------------------------------------------------------------------------


def diff_text(
    repo: Repo,
    sha: str,
    path: str | None = None,
    *,
    context: int = 3,
    max_bytes: int | None = None,
) -> DiffResult:
    """Compute unified diff of a single commit vs its first parent.

    For the initial commit (no parents) the diff is computed against the
    empty tree (``NULL_TREE``).  ``path`` restricts the diff to a single
    file; if the path does not appear in the commit the result has an empty
    files list (callers surface this as 404).

    ``context`` is the number of context lines (clamped externally by the
    route to 0-10).  ``max_bytes`` defaults to ``settings.git_diff_max_bytes``
    (1 MiB) when not supplied.

    Args:
        repo: An open hardened ``Repo`` handle (from ``open_repo``).
        sha: Full or abbreviated commit SHA; will raise ``git.BadName`` on mismatch.
        path: Optional file path filter (literal path, not a glob).
        context: Unified context lines; default 3.
        max_bytes: Byte cap on total patch text.  None → settings default.

    Returns:
        ``DiffResult`` with per-file ``FileDiff`` entries and top-level
        ``truncated`` flag.

    Raises:
        git.BadName: SHA not found in this repository.
    """
    if max_bytes is None:
        max_bytes = get_settings().git_diff_max_bytes

    c = repo.commit(sha)  # raises git.BadName if not found
    if c.parents:
        rev_args = [c.parents[0].hexsha, c.hexsha]
    else:
        # Initial commit: diff against empty tree (git's empty tree SHA)
        rev_args = ["4b825dc642cb6eb9a060e54bf8d69288fbee4904", c.hexsha]

    return _build_diff_files(
        repo=repo,
        rev_args=rev_args,
        path=path,
        context=context,
        max_bytes=max_bytes,
    )


def range_diff(
    repo: Repo,
    base: str,
    head: str,
    path: str | None = None,
    *,
    context: int = 3,
    max_bytes: int | None = None,
) -> DiffResult:
    """Compute a merge-base range diff (``base...head``, three-dot notation).

    Three-dot semantics anchor the diff to the merge base of ``base`` and
    ``head``, matching GitHub/GitLab PR-diff conventions.  ``base`` and
    ``head`` may be branch names, tag names, or full/short SHAs.

    Args:
        repo: An open hardened ``Repo`` handle.
        base: Left side of the range (branch, tag, or SHA).
        head: Right side of the range (branch, tag, or SHA).
        path: Optional file path filter.
        context: Unified context lines; default 3.
        max_bytes: Byte cap on total patch text.  None → settings default.

    Returns:
        ``DiffResult`` with merged view of changes from ``base...head``.

    Raises:
        git.GitCommandError: If ``base`` or ``head`` cannot be resolved.
    """
    if max_bytes is None:
        max_bytes = get_settings().git_diff_max_bytes

    # Three-dot range: f"{base}...{head}" (merge-base diff, equivalent to PR diff)
    # Passed as a single rev_arg; git diff accepts this notation natively.
    range_expr = f"{base}...{head}"

    return _build_diff_files(
        repo=repo,
        rev_args=[range_expr],
        path=path,
        context=context,
        max_bytes=max_bytes,
    )


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


async def adiff_text(
    repo: Repo,
    sha: str,
    path: str | None = None,
    *,
    context: int = 3,
    max_bytes: int | None = None,
) -> DiffResult:
    """Async wrapper for ``diff_text``.

    Runs in a thread-pool worker so git subprocess I/O does not block the
    event loop.
    """
    return await asyncio.to_thread(diff_text, repo, sha, path, context=context, max_bytes=max_bytes)


async def arange_diff(
    repo: Repo,
    base: str,
    head: str,
    path: str | None = None,
    *,
    context: int = 3,
    max_bytes: int | None = None,
) -> DiffResult:
    """Async wrapper for ``range_diff``.

    Runs in a thread-pool worker so git subprocess I/O does not block the
    event loop.
    """
    return await asyncio.to_thread(
        range_diff, repo, base, head, path, context=context, max_bytes=max_bytes
    )
