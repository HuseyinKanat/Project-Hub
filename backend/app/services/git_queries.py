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

import bisect
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
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
    TicketBranchEntry,
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
    merged_shas: set[str] | None = None,
) -> GitCommitSummary:
    """Serialise a cached commit to ``GitCommitSummary``.

    PH-268: ``merged_shas`` is the ancestor set of the default-branch head over
    the FULL repo cache (default head included).  When provided, a commit's
    ``merged_into_default`` flag is set to membership in that set.  Callers that
    cannot compute reachability (commits-list / commit-detail) pass ``None`` and
    the field stays at its back-compat default of ``False``.
    """
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
        merged_into_default=(
            merged_shas is not None and commit.sha in merged_shas
        ),
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


def _bounded_ancestors(
    parents_map: dict[str, list[str]],
    start_sha: str,
    limit: int,
) -> set[str]:
    """Return the set of shas reachable from ``start_sha`` via parent edges,
    capped at ``limit`` entries (the bounded *partial* walk on overflow).

    PH-270: the ``/git/commits`` LOG view filters by branch reachability over the
    FULL repo cache.  Unlike :func:`_bfs_reachable` (which returns ``None`` on
    overflow so ahead/behind set math stays correct), here overflow must degrade
    to the head plus as many ancestors as ``limit`` allows — never ``None`` and
    never the unfiltered set.  Walking only parent edges from the head means the
    result can never contain commits on an unrelated branch.
    """
    visited: set[str] = set()
    queue: deque[str] = deque([start_sha])
    while queue and len(visited) < limit:
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


def _reachable_from_heads(
    parents_map: dict[str, list[str]],
    head_shas: list[str],
    limit: int,
) -> set[str]:
    """Union of commits reachable (via parent edges) from each head in ``head_shas``.

    PH-269: ``graph_payload``'s ``branch_filter`` historically only scoped the
    ``branches[]`` list / refs map — the ``commits[]`` set was identical to the
    unfiltered top-N, so the documented "reachability filtering" was a lie.  This
    wires the existing ``_bfs_reachable`` over the filtered branch heads to make
    the contract honest.

    Each head is walked with ``_bfs_reachable``; an overflow (``None``) on a head
    contributes nothing extra to the union beyond what other heads cover (the BFS
    is already bounded by the cached window, so overflow only means a head is
    deeper than ``limit`` — we still keep whatever the other heads reach).  An
    empty / all-overflow result yields an empty set (caller decides the
    no-match semantics).

    Pure / cache-only: walks the in-memory ``parents_map`` only.
    """
    union: set[str] = set()
    for head in head_shas:
        reach = _bfs_reachable(parents_map, head, limit)
        if reach is not None:
            union |= reach
    return union


# ---------------------------------------------------------------------------
# Internal: topological order (newest-first; child before parent)
# ---------------------------------------------------------------------------


def _count_pending_children(
    commits: list[GitCommit], in_window: set[str]
) -> dict[str, int]:
    """Map each in-window sha -> count of its in-window children.

    Extracted from ``_topological_order`` to keep that function's nesting (and
    cognitive complexity) low (PH-266 revision, Sonar S3776). When we later
    "emit" a child we decrement each of its in-window parents' count; a parent
    becomes ready once all its in-window children are emitted (newest-first =>
    child before parent).
    """
    pending: dict[str, int] = dict.fromkeys(in_window, 0)
    for c in commits:
        for parent in c.parents:
            if parent in in_window:
                pending[parent] += 1
    return pending


def _release_ready_parents(
    sha: str,
    by_sha: dict[str, GitCommit],
    in_window: set[str],
    pending_children: dict[str, int],
    ready: list[str],
    key: Callable[[str], tuple[bool, Any, str]],
) -> None:
    """After emitting ``sha``, decrement its in-window parents and insert any
    that just became ready into ``ready`` (kept sorted ascending by ``key``).

    Extracted from the Kahn main loop to flatten nesting (PH-266 Sonar S3776).
    """
    for parent in by_sha[sha].parents:
        if parent not in in_window:
            continue
        pending_children[parent] -= 1
        if pending_children[parent] == 0:
            keys = [key(s) for s in ready]
            idx = bisect.bisect_right(keys, key(parent))
            ready.insert(idx, parent)


