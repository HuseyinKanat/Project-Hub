"""Relationship-scoring service (PH-284, epic PH-283 child A — FOUNDATION).

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
(copied from ``services/search._require_ticket_read``): the tool exposes
cross-board ticket identity, so it is a cross-board ticket read, gated in the
SERVICE (the MCP catalog carries no ``permission=`` — advertisement only).

Scoring (deterministic + explainable):

    score = 5.0 * reference + 3.0 * epic + min(shared_label_count, 3) * 1.0

Reference (an explicit human-authored link) is strongest, epic (structural
grouping) next, shared-label weakest + count-scaled but CAPPED at 3 so a hub
label (e.g. ``backend`` co-occurring on dozens of tickets) cannot outrank a real
reference. The ordering invariant ``reference > epic > single strong label
overlap`` is the contract; exact numbers are tunable constants below.

N+1 discipline (grounded in graph.py / labels.py): the candidate gather is a
CONSTANT number of batched queries independent of how many relations exist:
ONE dialect-aware shared-label query, ONE batched outbound ``_resolve_keys`` +
ONE inbound substring query for references, ONE epic column-predicate query,
plus the membership gate + the src fetch. Candidate ``board.key`` is eager-loaded
(``selectinload(Ticket.board)``), labels read inline off the loaded ARRAY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Actor, BoardMembership, Ticket
from app.schemas import RelatedReason, RelatedTicket
from app.services.graph import _extract_keys, _resolve_keys
from app.services.labels import label_match_predicate
from app.services.search import _ci_contains
from app.services.tickets import get_ticket

# --- Scoring constants (tunable; the ORDERING invariant is the contract) -----
_REFERENCE_WEIGHT = 5.0  # explicit human-authored this↔PH-X link — strongest
_EPIC_WEIGHT = 3.0  # same epic / parent / child — structural grouping
_SHARED_LABEL_WEIGHT = 1.0  # per shared label …
_SHARED_LABEL_CAP = 3  # … capped so a hub label can't outrank a reference

# Eager-load only board.key on a candidate (labels are inline on the row).
_CANDIDATE_OPTIONS = (selectinload(Ticket.board),)


async def _require_ticket_read(session: AsyncSession, actor: Actor) -> None:
    """Global read gate: the actor must hold ``ticket.read`` (or ``*``) under the
    role of AT LEAST ONE board membership.

    Copied verbatim from ``services/search._require_ticket_read`` — the tool
    exposes cross-board ticket identity, so it is a cross-board ticket read.
    Loads ``(role, board)`` pairs with ``board.roles`` eager-loaded
    (``current_actor`` only selectinloads ``Actor.memberships``, NOT the board).
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


@dataclass
class _Accumulator:
    """Per-candidate relation signal aggregator (merged across relation sources)."""

    ticket: Ticket
    shared_labels: set[str] = field(default_factory=set)
    reference: bool = False
    ref_outbound: bool = False  # src references candidate
    ref_inbound: bool = False  # candidate references src
    epic: bool = False
    epic_detail: str = ""

    def score(self) -> float:
        s = 0.0
        if self.reference:
            s += _REFERENCE_WEIGHT
        if self.epic:
            s += _EPIC_WEIGHT
        s += min(len(self.shared_labels), _SHARED_LABEL_CAP) * _SHARED_LABEL_WEIGHT
        return s

    def reasons(self, src_key: str) -> list[RelatedReason]:
        out: list[RelatedReason] = []
        if self.reference:
            out.append(
                RelatedReason(type="reference", detail=self._reference_detail(src_key))
            )
        if self.epic:
            out.append(RelatedReason(type="epic", detail=self.epic_detail))
        if self.shared_labels:
            shared = ", ".join(sorted(self.shared_labels))
            out.append(
                RelatedReason(type="shared_label", detail=f"shared labels: {shared}")
            )
        return out

    def _reference_detail(self, src_key: str) -> str:
        cand_key = self.ticket.key
        if self.ref_outbound and self.ref_inbound:
            return f"{src_key} ↔ {cand_key} (mutual reference)"
        if self.ref_outbound:
            return f"{src_key} → {cand_key} (referenced in {src_key} description)"
        return f"{cand_key} → {src_key} (referenced in {cand_key} description)"


