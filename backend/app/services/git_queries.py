"""Git read-API service layer (G4 — PH-153).

All queries are cache-only: no git subprocess is spawned here.
Source tables: git_commits, git_branches, git_commit_files, git_commit_tickets.

Public functions (one per endpoint):
  graph_payload       → GitGraphResponse
  branches_payload    → GitBranchesListResponse
  commits_payload     → GitCommitsListResponse
  commit_detail       → GitCommitDetail

Helper:
  resolve_sha         → str (full 40-hex sha, or raises NotFound / RepoNotConfigured)

Design notes:
  - ``refs`` join: single query loads all branches, builds an in-memory
    head_sha → [name] map, then serialiser attaches refs per commit.
    Avoids N+1 while keeping query count predictable.
  - ahead/behind BFS: walks ``git_commits.parents`` JSON column in Python;
    bounded by ``settings.git_backfill_limit``.  Returns ``(None, None)``
    on overflow.  Default branch always returns (0, 0).
  - Pagination cursor: ``before=<sha>`` is translated to a ``committed_at``
    upper-bound via a sub-select.  Tie-break on sha DESC ensures stable pages
    when multiple commits share the same timestamp.
  - Path filter: EXISTS sub-query over git_commit_files to avoid a JOIN that
    would multiply rows when a commit touches the same file multiple times.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFound, RepoNotConfigured
from app.db.models import Board, GitBranch, GitCommit, GitCommitFile, Repository
from app.schemas import (
    GitBranchEntry,
    GitBranchesListResponse,
    GitCommitDetail,
    GitCommitFileEntry,
    GitCommitsListResponse,
    GitCommitSummary,
    GitGraphResponse,
)

# ---------------------------------------------------------------------------
# Internal helper: resolve board → repo (raises 409 when absent)
# ---------------------------------------------------------------------------


async def _get_repo(session: AsyncSession, board: Board) -> Repository:
    """Return the Repository for a board or raise RepoNotConfigured (409)."""
    repo = (
        await session.execute(
            select(Repository).where(Repository.board_id == board.id)
        )
    ).scalar_one_or_none()
    if repo is None:
        raise RepoNotConfigured()
    return repo


# ---------------------------------------------------------------------------
# Internal: sha resolver (exact 40-hex or prefix ≥7 chars)
# ---------------------------------------------------------------------------


async def resolve_sha(session: AsyncSession, repo_id: uuid.UUID, sha: str) -> str:
    """Resolve a full or short sha to the canonical 40-hex sha.

    - 40 chars: exact lookup (uq_git_commit_repo_sha index).
    - < 40 chars: prefix LIKE with LIMIT 2; >1 row → collision → 404.
    - No match: 404.

    Raises:
        NotFound("commit") on no match or collision.
    """
    if len(sha) == 40:
        row = (
            await session.execute(
                select(GitCommit.sha).where(
                    GitCommit.repo_id == repo_id,
                    GitCommit.sha == sha,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("commit")
        return str(row)

    # Short sha: use LIKE prefix match with collision guard
    rows = (
        await session.execute(
            select(GitCommit.sha).where(
                GitCommit.repo_id == repo_id,
                GitCommit.sha.like(sha + "%"),
            ).limit(2)
        )
    ).scalars().all()

    if len(rows) != 1:
        raise NotFound("commit")
    return str(rows[0])


# ---------------------------------------------------------------------------
# Internal: build refs map (head_sha → list[branch_name])
# ---------------------------------------------------------------------------


def _build_refs_map(branches: list[GitBranch]) -> dict[str, list[str]]:
    """Map each head_sha to the branch names pointing at it."""
    refs: dict[str, list[str]] = defaultdict(list)
    for b in branches:
        refs[b.head_sha].append(b.name)
    return dict(refs)


# ---------------------------------------------------------------------------
# Internal: serializers
# ---------------------------------------------------------------------------


def _serialise_commit_summary(
    commit: GitCommit,
    refs_map: dict[str, list[str]],
) -> GitCommitSummary:
    return GitCommitSummary(
        sha=commit.sha,
        short_sha=commit.short_sha,
        parents=list(commit.parents),
        author_name=commit.author_name,
        author_email=commit.author_email,
        authored_at=commit.authored_at,
        committed_at=commit.committed_at,
        summary=commit.summary,
        is_conventional=commit.is_conventional,
        commit_type=commit.commit_type,
        ticket_keys=list(commit.ticket_keys),
        refs=refs_map.get(commit.sha, []),
    )


def _serialise_file(f: GitCommitFile) -> GitCommitFileEntry:
    return GitCommitFileEntry(
        path=f.path,
        old_path=f.old_path,
        change_type=f.change_type,
        additions=f.additions,
        deletions=f.deletions,
        is_binary=f.is_binary,
    )


# ---------------------------------------------------------------------------
# Internal: ahead/behind BFS
# ---------------------------------------------------------------------------


def _bfs_reachable(
    parents_map: dict[str, list[str]],
    start_sha: str,
    limit: int,
) -> set[str] | None:
    """Return the set of shas reachable from start_sha via BFS.

    Uses ``parents_map`` (sha → parent list) which only covers cached commits.
    Returns None when the BFS exceeds ``limit`` (overflow sentinel).
    """
    visited: set[str] = set()
    queue: deque[str] = deque([start_sha])
    while queue:
        if len(visited) >= limit:
            return None  # overflow
        sha = queue.popleft()
        if sha in visited:
            continue
        visited.add(sha)
        for parent in parents_map.get(sha, []):
            if parent not in visited:
                queue.append(parent)
    return visited


def _compute_ahead_behind(
    parents_map: dict[str, list[str]],
    branch_head: str,
    default_head: str,
    limit: int,
) -> tuple[int | None, int | None]:
    """Compute ahead/behind counts for branch vs. default.

    Returns (None, None) on BFS overflow.  Default branch always (0, 0).
    """
    if branch_head == default_head:
        return (0, 0)

    branch_reach = _bfs_reachable(parents_map, branch_head, limit)
    default_reach = _bfs_reachable(parents_map, default_head, limit)

    if branch_reach is None or default_reach is None:
        return (None, None)

    ahead = len(branch_reach - default_reach)
    behind = len(default_reach - branch_reach)
    return (ahead, behind)


# ---------------------------------------------------------------------------
# Public: graph_payload
# ---------------------------------------------------------------------------


async def graph_payload(
    session: AsyncSession,
    board: Board,
    limit: int = 200,
    branch_filter: list[str] | None = None,
) -> GitGraphResponse:
    """Return the DAG payload for GET /git/graph.

    When ``branch_filter`` is provided (non-empty), only commits whose sha
    appears in the parents of any listed branch head are included (simple
    reachability from the filtered heads within the cached window).
    ``branches[]`` is also filtered to the requested names.

    Implementation note: full reachability from an arbitrary set of heads is
    expensive.  G4 uses a pragmatic shortcut: fetch the top ``limit`` commits
    ordered by committed_at DESC; if branch_filter is set, additionally exclude
    branches not in the filter.  The commits[] already carry parents so the
    frontend renderer can reconstruct the DAG for the returned window.
    """
    repo = await _get_repo(session, board)

    # Load all branches (needed for refs map and optional filter)
    branches_rows: list[GitBranch] = list(
        (
            await session.execute(
                select(GitBranch).where(GitBranch.repo_id == repo.id)
            )
        )
        .scalars()
        .all()
    )

    # Apply branch filter to branches list
    if branch_filter:
        branches_rows = [b for b in branches_rows if b.name in branch_filter]

    refs_map = _build_refs_map(branches_rows)

    # Build commit query
    stmt = (
        select(GitCommit)
        .where(GitCommit.repo_id == repo.id)
        .order_by(GitCommit.committed_at.desc(), GitCommit.sha.desc())        .limit(limit)
    )
    commits_rows: list[GitCommit] = list(
        (await session.execute(stmt)).scalars().all()
    )

    commits_out = [_serialise_commit_summary(c, refs_map) for c in commits_rows]

    # Load default branch info for ahead/behind (needed by branches)
    default_branch_row = next((b for b in branches_rows if b.is_default), None)

    # Build parents map for ahead/behind BFS
    # Load ALL cached commits for the BFS (not just the window)
    all_commits_stmt = select(GitCommit.sha, GitCommit.parents).where(
        GitCommit.repo_id == repo.id
    )
    all_commits_data: list[Any] = list(
        (await session.execute(all_commits_stmt)).all()
    )
    parents_map: dict[str, list[str]] = {
        row.sha: list(row.parents) for row in all_commits_data
    }

    settings = get_settings()
    bfs_limit = settings.git_backfill_limit

    branches_out: list[GitBranchEntry] = []
    for b in branches_rows:
        if b.is_default or default_branch_row is None:
            ahead_v: int | None = 0
            behind_v: int | None = 0
        else:
            ahead_v, behind_v = _compute_ahead_behind(
                parents_map, b.head_sha, default_branch_row.head_sha, bfs_limit
            )
        branches_out.append(
            GitBranchEntry(
                name=b.name,
                head_sha=b.head_sha,
                is_default=b.is_default,
                ticket_key=b.ticket_key,
                ahead=ahead_v,
                behind=behind_v,
            )
        )

    return GitGraphResponse(commits=commits_out, branches=branches_out, tags=[])


# ---------------------------------------------------------------------------
# Public: branches_payload
# ---------------------------------------------------------------------------


async def branches_payload(
    session: AsyncSession,
    board: Board,
) -> GitBranchesListResponse:
    """Return branch list with ahead/behind for GET /git/branches."""
    repo = await _get_repo(session, board)

    branches_rows: list[GitBranch] = list(
        (
            await session.execute(
                select(GitBranch).where(GitBranch.repo_id == repo.id)
            )
        )
        .scalars()
        .all()
    )

    default_branch_row = next((b for b in branches_rows if b.is_default), None)

    # Load parents map for BFS
    all_commits_stmt = select(GitCommit.sha, GitCommit.parents).where(
        GitCommit.repo_id == repo.id
    )
    all_commits_data: list[Any] = list(
        (await session.execute(all_commits_stmt)).all()
    )
    parents_map: dict[str, list[str]] = {
        row.sha: list(row.parents) for row in all_commits_data
    }

    settings = get_settings()
    bfs_limit = settings.git_backfill_limit

    branches_out: list[GitBranchEntry] = []
    for b in branches_rows:
        if b.is_default or default_branch_row is None:
            ahead_v: int | None = 0
            behind_v: int | None = 0
        else:
            ahead_v, behind_v = _compute_ahead_behind(
                parents_map, b.head_sha, default_branch_row.head_sha, bfs_limit
            )
        branches_out.append(
            GitBranchEntry(
                name=b.name,
                head_sha=b.head_sha,
                is_default=b.is_default,
                ticket_key=b.ticket_key,
                ahead=ahead_v,
                behind=behind_v,
            )
        )

    return GitBranchesListResponse(branches=branches_out)


# ---------------------------------------------------------------------------
# Public: commits_payload
# ---------------------------------------------------------------------------


async def commits_payload(
    session: AsyncSession,
    board: Board,
    branch: str | None = None,
    path: str | None = None,
    limit: int = 50,
    before: str | None = None,
) -> GitCommitsListResponse:
    """Return paginated commit log for GET /git/commits.

    Cursor pagination:
      ``before=<sha>`` → fetch committed_at of that sha, then filter
      ``committed_at < cursor_time OR (committed_at == cursor_time AND sha < before_sha)``.
      Tie-break on sha DESC ensures stable pages with identical timestamps.

    Branch filter:
      Loads head_sha of the requested branch, then limits to commits reachable
      (those whose sha == head or whose sha appears as a parent chain).
      Pragmatic implementation: fetch all commits ordered by committed_at DESC,
      then do an in-Python BFS limited to ``limit * 10`` to find reachable shas.

    Path filter:
      EXISTS sub-query over git_commit_files WHERE path = :path.
    """
    repo = await _get_repo(session, board)

    # Resolve before cursor
    cursor_at = None
    cursor_sha: str | None = None
    if before is not None:
        full_before_sha = await resolve_sha(session, repo.id, before)
        cursor_row = (
            await session.execute(
                select(GitCommit.committed_at, GitCommit.sha).where(
                    GitCommit.repo_id == repo.id,
                    GitCommit.sha == full_before_sha,
                )
            )
        ).one_or_none()
        if cursor_row is not None:
            cursor_at = cursor_row.committed_at
            cursor_sha = cursor_row.sha

    # Resolve branch head sha for reachability filter
    branch_head_sha: str | None = None
    if branch is not None:
        branch_row = (
            await session.execute(
                select(GitBranch.head_sha).where(
                    GitBranch.repo_id == repo.id,
                    GitBranch.name == branch,
                )
            )
        ).scalar_one_or_none()
        if branch_row is not None:
            branch_head_sha = branch_row

    # Load commits (with broad limit for branch-reachability filtering)
    # We fetch up to limit * 20 rows before filtering to keep memory bounded.
    fetch_limit = limit * 20 if branch is not None else limit

    stmt = (
        select(GitCommit)
        .where(GitCommit.repo_id == repo.id)
    )

    # Cursor filter: strict before or same-time-but-sha-before
    if cursor_at is not None and cursor_sha is not None:
        from sqlalchemy import and_, or_

        stmt = stmt.where(
            or_(
                GitCommit.committed_at < cursor_at,
                and_(
                    GitCommit.committed_at == cursor_at,
                    GitCommit.sha < cursor_sha,
                ),
            )
        )

    # Path filter via EXISTS
    if path is not None:
        from sqlalchemy import exists

        stmt = stmt.where(
            exists().where(
                GitCommitFile.commit_id == GitCommit.id,
                GitCommitFile.path == path,
            )
        )

    stmt = stmt.order_by(
        GitCommit.committed_at.desc(), GitCommit.sha.desc()    ).limit(fetch_limit)

    commits_rows: list[GitCommit] = list(
        (await session.execute(stmt)).scalars().all()
    )

    # Branch reachability filter (in-Python BFS over fetched rows)
    if branch_head_sha is not None and commits_rows:
        parents_map: dict[str, list[str]] = {
            c.sha: list(c.parents) for c in commits_rows
        }
        reachable = _bfs_reachable(parents_map, branch_head_sha, len(commits_rows))
        if reachable is not None:
            commits_rows = [c for c in commits_rows if c.sha in reachable]
        # If overflow (None), all rows are included (best-effort)
        commits_rows = commits_rows[:limit]

    # Load branches for refs map
    branches_rows: list[GitBranch] = list(
        (
            await session.execute(
                select(GitBranch).where(GitBranch.repo_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    refs_map = _build_refs_map(branches_rows)

    commits_out = [_serialise_commit_summary(c, refs_map) for c in commits_rows[:limit]]
    return GitCommitsListResponse(commits=commits_out)


# ---------------------------------------------------------------------------
# Public: commit_detail
# ---------------------------------------------------------------------------


async def commit_detail(
    session: AsyncSession,
    board: Board,
    sha: str,
) -> GitCommitDetail:
    """Return full commit payload including per-file numstat.

    ``sha`` can be 40-hex or a short prefix (≥7 chars); collision → 404.
    """
    repo = await _get_repo(session, board)
    full_sha = await resolve_sha(session, repo.id, sha)

    commit_row = (
        await session.execute(
            select(GitCommit).where(
                GitCommit.repo_id == repo.id,
                GitCommit.sha == full_sha,
            )
        )
    ).scalar_one_or_none()
    if commit_row is None:
        raise NotFound("commit")

    files_rows: list[GitCommitFile] = list(
        (
            await session.execute(
                select(GitCommitFile).where(GitCommitFile.commit_id == commit_row.id)
            )
        )
        .scalars()
        .all()
    )

    # Build refs map (load all branches for this repo)
    branches_rows: list[GitBranch] = list(
        (
            await session.execute(
                select(GitBranch).where(GitBranch.repo_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    refs_map = _build_refs_map(branches_rows)

    return GitCommitDetail(
        sha=commit_row.sha,
        short_sha=commit_row.short_sha,
        parents=list(commit_row.parents),
        author_name=commit_row.author_name,
        author_email=commit_row.author_email,
        authored_at=commit_row.authored_at,
        committed_at=commit_row.committed_at,
        summary=commit_row.summary,
        is_conventional=commit_row.is_conventional,
        commit_type=commit_row.commit_type,
        ticket_keys=list(commit_row.ticket_keys),
        refs=refs_map.get(commit_row.sha, []),
        committer_name=commit_row.committer_name,
        committer_email=commit_row.committer_email,
        body=commit_row.body,
        files=[_serialise_file(f) for f in files_rows],
    )