def _topological_order(
    commits: list[GitCommit], default_head: str | None = None
) -> list[GitCommit]:
    """Return ``commits`` in a deterministic newest-first topological order.

    PH-266: the DAG payload was previously emitted ordered by
    ``committed_at DESC, sha DESC``.  That ordering is NOT topological — on real
    history a child commit and its first parent can share (or invert) timestamps
    so the parent sorts ABOVE the child.  The frontend lane layout
    (``assignLanes``/``computeLanePaths``) then isolates the inverted commit into
    its own single-row span and can never inherit the "merged" classification,
    rendering a fully-merged tip as a false "open/unmerged" ring.

    This emits a Kahn topological order where every commit appears BEFORE any of
    its parents (newest-first / child-before-parent), considering only IN-WINDOW
    parent edges (edges whose BOTH endpoints are in ``commits``).

    ``default_head`` (the default-branch head sha, when known) is the PRIMARY
    ready-set ordering key: it ranks ahead of every other ready commit, so it is
    emitted first whenever it is ready.  The default head is a source (nothing
    in-window has it as a first parent to draw it below — it has no in-window
    children), so emitting it first preserves topological validity while
    guaranteeing it lands at ``commits[0]`` in ALL cases — even when an OPEN
    side-branch tip carries a newer ``committed_at`` (PH-266 revision finding 1).
    The remaining tie-break is ``(committed_at DESC, sha DESC)`` for full
    determinism.

    Pure / cache-only: no git subprocess, no DB access.
    """
    if not commits:
        return []

    in_window: set[str] = {c.sha for c in commits}
    by_sha: dict[str, GitCommit] = {c.sha: c for c in commits}
    pending_children = _count_pending_children(commits, in_window)

    # Deterministic sort key (ascending; ready list is popped from the END, so
    # the MAX key emits first): the default-branch head ranks ahead of EVERY
    # other ready commit (primary key True > False), then newest committed_at,
    # then sha desc.  This forces the default head to commits[0] in all cases —
    # even when an open side-branch tip has a newer committed_at (PH-266 rev).
    def _key(sha: str) -> tuple[bool, Any, str]:
        commit = by_sha[sha]
        return (sha == default_head, commit.committed_at, commit.sha)

    # Ready set = commits with no outstanding in-window children. Maintained as a
    # sorted list popped from the END (max key) so each emission is the
    # default head (if ready) then the newest/highest-sha currently-ready commit.
    ready: list[str] = sorted(
        (sha for sha in in_window if pending_children[sha] == 0),
        key=_key,
    )

    ordered: list[GitCommit] = []
    while ready:
        sha = ready.pop()  # max key under (default-head, committed_at, sha)
        ordered.append(by_sha[sha])
        _release_ready_parents(sha, by_sha, in_window, pending_children, ready, _key)

    # Cycle guard (git DAGs are acyclic, but a corrupt cache must not drop rows):
    # any commit never emitted (would only happen on a cycle) is appended in the
    # deterministic tie-break order so the output is never shorter than input.
    if len(ordered) != len(commits):
        emitted = {c.sha for c in ordered}
        leftover = sorted(
            (c for c in commits if c.sha not in emitted),
            key=lambda c: (c.committed_at, c.sha),
            reverse=True,
        )
        ordered.extend(leftover)

    return ordered


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

    When ``branch_filter`` is provided (non-empty):
      - ``branches[]`` / the refs map are restricted to the requested names; and
      - ``commits[]`` is scoped to the union of commits reachable (via parent
        edges) from the filtered branch heads, within the cached window
        (PH-269 — wires ``_bfs_reachable`` over the filtered heads).  Before
        PH-269 only the branch labels were filtered while ``commits[]`` stayed
        the full top-N, so the documented reachability did not exist.

    The default (no ``branch_filter``) path returns the full topo-ordered window
    truncated to ``limit`` — unchanged.  The reachability filter runs AFTER the
    topological sort (PH-266) as a pure membership filter, so commit ordering is
    preserved.  The commits[] carry parents so the frontend renderer can
    reconstruct the DAG for the returned window.

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

    # PH-268: capture the default head from the UNFILTERED branch set BEFORE the
    # branch_filter narrows ``branches_rows``.  Merged-ness is defined by the
    # real default branch regardless of whether the caller filtered it out of
    # the view — a side tip is still "merged into main" even when main is not in
    # the requested branch list.
    unfiltered_default_row = next(
        (b for b in branches_rows if b.is_default), None
    )
    unfiltered_default_head_sha = (
        unfiltered_default_row.head_sha if unfiltered_default_row else None
    )

    # Apply branch filter to branches list
    if branch_filter:
        branches_rows = [b for b in branches_rows if b.name in branch_filter]

    refs_map = _build_refs_map(branches_rows)

    # Build commit query.
    # PH-266: fetch the FULL candidate set ordered by committed_at DESC, sha DESC
    # (the legacy tie-break) WITHOUT a DB-level LIMIT, produce a topological order
    # (child before parent), THEN truncate to `limit`. Truncate-then-sort would
    # let window contents drift and re-introduce the parent-above-child inversion
    # that renders a merged tip as a false "open" ring. The cached window is
    # already bounded by what the sync layer persists (the same set the
    # ahead/behind parents_map below loads), so this stays memory-bounded.
    stmt = (
        select(GitCommit)
        .where(GitCommit.repo_id == repo.id)
        .order_by(GitCommit.committed_at.desc(), GitCommit.sha.desc())
    )
    candidate_rows: list[GitCommit] = list(
        (await session.execute(stmt)).scalars().all()
    )

    # Default branch info (needed for ahead/behind AND to anchor the topo order:
    # PH-266 makes the default head commits[0] in ALL cases, not just when its
    # committed_at happens to be newest).
    default_branch_row = next((b for b in branches_rows if b.is_default), None)
    default_head_sha = default_branch_row.head_sha if default_branch_row else None

    # Build parents map for ahead/behind BFS AND (PH-269) branch_filter
    # reachability.  Load ALL cached commits for the BFS (not just the window) so
    # reachability can follow parent edges beyond the truncated topo window.
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

    # PH-268: authoritative per-commit "merged into default" set.  A side-lane
    # branch tip is merged iff its sha is reachable from the default head via
    # parent edges over the FULL cache (the default head is its own ancestor, so
    # lane-0 / default-chain commits also land in the set — harmless: the FE only
    # consults the flag for side-lane span tips).  Reuses the ``parents_map``
    # already loaded above for ahead/behind (no second DB load — PH-269 gotcha).
    # Bounded by ``git_backfill_limit`` via ``_bounded_ancestors`` (partial on
    # overflow, never None; parent edges only, so it can never reach an unrelated
    # branch).  Computed from the UNFILTERED default head so a filtered-out main
    # still defines merged-ness; guarded to leave every flag False when there is
    # no default branch row.
    merged_shas: set[str] | None = None
    if unfiltered_default_head_sha is not None:
        merged_shas = _bounded_ancestors(
            parents_map, unfiltered_default_head_sha, bfs_limit
        )

    # Topological newest-first order over IN-WINDOW parent edges, with the
    # default-branch head forced first.
    ordered_rows = _topological_order(candidate_rows, default_head_sha)

    # PH-269: make ``branch_filter`` honest — scope commits[] to the union of
    # commits reachable from the FILTERED branch heads (``branches_rows`` is
    # already restricted to the requested names above).  Applied to the
    # topo-ordered list as a membership filter, so PH-266's ordering is
    # preserved; only the default (no-filter) path keeps the full window.
    if branch_filter:
        reachable = _reachable_from_heads(
            parents_map, [b.head_sha for b in branches_rows], bfs_limit
        )
        ordered_rows = [c for c in ordered_rows if c.sha in reachable]

    # Truncate to `limit` AFTER ordering (and after the optional reachability
    # filter) so window contents never drift and re-introduce the
    # parent-above-child inversion (PH-266).
    ordered_rows = ordered_rows[:limit]

    commits_out = [
        _serialise_commit_summary(c, refs_map, merged_shas)
        for c in ordered_rows
    ]

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
      from that head via parent edges.  PH-270: reachability is computed over the
      FULL repo cache (sha → parents for every cached commit, mirroring
      ``graph_payload``), bounded by ``settings.git_backfill_limit`` — NOT over a
      ``limit * N`` fetch window.  The reachable set is then handed to SQL as an
      ``sha IN (...)`` predicate so cursor + path filters, committed_at DESC order
      and ``limit`` all apply to the reachable subset (no silent unfiltered
      fallback).  An empty / overflow reachable set yields an empty page.

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

    # PH-270: branch reachability is computed over the FULL repo cache, not a
    # ``limit * N`` fetch window.  Build sha → parents for every cached commit
    # (mirrors ``graph_payload``), BFS from the head bounded by
    # ``git_backfill_limit``, then push the reachable set into SQL as an
    # ``sha IN (...)`` predicate so cursor + path + order + limit apply to the
    # reachable subset.  Overflow / empty reachability ⇒ empty page (never a
    # silent unfiltered fallback).
    reachable_shas: set[str] | None = None
    if branch is not None:
        if branch_head_sha is None:
            # Branch not found in cache → no reachable commits.
            reachable_shas = set()
        else:
            all_commits_data: list[Any] = list(
                (
                    await session.execute(
                        select(GitCommit.sha, GitCommit.parents).where(
                            GitCommit.repo_id == repo.id
                        )
                    )
                ).all()
            )
            parents_map: dict[str, list[str]] = {
                row.sha: list(row.parents) for row in all_commits_data
            }
            bfs_limit = get_settings().git_backfill_limit
            # Bounded ancestor walk from the branch head.  Unlike
            # ``_bfs_reachable`` (whose ``None`` overflow sentinel powers
            # ahead/behind set-difference math), the LOG view wants the bounded
            # *partial* set on overflow: the head plus as many ancestors as the
            # bound allows — never ``None`` (which used to trigger a silent
            # unfiltered fallback that leaked unrelated commits).
            reachable_shas = _bounded_ancestors(
                parents_map, branch_head_sha, bfs_limit
            )

    stmt = (
        select(GitCommit)
        .where(GitCommit.repo_id == repo.id)
    )

    # PH-270: restrict to the reachable set when a branch filter is active.
    if reachable_shas is not None:
        if not reachable_shas:
            # No reachable commits → empty page (skip the query entirely).
            return GitCommitsListResponse(commits=[])
        stmt = stmt.where(GitCommit.sha.in_(reachable_shas))

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
        GitCommit.committed_at.desc(), GitCommit.sha.desc()
    ).limit(limit)

    commits_rows: list[GitCommit] = list(
        (await session.execute(stmt)).scalars().all()
    )

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

    # ``commits_rows`` is already SQL-limited to ``limit``.
    commits_out = [_serialise_commit_summary(c, refs_map) for c in commits_rows]
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
# G5: ticket_branches_payload (PH-250) + ticket_commits_payload (PH-154)
# ---------------------------------------------------------------------------