async def _shared_label_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> list[Ticket]:
    """ONE dialect-aware query: non-deleted tickets carrying ANY of ``src.labels``.

    Reuses ``labels.label_match_predicate`` (PG ``unnest`` / sqlite ``json_each``)
    OR-ed over each src label. Board-filtered when ``cross_board=False``. Shared
    COUNT per candidate is intersected in Python off the loaded ARRAY (no extra
    query). Empty-guard: a label-less src issues no query.
    """
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
    """Bidirectional reference candidates (NO N+1).

    - outbound (src → X): ``_extract_keys(src.description)`` → ONE batched
      ``_resolve_keys`` (empty-guarded).
    - inbound (X → src): ONE substring query (``_ci_contains(description, src.key)``)
      confirmed in Python with word-boundary ``_extract_keys`` (so ``PH-2`` does
      not match ``PH-28``).

    Returns ``(candidate_tickets, outbound_ids, inbound_ids)`` — the id sets let
    the accumulator record direction. Board-filtered when ``cross_board=False``.
    """
    outbound_ids: set[UUID] = set()
    inbound_ids: set[UUID] = set()
    by_id: dict[UUID, Ticket] = {}

    # --- outbound: keys src mentions → batched resolve → load those tickets ---
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

    # --- inbound: tickets whose description mentions src.key (substring + confirm)
    in_stmt = (
        select(Ticket)
        .where(
            Ticket.deleted_at.is_(None),
            _ci_contains(Ticket.description, src.key),
        )
        .options(*_CANDIDATE_OPTIONS)
    )
    if not cross_board:
        in_stmt = in_stmt.where(Ticket.board_id == src.board_id)
    for cand in (await session.execute(in_stmt)).scalars():
        if cand.id == src.id:
            continue
        # Confirm a real word-boundary key mention (avoid PH-2 ⊂ PH-28).
        if src.key not in _extract_keys(cand.description):
            continue
        by_id.setdefault(cand.id, cand)
        inbound_ids.add(cand.id)

    return list(by_id.values()), outbound_ids, inbound_ids


async def _epic_candidates(
    session: AsyncSession, src: Ticket, *, cross_board: bool
) -> list[Ticket]:
    """ONE query: epic siblings (same ``epic_id``) + children (``epic_id == src.id``)
    + the parent (``id == src.epic_id``).

    The sibling/parent predicates only apply when ``src.epic_id`` is set; the
    child predicate always applies (src may itself be an epic). Board-filtered
    when ``cross_board=False``.
    """
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


def _accumulate(
    acc_map: dict[UUID, _Accumulator],
    cand: Ticket,
) -> _Accumulator:
    """Fetch-or-create the accumulator for a candidate ticket id."""
    acc = acc_map.get(cand.id)
    if acc is None:
        acc = _Accumulator(ticket=cand)
        acc_map[cand.id] = acc
    return acc


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
    (``NotFound`` if missing), gathers shared-label / reference / epic candidates
    (each a constant, batched query), merges by ticket id (input excluded),
    scores + builds reasons, sorts by ``(score desc, updated_at desc, key asc)``,
    and returns the top ``limit``. No relations → ``[]`` (never an error).

    ``cross_board=False`` restricts every candidate query to the input's board.
    """
    await _require_ticket_read(session, actor)
    src = await get_ticket(session, ticket)

    acc_map: dict[UUID, _Accumulator] = {}

    # --- shared-label candidates -------------------------------------------
    src_labels = set(src.labels or ())
    for cand in await _shared_label_candidates(session, src, cross_board=cross_board):
        if cand.id == src.id:
            continue
        shared = set(cand.labels or ()) & src_labels
        if not shared:
            continue
        _accumulate(acc_map, cand).shared_labels |= shared

    # --- reference candidates (bidirectional) ------------------------------
    ref_cands, outbound_ids, inbound_ids = await _reference_candidates(
        session, src, cross_board=cross_board
    )
    for cand in ref_cands:
        if cand.id == src.id:
            continue
        acc = _accumulate(acc_map, cand)
        acc.reference = True
        acc.ref_outbound = acc.ref_outbound or cand.id in outbound_ids
        acc.ref_inbound = acc.ref_inbound or cand.id in inbound_ids

    # --- epic candidates ----------------------------------------------------
    for cand in await _epic_candidates(session, src, cross_board=cross_board):
        if cand.id == src.id:
            continue
        acc = _accumulate(acc_map, cand)
        acc.epic = True
        acc.epic_detail = _epic_detail(src, cand)

    # --- deterministic sort on the accumulators (score desc, updated_at desc,
    # key asc) BEFORE projecting to RelatedTicket — keeps the tiebreak fields
    # (updated_at) attached to their row.
    def _sort_key(acc: _Accumulator) -> tuple[float, float, str]:
        updated = acc.ticket.updated_at
        ts = updated.timestamp() if updated is not None else 0.0
        return (-acc.score(), -ts, acc.ticket.key)

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
