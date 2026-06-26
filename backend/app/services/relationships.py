"""Relationship-scoring service (PH-284, epic PH-283 child A — FOUNDATION;
evolved by PH-287 into a weighted, noise-suppressed, explainable multi-signal
relevance model).

A scoring/aggregation layer ON TOP of the edges already derived by
``services/graph.py``. We do NOT re-implement edge derivation — we reuse
graph.py's reference parsers (``_extract_keys`` / ``_resolve_keys`` /
``_TICKET_KEY_RE``) and labels.py's dialect-aware unnest (``label_match_predicate``).
The NEW piece is, per related ticket, an explainable ``score`` + structured
``reasons``.

``related_tickets(session, actor, ticket, cross_board, limit)`` is a pure
``(session, actor, ...) -> list[RelatedTicket]`` seam (no MCP-layer coupling) so
Child C (``recall_context``, PH-286) can call it directly; the MCP dispatch arm is
a thin adapter.

Read-only over existing data — NO migration. Auth = global ``ticket.read``
(PH-287: pm + orchestrator now hold it via role defaults so the Coordinator can
call the tool; the stranger-with-no-membership invariant is preserved).

PH-287 scoring (deterministic + explainable — every non-zero term emits exactly
one ``RelatedReason`` whose stated weight sums back to ``score``):

    score = 8.0 * dependency           # explicit Depends on:/blocked by/blocks — strongest
          + 5.0 * reference            # plain PH-XXX mention (no dep keyword)
          + 3.0 * epic                 # same epic / parent / child
          + min(0.6 * Σ idf(label), 8.0)       # IDF-weighted shared labels; hubs (n>=15) = 0
          + min(0.5 * Σ idf(file),  6.0)       # code overlap: shared touched files (git cache)

Signal precedence (the ORDERING invariant — the contract; exact numbers are
tunable consts):

    dependency > reference > epic > IDF-shared-labels(rare) > code_overlap
                                  ... > single hub label (== 0, dropped entirely)

IDF self-suppresses hub-label noise: a label on 128 tickets (``frontend``) is
HARD-DROPPED (n_label >= 15 ⇒ weight 0, no reason), while a label on 2-3 tickets
scores high (the meaningful connectors). This replaces PH-284's flat
``min(count, 3) * 1.0`` which let hub labels dominate the hairball.

N+1 discipline (grounded in graph.py / labels.py): the candidate gather is a
CONSTANT number of batched queries independent of how many relations exist —
ONE shared-label query + ONE label-frequency GROUP BY + ONE N_total count, ONE
batched outbound ``_resolve_keys`` + ONE inbound substring query, ONE epic
predicate query, and (NEW) TWO set-based git-cache queries + ONE candidate load
for code-overlap. Candidate ``board.key`` is eager-loaded.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Actor, BoardMembership, Ticket
from app.schemas import RelatedReason, RelatedTicket
from app.services.git_overlap import src_file_paths, tickets_touching_paths
from app.services.graph import _TICKET_KEY_RE, _extract_keys, _resolve_keys
from app.services.labels import _labels_table_function, label_match_predicate
from app.services.search import _ci_contains
from app.services.tickets import get_ticket

# Mirror of schemas.RelatedReason.type — the explainable signal kinds.
ReasonType = Literal["shared_label", "reference", "epic", "dependency", "code_overlap"]

# --- Scoring constants (tunable; the ORDERING invariant is the contract) -----
_DEPENDENCY_WEIGHT = 8.0  # explicit human-authored directed dependency — strongest
_REFERENCE_WEIGHT = 5.0  # plain PH-XXX mention (no dependency keyword)
_EPIC_WEIGHT = 3.0  # same epic / parent / child — structural grouping

# IDF-weighted shared labels: hubs (n_label >= threshold) drop to 0; rare labels
# (2-3 tickets) score high. idf = max(0, log((N+1)/(n+1))); contribution scaled +
# capped so a pathological many-rare-shared case can't run away.
_HUB_LABEL_THRESHOLD = 15  # n_label >= this ⇒ weight 0 (belt-and-suspenders hard drop)
_LABEL_SIGNAL_WEIGHT = 0.6
_LABEL_CONTRIB_CAP = 8.0

# Code-overlap (shared touched files): file-IDF (a file touched by many tickets
# is less specific). Same shape as labels; modest weight (shared infra files are
# a strong-but-noisier signal).
_CODE_OVERLAP_WEIGHT = 0.5
_CODE_OVERLAP_CAP = 6.0
_CODE_PATHS_IN_REASON = 3  # name first N shared files, then "+M more"

# Explicit dependency keyword → direction. `blocked by X` == "this depends on X".
_DEP_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"depends?\s+on[:\s]+", re.IGNORECASE), "depends_on"),
    (re.compile(r"blocked\s+by[:\s]+", re.IGNORECASE), "depends_on"),
    (re.compile(r"\bblocks[:\s]+", re.IGNORECASE), "blocks"),
)

# Eager-load only board.key on a candidate (labels are inline on the row).
_CANDIDATE_OPTIONS = (selectinload(Ticket.board),)


async def _require_ticket_read(session: AsyncSession, actor: Actor) -> None:
    """Global read gate: the actor must hold ``ticket.read`` (or ``*``) under the
    role of AT LEAST ONE board membership.

    Copied verbatim from ``services/search._require_ticket_read`` — the tool
    exposes cross-board ticket identity, so it is a cross-board ticket read.
    PH-287: pm + orchestrator now carry ``ticket.read`` in role defaults, so the
    Coordinator can call the tool; a board-less stranger still has no membership
    that grants the cap → still denied (least-privilege preserved).
    """
    from app.core.permissions import require_global_permission

    rows = (
        await session.execute(
            select(BoardMembership)
            .where(BoardMembership.actor_id == actor.id)
            .options(selectinload(BoardMembership.board))
        )
    ).scalars()
    require_global_permission(actor, "ticket.read", [(m.role, m.board) for m in rows])


# ---------------------------------------------------------------------------
# IDF helpers (label specificity precompute — 2 constant queries, src-bounded)
# ---------------------------------------------------------------------------


def _idf(n_total: int, n_item: int) -> float:
    """Smoothed natural-log IDF, clamped >= 0. An item on FEW tickets scores
    high; an item on many tickets approaches 0."""
    return max(0.0, math.log((n_total + 1) / (n_item + 1)))


async def _total_ticket_count(session: AsyncSession) -> int:
    """ONE query: count of non-deleted tickets (N_total for the IDF formula)."""
    stmt = select(func.count()).select_from(Ticket).where(Ticket.deleted_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def _label_freq_map(
    session: AsyncSession, src_labels: set[str]
) -> dict[str, int]:
    """ONE batched GROUP BY: ``{label: n_label}`` for the SRC's own labels only
    (never the whole 437-label space). Dialect-aware unnest (reuse labels.py).

    Empty src-labels ⇒ no query (preserve the constant-statement-count invariant).
    """
    if not src_labels:
        return {}
    elements = _labels_table_function(
        session.bind.dialect if session.bind is not None else "sqlite"
    )
    stmt = (
        select(elements.c.value, func.count())
        .select_from(Ticket)
        .join(elements, true())
        .where(elements.c.value.in_(src_labels), Ticket.deleted_at.is_(None))
        .group_by(elements.c.value)
    )
    return {value: int(count) for value, count in (await session.execute(stmt)).all()}


def _label_idf_map(
    freq: dict[str, int], n_total: int
) -> dict[str, float]:
    """``{label: idf}`` with HUB labels (n_label >= threshold) HARD-DROPPED to 0
    (so they emit no reason and contribute nothing — AC1)."""
    out: dict[str, float] = {}
    for label, n_label in freq.items():
        out[label] = 0.0 if n_label >= _HUB_LABEL_THRESHOLD else _idf(n_total, n_label)
    return out


# ---------------------------------------------------------------------------
# Dependency parsing (explicit directed deps, distinct from plain references)
# ---------------------------------------------------------------------------


def _parse_dependency_keys(text: str) -> dict[str, str]:
    """``{ticket_key: direction}`` for EXPLICIT dependency lines in ``text``.

    For each line, the FIRST matching dependency keyword (``depends on`` /
    ``blocked by`` → ``depends_on``; ``blocks`` → ``blocks``) classifies the keys
    that appear AFTER it on that line. Plain ``PH-XXX`` mentions with no keyword
    are NOT captured here (they stay the lower-weight reference signal).
    """
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        for pattern, direction in _DEP_PATTERNS:
            m = pattern.search(line)
            if m is None:
                continue
            for key in _TICKET_KEY_RE.findall(line[m.end() :]):
                out.setdefault(key, direction)
            break  # first dep keyword on the line wins (avoid re-scan)
    return out


# ---------------------------------------------------------------------------
# Accumulator — per-candidate signal aggregation + explainable scoring
# ---------------------------------------------------------------------------


@dataclass
class _Accumulator:
    """Per-candidate relation signal aggregator (merged across relation sources).

    ``signal_weights`` is the single source of truth for BOTH ``score()`` and
    ``reasons()`` — each is computed ONCE (in the merge passes / lazily) and stored
    here, so ``score == sum(reason weights)`` BY CONSTRUCTION (no drift — AC6)."""

    ticket: Ticket
    shared_label_idf: dict[str, float] = field(default_factory=dict)  # label -> idf>0
    reference: bool = False
    ref_outbound: bool = False  # src references candidate
    ref_inbound: bool = False  # candidate references src
    epic: bool = False
    epic_detail: str = ""
    dep_direction: str = ""  # "" | "depends_on" | "blocks" (src→cand orientation)
    code_paths: dict[str, float] = field(default_factory=dict)  # path -> file idf

    # --- per-signal contribution (filled by _signal_weights, read by both methods)
    def _signal_weights(self) -> dict[str, float]:
        weights: dict[str, float] = {}
        if self.dep_direction:
            weights["dependency"] = _DEPENDENCY_WEIGHT
        if self.reference:
            weights["reference"] = _REFERENCE_WEIGHT
        if self.epic:
            weights["epic"] = _EPIC_WEIGHT
        label_raw = _LABEL_SIGNAL_WEIGHT * sum(self.shared_label_idf.values())
        label_w = min(label_raw, _LABEL_CONTRIB_CAP)
        if label_w > 0:
            weights["shared_label"] = label_w
        code_raw = _CODE_OVERLAP_WEIGHT * sum(self.code_paths.values())
        code_w = min(code_raw, _CODE_OVERLAP_CAP)
        if code_w > 0:
            weights["code_overlap"] = code_w
        return weights

    def score(self) -> float:
        return sum(self._signal_weights().values())

    def reasons(self, src_key: str) -> list[RelatedReason]:
        """One RelatedReason per NON-ZERO signal, in precedence order. The detail
        builders read the SAME stored signal data as ``_signal_weights`` so the
        reasons reconstruct ``score`` exactly (no drift)."""
        weights = self._signal_weights()
        details: dict[ReasonType, str] = {
            "dependency": self._dependency_detail(src_key),
            "reference": self._reference_detail(src_key),
            "epic": self.epic_detail,
            "shared_label": self._shared_label_detail(),
            "code_overlap": self._code_overlap_detail(),
        }
        order: tuple[ReasonType, ...] = (
            "dependency",
            "reference",
            "epic",
            "shared_label",
            "code_overlap",
        )
        return [
            RelatedReason(type=rtype, detail=details[rtype])
            for rtype in order
            if rtype in weights
        ]

    def _dependency_detail(self, src_key: str) -> str:
        cand_key = self.ticket.key
        if self.dep_direction == "blocks":
            return f"{cand_key} blocks {src_key} (explicit)"
        return f"{src_key} depends on {cand_key} (explicit)"

    def _reference_detail(self, src_key: str) -> str:
        cand_key = self.ticket.key
        if self.ref_outbound and self.ref_inbound:
            return f"{src_key} ↔ {cand_key} (mutual reference)"
        if self.ref_outbound:
            return f"{src_key} → {cand_key} (referenced in {src_key} description)"
        return f"{cand_key} → {src_key} (referenced in {cand_key} description)"

    def _shared_label_detail(self) -> str:
        parts = [
            f"{label} (idf {idf:.1f})"
            for label, idf in sorted(
                self.shared_label_idf.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ]
        return "shared specific labels: " + ", ".join(parts)

    def _code_overlap_detail(self) -> str:
        paths = sorted(self.code_paths)
        head = ", ".join(paths[:_CODE_PATHS_IN_REASON])
        extra = len(paths) - _CODE_PATHS_IN_REASON
        suffix = f" (+{extra} more)" if extra > 0 else ""
        return f"touched same files: {head}{suffix}"


def _accumulate(acc_map: dict[UUID, _Accumulator], cand: Ticket) -> _Accumulator:
    """Fetch-or-create the accumulator for a candidate ticket id."""
    acc = acc_map.get(cand.id)
    if acc is None:
        acc = _Accumulator(ticket=cand)
        acc_map[cand.id] = acc
    return acc


# ---------------------------------------------------------------------------
# Candidate queries (each a constant, batched query — no N+1)
# ---------------------------------------------------------------------------


async def _shared_label_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> list[Ticket]:
    """ONE dialect-aware query: non-deleted tickets carrying ANY of ``src.labels``."""
    if not src.labels:
        return []
    dialect = session.bind.dialect if session.bind is not None else "sqlite"
    predicates = [label_match_predicate(dialect, value) for value in src.labels]
    stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None), or_(*predicates))
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        stmt = stmt.where(Ticket.board_id == src.board_id)
    return list((await session.execute(stmt)).scalars())


async def _reference_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> tuple[list[Ticket], set[UUID], set[UUID]]:
    """Bidirectional reference candidates (NO N+1). Returns
    ``(candidate_tickets, outbound_ids, inbound_ids)``."""
    outbound_ids: set[UUID] = set()
    inbound_ids: set[UUID] = set()
    by_id: dict[UUID, Ticket] = {}

    out_keys = _extract_keys(src.description)
    key_map = await _resolve_keys(session, out_keys)
    resolved_ids = {tid for tid, _ in key_map.values() if tid != src.id}
    if resolved_ids:
        stmt = (
            select(Ticket)
            .where(Ticket.id.in_(resolved_ids), Ticket.deleted_at.is_(None))
            .options(*_CANDIDATE_OPTIONS)
        )
        if not cross_board:
            stmt = stmt.where(Ticket.board_id == src.board_id)
        for cand in (await session.execute(stmt)).scalars():
            by_id[cand.id] = cand
            outbound_ids.add(cand.id)

    in_stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None), _ci_contains(Ticket.description, src.key))
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        in_stmt = in_stmt.where(Ticket.board_id == src.board_id)
    for cand in (await session.execute(in_stmt)).scalars():
        if cand.id == src.id:
            continue
        if src.key not in _extract_keys(cand.description):
            continue
        by_id.setdefault(cand.id, cand)
        inbound_ids.add(cand.id)

    return list(by_id.values()), outbound_ids, inbound_ids


async def _epic_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> list[Ticket]:
    """ONE query: epic siblings + children + the parent."""
    predicates = [Ticket.epic_id == src.id]  # children of src
    if src.epic_id is not None:
        predicates.append(Ticket.epic_id == src.epic_id)  # siblings
        predicates.append(Ticket.id == src.epic_id)  # the parent
    stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None), or_(*predicates))
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        stmt = stmt.where(Ticket.board_id == src.board_id)
    return list((await session.execute(stmt)).scalars())


def _epic_detail(src: Ticket, cand: Ticket) -> str:
    """Human-readable epic relation between src and a candidate."""
    if src.epic_id is not None and cand.id == src.epic_id:
        return f"{cand.key} is the epic of {src.key}"
    if cand.epic_id == src.id:
        return f"{cand.key} is a child of epic {src.key}"
    return f"same epic ({cand.key} sibling of {src.key})"


async def _code_overlap_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> tuple[dict[UUID, set[str]], dict[str, float]]:
    """Set-based code overlap (2 git queries). Returns
    ``({other_ticket_id: {shared_paths}}, {path: file_idf})``.

    Q1 src paths → (guard empty) Q2 pairs; file specificity (``n_file`` = distinct
    tickets per path) is counted from the SAME Q2 rows + src itself — no 3rd query.
    """
    src_paths = await src_file_paths(session, src.id)
    if not src_paths:
        return {}, {}

    pairs = await tickets_touching_paths(session, src.id, src_paths)
    overlap: dict[UUID, set[str]] = {}
    path_tickets: dict[str, set[UUID]] = {p: {src.id} for p in src_paths}
    for ticket_id, path in pairs:
        overlap.setdefault(ticket_id, set()).add(path)
        path_tickets.setdefault(path, set()).add(ticket_id)

    n_total = await _total_ticket_count(session)
    file_idf = {p: _idf(n_total, len(tickets)) for p, tickets in path_tickets.items()}
    return overlap, file_idf


# ---------------------------------------------------------------------------
# Merge passes (write signals onto the accumulator map)
# ---------------------------------------------------------------------------


async def _merge_shared_label(
    acc_map: dict[UUID, _Accumulator],
    session: AsyncSession,
    src: Ticket,
    idf_map: dict[str, float],
    *,
    cross_board: bool,
) -> None:
    """Merge IDF-weighted shared labels (hubs already 0 in ``idf_map`` → dropped)."""
    src_labels = set(src.labels or ())
    for cand in await _shared_label_candidates(session, src, cross_board=cross_board):
        if cand.id == src.id:
            continue
        shared = (set(cand.labels or ()) & src_labels)
        weighted = {lbl: idf_map.get(lbl, 0.0) for lbl in shared if idf_map.get(lbl, 0.0) > 0}
        if not weighted:
            continue  # hub-only overlap contributes nothing, emits no reason (AC1)
        _accumulate(acc_map, cand).shared_label_idf.update(weighted)


async def _merge_reference(
    acc_map: dict[UUID, _Accumulator],
    session: AsyncSession,
    src: Ticket,
    dep_keys: dict[str, str],
    *,
    cross_board: bool,
) -> None:
    """Merge plain references EXCLUDING keys already captured as dependencies
    (no double-count — a dep-key yields only a ``dependency`` reason)."""
    ref_cands, outbound_ids, inbound_ids = await _reference_candidates(
        session, src, cross_board=cross_board
    )
    for cand in ref_cands:
        if cand.id == src.id or cand.key in dep_keys:
            continue
        acc = _accumulate(acc_map, cand)
        acc.reference = True
        acc.ref_outbound = acc.ref_outbound or cand.id in outbound_ids
        acc.ref_inbound = acc.ref_inbound or cand.id in inbound_ids


async def _merge_dependency(
    acc_map: dict[UUID, _Accumulator],
    session: AsyncSession,
    src: Ticket,
    dep_keys: dict[str, str],
    *,
    cross_board: bool,
) -> None:
    """Merge EXPLICIT directed dependencies (highest weight). Reuses the batched
    ``_resolve_keys`` then loads those candidate tickets (board-filtered)."""
    if not dep_keys:
        return
    key_map = await _resolve_keys(session, set(dep_keys))
    dep_ids = {tid: dep_keys[key] for key, (tid, _) in key_map.items() if tid != src.id}
    if not dep_ids:
        return
    stmt = (
        select(Ticket)
        .where(Ticket.id.in_(dep_ids), Ticket.deleted_at.is_(None))
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        stmt = stmt.where(Ticket.board_id == src.board_id)
    for cand in (await session.execute(stmt)).scalars():
        _accumulate(acc_map, cand).dep_direction = dep_ids[cand.id]


async def _merge_epic(
    acc_map: dict[UUID, _Accumulator],
    session: AsyncSession,
    src: Ticket,
    *,
    cross_board: bool,
) -> None:
    """Merge epic (sibling/parent/child) candidates into ``acc_map`` (src excluded)."""
    for cand in await _epic_candidates(session, src, cross_board=cross_board):
        if cand.id == src.id:
            continue
        acc = _accumulate(acc_map, cand)
        acc.epic = True
        acc.epic_detail = _epic_detail(src, cand)


async def _merge_code_overlap(
    acc_map: dict[UUID, _Accumulator],
    session: AsyncSession,
    src: Ticket,
    *,
    cross_board: bool,
) -> None:
    """Merge code-overlap (shared touched files) into ``acc_map``. Loads the
    overlapping candidate tickets in ONE batched query (board-filtered)."""
    overlap, file_idf = await _code_overlap_candidates(
        session, src, cross_board=cross_board
    )
    if not overlap:
        return
    stmt = (
        select(Ticket)
        .where(Ticket.id.in_(overlap), Ticket.deleted_at.is_(None))
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        stmt = stmt.where(Ticket.board_id == src.board_id)
    for cand in (await session.execute(stmt)).scalars():
        if cand.id == src.id:
            continue
        acc = _accumulate(acc_map, cand)
        acc.code_paths.update({p: file_idf.get(p, 0.0) for p in overlap[cand.id]})


def _sort_key(acc: _Accumulator) -> tuple[float, float, str]:
    """Deterministic order: score desc, updated_at desc, key asc."""
    updated = acc.ticket.updated_at
    ts = updated.timestamp() if updated is not None else 0.0
    return (-acc.score(), -ts, acc.ticket.key)


async def related_tickets(
    session: AsyncSession,
    actor: Actor,
    *,
    ticket: str,
    cross_board: bool = True,
    limit: int = 20,
) -> list[RelatedTicket]:
    """Scored, explainable related tickets for ``ticket`` (key OR uuid).

    Read-gated on global ``ticket.read``. Resolves the input ticket
    (``NotFound`` if missing), precomputes label IDF (2 constant queries), then
    gathers dependency / reference / epic / IDF-label / code-overlap candidates
    (each a constant, batched query), merges by ticket id (input excluded), scores
    + builds reasons (reasons sum to score by construction), sorts by
    ``(score desc, updated_at desc, key asc)``, returns the top ``limit``. No
    relations → ``[]`` (never an error).

    ``cross_board=False`` restricts every candidate query to the input's board.
    """
    await _require_ticket_read(session, actor)
    src = await get_ticket(session, ticket)

    # Precompute label specificity (hubs → 0) once; reused by the label merge.
    n_total = await _total_ticket_count(session)
    idf_map = _label_idf_map(
        await _label_freq_map(session, set(src.labels or ())), n_total
    )
    dep_keys = _parse_dependency_keys(src.description)

    acc_map: dict[UUID, _Accumulator] = {}
    await _merge_dependency(acc_map, session, src, dep_keys, cross_board=cross_board)
    await _merge_reference(acc_map, session, src, dep_keys, cross_board=cross_board)
    await _merge_epic(acc_map, session, src, cross_board=cross_board)
    await _merge_shared_label(acc_map, session, src, idf_map, cross_board=cross_board)
    await _merge_code_overlap(acc_map, session, src, cross_board=cross_board)

    ordered = sorted(acc_map.values(), key=_sort_key)[:limit]
    return [
        RelatedTicket(
            key=acc.ticket.key,
            title=acc.ticket.title,
            board=acc.ticket.board.key,
            state=acc.ticket.state,
            score=acc.score(),
            reasons=acc.reasons(src.key),
        )
        for acc in ordered
    ]