async def ticket_branches_payload(
    session: AsyncSession,
    ticket: Ticket,
) -> list[TicketBranchEntry]:
    """Return a ticket's branches across ALL linked repos (cache-only).

    PH-250: joins ``git_branches → repositories`` and filters by
    ``git_branches.ticket_key == ticket.key`` — the ticket KEY STRING (e.g.
    "PH-250"), NOT ``ticket_id``. ``git.sync`` derives ``ticket_key`` from the
    branch NAME on every sync for every repo, so a branch in a non-primary repo
    is already recorded with its correct ``repo_id``; this just surfaces it.

    Contrast ``ticket_commits_payload`` which joins ``git_commit_tickets`` by
    ``ticket_id`` (the UUID). Both are correct for their own source data — do
    NOT unify them on one key.

    The ``Repository`` join (1:1 via the NOT-NULL ``repo_id`` FK) cannot
    multiply rows; each entry is tagged with its source repo
    (``repo_id``/``repo_slug``/``repo_name``). A ticket worked in N repos yields
    N+ branch rows, all returned. Ordered primary-repo-first, then newest by
    ``last_commit_at`` (a UX nicety; NULLs last). Zero rows → ``[]``.

    Args:
        session: Async DB session.
        ticket: Ticket ORM object whose ``key`` is the branch join key.

    Returns:
        List of ``TicketBranchEntry`` (possibly empty).
    """
    rows = (
        await session.execute(
            select(
                GitBranch.name,
                GitBranch.head_sha,
                GitBranch.is_default,
                GitBranch.last_commit_at,
                GitBranch.repo_id,
                Repository.slug.label("repo_slug"),
                Repository.name.label("repo_name"),
            )
            .join(Repository, Repository.id == GitBranch.repo_id)
            .where(GitBranch.ticket_key == ticket.key)
            .order_by(
                Repository.is_primary.desc(),
                GitBranch.last_commit_at.desc().nullslast(),
                GitBranch.name.asc(),
            )
        )
    ).all()

    return [
        TicketBranchEntry(
            name=row.name,
            head_sha=row.head_sha,
            is_default=row.is_default,
            last_commit_at=row.last_commit_at,
            repo_id=row.repo_id,
            repo_slug=row.repo_slug,
            repo_name=row.repo_name,
        )
        for row in rows
    ]


