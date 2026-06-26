"""Code-overlap signal helper (PH-287, epic PH-283 child) — git-cache coupling
isolated from ``services/relationships.py`` (mirrors how ``services/labels.py``
isolates the dialect-aware unnest).

Two tickets that touched the SAME file(s) are code-related. The relation is
derived from the PH-152 git cache (``git_commit_tickets`` ⋈ ``git_commit_files``)
read-only — NO migration. The contract is SET-BASED (no per-ticket loop over the
23k-row file table): exactly TWO queries regardless of how many overlapping
tickets exist, so the relationships N+1 invariant holds for this signal too.

    Q1 — ``src_file_paths(session, src_id)``: ONE query → the DISTINCT set of file
         paths the src ticket's commits touched. Empty (src has no commits) ⇒ the
         caller skips Q2 entirely → no ``code_overlap`` reason (graceful).

    Q2 — ``tickets_touching_paths(session, src_id, paths)``: ONE query → the
         ``(other_ticket_id, shared_path)`` pairs for every OTHER ticket whose
         commits touched any of ``paths``. The caller aggregates in Python into
         ``{ticket_id: {shared_paths}}`` and derives per-file specificity
         (``n_file`` = distinct tickets per path) from the SAME result rows — no
         third query.

``src_file_paths`` is bounded defensively (``_SRC_PATH_CAP``) so a pathological
mega-commit src cannot inflate the ``IN (...)`` list; the path column carries no
dedicated index (only ``ix_git_commit_files_commit_id``), but ``paths`` is small
and bounded, acceptable for a read tool (see PH-287 technical_depth Risks).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import GitCommitFile, GitCommitTicket

# Defensive bound on how many src paths feed the Q2 ``IN (...)`` predicate. A
# ticket touching hundreds of files is rare; capping keeps the unindexed-path
# scan bounded without a migration. Deterministic: sorted before slicing.
_SRC_PATH_CAP = 200


async def src_file_paths(session: AsyncSession, src_id: UUID) -> set[str]:
    """Q1: DISTINCT non-binary file paths touched by ``src_id``'s commits.

    ONE set-based query joining ``git_commit_tickets`` → ``git_commit_files`` on
    ``commit_id``. Binary files are excluded (no meaningful code overlap). The
    result is capped at ``_SRC_PATH_CAP`` deterministically (sorted) so a giant
    src cannot inflate the downstream ``IN (...)`` list. Empty ⇒ src has no
    commits → the caller emits no ``code_overlap``.
    """
    stmt = (
        select(GitCommitFile.path)
        .join(GitCommitTicket, GitCommitTicket.commit_id == GitCommitFile.commit_id)
        .where(
            GitCommitTicket.ticket_id == src_id,
            GitCommitFile.is_binary.is_(False),
        )
        .distinct()
    )
    paths = {row[0] for row in (await session.execute(stmt)).all()}
    if len(paths) > _SRC_PATH_CAP:
        return set(sorted(paths)[:_SRC_PATH_CAP])
    return paths


async def tickets_touching_paths(
    session: AsyncSession, src_id: UUID, paths: set[str]
) -> list[tuple[UUID, str]]:
    """Q2: ``(other_ticket_id, shared_path)`` pairs for tickets (≠ src) whose
    commits touched any path in ``paths``.

    ONE set-based, DISTINCT query joining ``git_commit_tickets`` →
    ``git_commit_files``. Returns flat pairs; the caller groups them into
    ``{ticket_id: {paths}}`` and counts distinct tickets per path (file
    specificity) from these SAME rows — no extra query. Empty ``paths`` ⇒ no
    query, empty result (the caller already guards this).
    """
    if not paths:
        return []
    stmt = (
        select(GitCommitTicket.ticket_id, GitCommitFile.path)
        .join(GitCommitFile, GitCommitFile.commit_id == GitCommitTicket.commit_id)
        .where(
            GitCommitFile.path.in_(paths),
            GitCommitTicket.ticket_id != src_id,
        )
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
    ``commit_id``) against itself on shared ``path``, restricted to the in-scope
    ``ticket_ids`` on BOTH sides, with ``a < b`` to emit each unordered pair once
    (and skip self-pairs). Binary files excluded (no meaningful code overlap).
    The caller aggregates rows into ``{(a,b): {paths}}`` + derives per-file IDF
    (``n_file`` = distinct in-scope tickets per path) from the SAME rows — no
    extra query. Empty ``ticket_ids`` ⇒ no query (constant-statement guard).
    """
    if not ticket_ids:
        return []
    gct_a = aliased(GitCommitTicket)
    gct_b = aliased(GitCommitTicket)
    gcf_a = aliased(GitCommitFile)
    gcf_b = aliased(GitCommitFile)
    stmt = (
        select(gct_a.ticket_id, gct_b.ticket_id, gcf_a.path)
        .join(gcf_a, gcf_a.commit_id == gct_a.commit_id)
        .join(gcf_b, gcf_b.path == gcf_a.path)
        .join(gct_b, gct_b.commit_id == gcf_b.commit_id)
        .where(
            gct_a.ticket_id.in_(ticket_ids),
            gct_b.ticket_id.in_(ticket_ids),
            gct_a.ticket_id < gct_b.ticket_id,
            gcf_a.is_binary.is_(False),
            gcf_b.is_binary.is_(False),
        )
        .distinct()
    )
    return [(a, b, path) for a, b, path in (await session.execute(stmt)).all()]
