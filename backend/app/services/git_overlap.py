"""Code-overlap signal helper (PH-287, epic PH-283 child) — git-cache coupling
isolated from ``services/relationships.py`` (mirrors how ``services/labels.py``
isolates the dialect-aware unnest).

Two tickets that touched the SAME file IN THE SAME REPO are code-related. The
relation is derived from the PH-152 git cache (``git_commit_tickets`` ⋈
``git_commit_files`` ⋈ ``git_commits`` for ``repo_id``) read-only — NO migration.
The contract is SET-BASED (no per-ticket loop over the 23k-row file table):
exactly TWO queries regardless of how many overlapping tickets exist, so the
relationships N+1 invariant holds for this signal too.

PH-289 — REPO SCOPING: overlap is keyed by ``(repo_id, path)``, never by the bare
``path`` string. Every Jarwis-managed repo carries the SAME scaffold files
(``CLAUDE.md``, ``.gitignore``, ``.claude/agents/*.md``, ``docs/codewiki/log.md``,
``sonar-project.properties`` …); matching those across DIFFERENT repos made a
restaurant-POS board (Kims) look "code-related" to an Android-game board and
manufactured a cross-board hairball in ``/space``. Since a repo belongs to exactly
one board, requiring the same ``repo_id`` on both sides makes code overlap a
strictly intra-repo (hence intra-board) signal — the correct semantics (two files
with the same relative path in two separate codebases are NOT the same file).

    Q1 — ``src_file_paths(session, src_id)``: ONE query → the DISTINCT set of
         ``(repo_id, path)`` pairs the src ticket's commits touched. Empty (src
         has no commits) ⇒ the caller skips Q2 entirely → no ``code_overlap``
         reason (graceful).

    Q2 — ``tickets_touching_paths(session, src_id, repo_paths)``: ONE query → the
         ``(other_ticket_id, shared_path)`` pairs for every OTHER ticket whose
         commits touched one of ``repo_paths`` IN THE SAME REPO. The caller
         aggregates in Python into ``{ticket_id: {shared_paths}}`` and derives
         per-file specificity (``n_file`` = distinct tickets per path) from the
         SAME result rows — no third query.

``src_file_paths`` is bounded defensively (``_SRC_PATH_CAP``) so a pathological
mega-commit src cannot inflate the ``IN (...)`` list; the path column carries no
dedicated index (only ``ix_git_commit_files_commit_id``), but ``paths`` is small
and bounded, acceptable for a read tool (see PH-287 technical_depth Risks).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import GitCommit, GitCommitFile, GitCommitTicket

# Defensive bound on how many src paths feed the Q2 ``IN (...)`` predicate. A
# ticket touching hundreds of files is rare; capping keeps the unindexed-path
# scan bounded without a migration. Deterministic: sorted before slicing.
_SRC_PATH_CAP = 200


async def src_file_paths(session: AsyncSession, src_id: UUID) -> set[tuple[UUID, str]]:
    """Q1: DISTINCT ``(repo_id, path)`` pairs touched by ``src_id``'s commits.

    ONE set-based query joining ``git_commit_tickets`` → ``git_commit_files`` →
    ``git_commits`` (for ``repo_id``). Binary files are excluded (no meaningful
    code overlap). Carrying ``repo_id`` alongside ``path`` is what makes overlap
    REPO-SCOPED (PH-289) — the same relative path in two different repos is not the
    same file. Capped at ``_SRC_PATH_CAP`` deterministically (sorted) so a giant
    src cannot inflate the downstream predicate. Empty ⇒ src has no commits → the
    caller emits no ``code_overlap``.
    """
    stmt = (
        select(GitCommit.repo_id, GitCommitFile.path)
        .join(GitCommitTicket, GitCommitTicket.commit_id == GitCommitFile.commit_id)
        .join(GitCommit, GitCommit.id == GitCommitFile.commit_id)
        .where(
            GitCommitTicket.ticket_id == src_id,
            GitCommitFile.is_binary.is_(False),
        )
        .distinct()
    )
    pairs = {(repo_id, path) for repo_id, path in (await session.execute(stmt)).all()}
    if len(pairs) > _SRC_PATH_CAP:
        return set(sorted(pairs, key=lambda rp: (str(rp[0]), rp[1]))[:_SRC_PATH_CAP])
    return pairs


async def tickets_touching_paths(
    session: AsyncSession, src_id: UUID, repo_paths: set[tuple[UUID, str]]
) -> list[tuple[UUID, str]]:
    """Q2: ``(other_ticket_id, shared_path)`` pairs for tickets (≠ src) whose
    commits touched one of ``repo_paths`` IN THE SAME REPO.

    ONE set-based, DISTINCT query joining ``git_commit_tickets`` →
    ``git_commit_files`` → ``git_commits``. Matches on ``(repo_id, path)`` — not
    the bare ``path`` (PH-289) — so an overlap only counts when both tickets
    touched the file in the SAME repository. Paths are grouped by repo into an OR
    of ``repo_id == r AND path IN (...)`` terms (portable on sqlite + Postgres;
    avoids a row-value ``IN``). Returns flat pairs; the caller groups them into
    ``{ticket_id: {paths}}`` and counts distinct tickets per path from these SAME
    rows — no extra query. Empty ``repo_paths`` ⇒ no query (the caller guards it).
    """
    if not repo_paths:
        return []
    by_repo: dict[UUID, set[str]] = {}
    for repo_id, path in repo_paths:
        by_repo.setdefault(repo_id, set()).add(path)
    repo_terms = [
        and_(GitCommit.repo_id == repo_id, GitCommitFile.path.in_(paths))
        for repo_id, paths in by_repo.items()
    ]
    stmt = (
        select(GitCommitTicket.ticket_id, GitCommitFile.path)
        .join(GitCommitFile, GitCommitFile.commit_id == GitCommitTicket.commit_id)
        .join(GitCommit, GitCommit.id == GitCommitTicket.commit_id)
        .where(or_(*repo_terms), GitCommitTicket.ticket_id != src_id)
        .distinct()
    )
    return [(tid, path) for tid, path in (await session.execute(stmt)).all()]


async def all_overlapping_pairs(
    session: AsyncSession, ticket_ids: set[UUID]
) -> list[tuple[UUID, UUID, str]]:
    """ONE set-based self-join: ``(ticket_a, ticket_b, shared_path)`` rows for
    every UNORDERED pair of in-scope tickets that touched the SAME file (PH-288).

    This is the BATCHED, all-pairs counterpart of ``tickets_touching_paths``
    (which is src-anchored). The graph computes code-overlap adjacency over the
    WHOLE in-scope ticket set in a SINGLE query instead of N per-src queries —
    the load-bearing N+1 bound for the graph's code-overlap signal.

    Shape: a self-join of ``git_commit_tickets`` (via ``git_commit_files`` on
    ``commit_id``) against itself on shared ``path`` AND shared ``repo_id``
    (PH-289 — the same relative path in two different repos is not the same file),
    restricted to the in-scope ``ticket_ids`` on BOTH sides, with ``a < b`` to emit
    each unordered pair once (and skip self-pairs). Binary files excluded (no
    meaningful code overlap). The caller aggregates rows into ``{(a,b): {paths}}``
    + derives per-file IDF (``n_file`` = distinct in-scope tickets per path) from
    the SAME rows — no extra query. Empty ``ticket_ids`` ⇒ no query
    (constant-statement guard).
    """
    if not ticket_ids:
        return []
    gct_a = aliased(GitCommitTicket)
    gct_b = aliased(GitCommitTicket)
    gcf_a = aliased(GitCommitFile)
    gcf_b = aliased(GitCommitFile)
    gc_a = aliased(GitCommit)
    gc_b = aliased(GitCommit)
    stmt = (
        select(gct_a.ticket_id, gct_b.ticket_id, gcf_a.path)
        .join(gcf_a, gcf_a.commit_id == gct_a.commit_id)
        .join(gcf_b, gcf_b.path == gcf_a.path)
        .join(gct_b, gct_b.commit_id == gcf_b.commit_id)
        .join(gc_a, gc_a.id == gcf_a.commit_id)
        .join(gc_b, gc_b.id == gcf_b.commit_id)
        .where(
            gct_a.ticket_id.in_(ticket_ids),
            gct_b.ticket_id.in_(ticket_ids),
            gct_a.ticket_id < gct_b.ticket_id,
            gcf_a.is_binary.is_(False),
            gcf_b.is_binary.is_(False),
            gc_a.repo_id == gc_b.repo_id,  # PH-289: same-repo only
        )
        .distinct()
    )
    return [(a, b, path) for a, b, path in (await session.execute(stmt)).all()]