async def ticket_commits_payload(
    session: AsyncSession,
    ticket: Ticket,
) -> TicketCommitsResponse:
    """Return commits + branches linked to a ticket (cache-only).

    Joins ``git_commit_tickets → git_commits`` and aggregates
    ``git_commit_files`` (SUM additions/deletions, COUNT files_changed) via a
    correlated sub-query.  Ordered newest-first by ``committed_at``.

    PH-250: also populates ``branches`` via ``ticket_branches_payload`` — the
    per-repo branch identity (join by ``ticket.key`` string). ``branch_name``
    (the legacy single repo-agnostic pointer) is retained unchanged.

    No diff text is included — the caller (UI) fetches per-commit diffs via
    ``GET /git/commits/{sha}/diff`` on demand.

    Args:
        session: Async DB session.
        ticket: Ticket ORM object (``id`` for commits, ``key`` for branches).

    Returns:
        ``TicketCommitsResponse`` with ``branch_name``, ``branches`` and
        ``commits``. Zero linkage rows → empty lists (200, not 404).
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

    # Join git_commit_tickets → git_commits → repositories LEFT JOIN file_agg.
    # PH-247: the JOIN keys on ticket_id ONLY (no repo filter) so a ticket's
    # commits aggregate across EVERY board repo; the Repository join (1:1 via the
    # NOT-NULL repo_id FK — cannot multiply rows) tags each entry with its source
    # repo so the FE can render a per-row badge + thread repo_slug into the
    # per-repo commit-detail / diff fetch.
    rows = (
        await session.execute(
            select(
                GitCommit.sha,
                GitCommit.short_sha,
                GitCommit.summary,
                GitCommit.authored_at,
                GitCommit.committed_at,
                GitCommit.author_name,
                GitCommit.repo_id,
                Repository.slug.label("repo_slug"),
                Repository.name.label("repo_name"),
                func.coalesce(file_agg.c.total_additions, 0).label("additions"),
                func.coalesce(file_agg.c.total_deletions, 0).label("deletions"),
                func.coalesce(file_agg.c.files_changed, 0).label("files_changed"),
            )
            .join(GitCommitTicket, GitCommitTicket.commit_id == GitCommit.id)
            .join(Repository, Repository.id == GitCommit.repo_id)
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
            repo_id=row.repo_id,
            repo_slug=row.repo_slug,
            repo_name=row.repo_name,
        )
        for row in rows
    ]

    branches_out = await ticket_branches_payload(session, ticket)

    return TicketCommitsResponse(
        branch_name=ticket.branch_name,
        branches=branches_out,
        commits=commits_out,
    )
