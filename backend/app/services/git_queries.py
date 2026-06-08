"""Git read-API service layer (G4 — PH-153; G5 extensions — PH-154).

All queries are cache-only: no git subprocess is spawned here.
Source tables: git_commits, git_branches, git_commit_files, git_commit_tickets.

Public functions (one per endpoint):
  graph_payload           → GitGraphResponse
  branches_payload        → GitBranchesListResponse
  commits_payload         → GitCommitsListResponse
  commit_detail           → GitCommitDetail
  ticket_commits_payload  → TicketCommitsResponse  (G5)

Helper:
  resolve_sha             → str (full 40-hex sha, or raises NotFound / RepoNotConfigured)

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
  - ticket_commits_payload: cache-only join (git_commit_tickets → git_commits
    LEFT JOIN aggregated git_commit_files); no git subprocess.  Diff payloads
    are not inlined — UI calls the single-commit diff endpoint on demand.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFound
from app.db.models import (
    Board,
    GitBranch,
    GitCommit,
    GitCommitFile,
    GitCommitTicket,
    Repository,
    Ticket,
)
from app.schemas import (
    GitBranchEntry,
    GitBranchesListResponse,
    GitCommitDetail,
    GitCommitFileEntry,
    GitCommitsListResponse,
    GitCommitSummary,
    GitGraphResponse,
    TicketCommitEntry,
    TicketCommitsResponse,
)

# ---------------------------------------------------------------------------
# Internal helper: resolve board → repo (raises 409 when absent)
# ---------------------------------------------------------------------------


async def _get_repo(
    session: AsyncSession, board: Board, selector: str | None = None
) -> Repository:
    """Resolve a board's repo (PH-221), defaulting to the primary.

    Delegates to ``services.repositories.resolve_repository``: ``selector`` None
    → primary (raises ``RepoNotConfigured`` (409) when no primary), a slug or id
    → that repo (raises ``NotFound("repository")`` (404) when unmatched). Every
    ``/git/*`` query enters through here.
    """
    from app.services.repositories import resolve_repository

    return await resolve_repository(session, board, selector)


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
    repo_selector: str | None = None,
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

    PH-221: ``repo_selector`` (slug or id; None → primary) chooses which repo's
    cache to serve.
    """
    repo = await _get_repo(session, board, repo_selector)

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
    repo_selector: str | None = None,
) -> GitBranchesListResponse:
    """Return branch list with ahead/behind for GET /git/branches.

    PH-221: ``repo_selector`` (slug or id; None → primary) chooses the repo.
    """
    repo = await _get_repo(session, board, repo_selector)

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
    repo_selector: str | None = None,
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

    PH-221: ``repo_selector`` (slug or id; None → primary) chooses the repo.
    """
    repo = await _get_repo(session, board, repo_selector)

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
    repo_selector: str | None = None,
) -> GitCommitDetail:
    """Return full commit payload including per-file numstat.

    ``sha`` can be 40-hex or a short prefix (≥7 chars); collision → 404.
    PH-221: ``repo_selector`` (slug or id; None → primary) chooses the repo.
    """
    repo = await _get_repo(session, board, repo_selector)
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


# ---------------------------------------------------------------------------
# G5: ticket_commits_payload (PH-154)
# ---------------------------------------------------------------------------


async def ticket_commits_payload(
    session: AsyncSession,
    ticket: Ticket,
) -> TicketCommitsResponse:
    """Return commits linked to a ticket via ``git_commit_tickets`` (cache-only).

    Joins ``git_commit_tickets → git_commits`` and aggregates
    ``git_commit_files`` (SUM additions/deletions, COUNT files_changed) via a
    correlated sub-query.  Ordered newest-first by ``committed_at``.

    No diff text is included — the caller (UI) fetches per-commit diffs via
    ``GET /git/commits/{sha}/diff`` on demand.

    Args:
        session: Async DB session.
        ticket: Ticket ORM object whose ``id`` is used as the join key.

    Returns:
        ``TicketCommitsResponse`` with ``branch_name`` and ``commits`` list.
        Zero linkage rows → ``{branch_name: ..., commits: []}`` (200, not 404).
    """
    # Aggregate git_commit_files per commit in a sub-query.
    file_agg = (
        select(
            GitCommitFile.commit_id,
            func.coalesce(func.sum(GitCommitFile.additions), 0).label("total_additions"),
            func.coalesce(func.sum(GitCommitFile.deletions), 0).label("total_deletions"),
            func.count(GitCommitFile.id).label("files_changed"),
        )
        .group_by(GitCommitFile.commit_id)
        .subquery()
    )

    # Join git_commit_tickets → git_commits LEFT JOIN file_agg
    rows = (
        await session.execute(
            select(
                GitCommit.sha,
                GitCommit.short_sha,
                GitCommit.summary,
                GitCommit.authored_at,
                GitCommit.committed_at,
                GitCommit.author_name,
                func.coalesce(file_agg.c.total_additions, 0).label("additions"),
                func.coalesce(file_agg.c.total_deletions, 0).label("deletions"),
                func.coalesce(file_agg.c.files_changed, 0).label("files_changed"),
            )
            .join(GitCommitTicket, GitCommitTicket.commit_id == GitCommit.id)
            .outerjoin(file_agg, file_agg.c.commit_id == GitCommit.id)
            .where(GitCommitTicket.ticket_id == ticket.id)
            .order_by(GitCommit.committed_at.desc(), GitCommit.sha.desc())
        )
    ).all()

    commits_out: list[TicketCommitEntry] = [
        TicketCommitEntry(
            sha=row.sha,
            short_sha=row.short_sha,
            summary=row.summary,
            authored_at=row.authored_at,
            committed_at=row.committed_at,
            author_name=row.author_name,
            additions=int(row.additions),
            deletions=int(row.deletions),
            files_changed=int(row.files_changed),
        )
        for row in rows
    ]

    return TicketCommitsResponse(
        branch_name=ticket.branch_name,
        commits=commits_out,
    )
