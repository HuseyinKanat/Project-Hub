"""Cross-board concept-graph assembly (PH-274 / PH-279; re-pointed to labels PH-281).

Builds the bipartite topology that feeds the Obsidian-style ``/space`` view
(PH-277/PH-280). Read-only; no migration.

PH-281 PIVOT (epic PH-271): the second bipartite axis moved from the separate
ConceptTag entity to the inline ``Ticket.labels`` free-text ARRAY. ``label``
nodes (``id="label:<rawValue>"``) replace ``tag`` nodes; ``has_label`` edges
replace ``has_tag``; the ``tag_link`` label↔label edge is GONE (labels have no
relation registry). Read-auth moved from the removed global ``tag.read`` to a
global ``ticket.read`` gate (the graph exposes cross-board ticket key/title/
state, so it is a cross-board ticket read).

Node/edge shape is the contract the frontend consumes (``schemas.GraphNode`` /
``GraphEdge``): node ids are TYPE-PREFIXED (``ticket:<uuid>`` /
``label:<rawValue>``) so a ticket UUID and a label value never collide. The
label value is kept RAW after the ``label:`` prefix — injective by construction,
round-trips losslessly; consumers do exact-string match, never split on ``:``.

N+1 discipline (AC6): labels are read from the inline ARRAY column already
materialized by the Q1 ticket SELECT — NO junction query, NO ``selectinload``
for labels. The total statement count is constant across 1 vs 50 tickets AND
strictly LOWER than the PH-279 count (which had the ConceptTag link + tag-map
queries). The only remaining follow-up query is the empty-guarded reference-key
batch (``_resolve_keys``) + (board-scope only) the shared-label reach + board
meta. ``selectinload(Ticket.board)`` issues a fixed extra SELECT for board.key.
"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Actor, Board, BoardMembership, Ticket
from app.schemas import GraphEdge, GraphNode
from app.services.boards import get_board
from app.services.labels import labels_reach_query

# Graph-local minimal eager-load options. PH-281: labels are inline on the Q1
# ticket SELECT — the only hop walked outside the loaded array is board.key on a
# ticket node, so board is the SOLE selectinload (was junction→tag + board).
_GRAPH_TICKET_OPTIONS = (selectinload(Ticket.board),)

# PH-279: candidate ticket-key extractor for `reference` edges. Word-boundary +
# 1-6 uppercase letters + "-" + digits (matches "<BOARD_KEY>-<n>", core.py:229).
# This is ONLY a candidate filter — the authoritative gate is "does the matched
# string equal a real, non-deleted Ticket.key?" (resolved in _resolve_keys), so
# false-positives (COVID-19, UTF-8, SHA-256) that don't match a real key are
# dropped for free. Module-level compile → no per-call regex build.
_TICKET_KEY_RE = re.compile(r"\b[A-Z]{1,6}-\d+\b")


def _ticket_node_id(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}"


def _label_node_id(value: str) -> str:
    return f"label:{value}"


def _board_node_id(board_key: str) -> str:
    return f"board:{board_key}"


def _ticket_node(ticket: Ticket) -> GraphNode:
    return GraphNode(
        id=_ticket_node_id(ticket.id),
        type="ticket",
        label=ticket.key,
        board=ticket.board.key,
        board_id=ticket.board_id,
        key=ticket.key,
        state=ticket.state,
        title=ticket.title,
    )


def _label_node(value: str) -> GraphNode:
    """Label node — raw value verbatim; no slug/color (frontend hashes the string)."""
    return GraphNode(id=_label_node_id(value), type="label", label=value)


def _scope_label_values(tickets: list[Ticket]) -> set[str]:
    """Distinct label strings across the in-scope tickets' inline ``labels`` arrays."""
    return {value for t in tickets for value in (t.labels or ())}


def _has_label_edges(tickets: list[Ticket]) -> list[GraphEdge]:
    """ticket↔label edges from each ticket's inline ``labels`` array (no junction).

    A per-ticket ``set`` guards a malformed duplicate value; deterministic id.
    """
    return [
        GraphEdge(
            id=f"has_label:{t.id}:{value}",
            source=_ticket_node_id(t.id),
            target=_label_node_id(value),
            type="has_label",
            context="has-label",
        )
        for t in tickets
        for value in set(t.labels or ())
    ]


def _epic_edges(tickets: list[Ticket], ticket_ids: set[UUID]) -> list[GraphEdge]:
    """ticket↔ticket epic edges, parent→child, only when the parent is present."""
    return [
        GraphEdge(
            id=f"epic:{t.epic_id}:{t.id}",
            source=_ticket_node_id(t.epic_id),
            target=_ticket_node_id(t.id),
            type="epic",
            context="epic",
        )
        for t in tickets
        if t.epic_id is not None and t.epic_id in ticket_ids
    ]


def _extract_keys(text: str) -> set[str]:
    """Candidate ticket keys mentioned in ``text`` (set → repeated mention dedupe)."""
    return set(_TICKET_KEY_RE.findall(text or ""))


async def _resolve_keys(
    session: AsyncSession, keys: set[str]
) -> dict[str, tuple[UUID, UUID]]:
    """Q: ONE batched ``WHERE key IN (...)`` → ``{key: (ticket_id, board_id)}``.

    Empty-candidate guard (LOAD-BEARING for the N+1 test): when ``keys`` is empty
    we skip the query entirely — issuing ``WHERE key IN ()`` would add a statement
    and break the constant-statement-count invariant. Only real, non-deleted
    tickets resolve; unknown / soft-deleted keys have no map entry → no edge.
    """
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(Ticket.key, Ticket.id, Ticket.board_id).where(
                Ticket.key.in_(keys), Ticket.deleted_at.is_(None)
            )
        )
    ).all()
    return {key: (tid, bid) for key, tid, bid in rows}


def _epic_pairs(epic_edges: list[GraphEdge]) -> set[tuple[str, str]]:
    """Ordered (source,target) node-id pairs already linked by an epic edge."""
    return {(e.source, e.target) for e in epic_edges}


def _reference_edges(
    tickets: list[Ticket],
    key_map: dict[str, tuple[UUID, UUID]],
    epic_pairs: set[tuple[str, str]],
) -> list[GraphEdge]:
    """ticket→ticket ``reference`` edges from description key-mentions.

    Rules (AC): skip self-reference; skip when the SAME ordered pair already has
    an epic edge (epic wins — child→parent reference, a different ordered pair,
    is kept); repeated mentions collapse via ``_extract_keys`` returning a set.
    """
    edges: list[GraphEdge] = []
    for ticket in tickets:
        src_id = _ticket_node_id(ticket.id)
        for key in _extract_keys(ticket.description):
            resolved = key_map.get(key)
            if resolved is None or resolved[0] == ticket.id:
                continue  # unresolved / self-reference
            dst_id = _ticket_node_id(resolved[0])
            if (src_id, dst_id) in epic_pairs:
                continue  # epic wins for this ordered pair
            edges.append(
                GraphEdge(
                    id=f"reference:{ticket.id}:{resolved[0]}",
                    source=src_id,
                    target=dst_id,
                    type="reference",
                    context="ticket-reference",
                )
            )
    return edges


async def _shared_label_reach(
    session: AsyncSession, included_labels: set[str], board_id: UUID
) -> dict[str, set[UUID]]:
    """Q: foreign reach via shared labels → ``{label_value: {foreign_board_id, ...}}``.

    PH-281: ONE dialect-aware query (PG ``unnest`` / sqlite ``json_each``) over
    the inline ``Ticket.labels`` ARRAY, restricted to in-scope label values
    present on a NON-deleted ticket on a DIFFERENT board. Foreign ticket rows are
    never materialised — only ``(label_value, board_id)`` pairs. Empty-guard keeps
    the statement count stable when there are no in-scope labels.
    """
    if not included_labels:
        return {}
    dialect = session.bind.dialect if session.bind is not None else "sqlite"
    rows = (
        await session.execute(
            labels_reach_query(dialect, included_labels, board_id)
        )
    ).all()
    reach: dict[str, set[UUID]] = {}
    for value, foreign_board_id in rows:
        reach.setdefault(value, set()).add(foreign_board_id)
    return reach


def _collapse_pairs_from_labels(
    tickets: list[Ticket], label_reach: dict[str, set[UUID]]
) -> set[tuple[str, UUID]]:
    """(in_scope_ticket_node, foreign_board_id) pairs from shared-label reach."""
    pairs: set[tuple[str, UUID]] = set()
    for ticket in tickets:
        src_id = _ticket_node_id(ticket.id)
        for value in set(ticket.labels or ()):
            for foreign_board_id in label_reach.get(value, set()):
                pairs.add((src_id, foreign_board_id))
    return pairs


def _collapse_pairs_from_refs(
    tickets: list[Ticket],
    key_map: dict[str, tuple[UUID, UUID]],
    board_id: UUID,
) -> set[tuple[str, UUID]]:
    """(in_scope_ticket_node, foreign_board_id) pairs from foreign references."""
    pairs: set[tuple[str, UUID]] = set()
    for ticket in tickets:
        src_id = _ticket_node_id(ticket.id)
        for key in _extract_keys(ticket.description):
            resolved = key_map.get(key)
            if resolved is None or resolved[0] == ticket.id:
                continue
            if resolved[1] != board_id:
                pairs.add((src_id, resolved[1]))
    return pairs


async def _board_collapse(
    session: AsyncSession,
    tickets: list[Ticket],
    included_labels: set[str],
    key_map: dict[str, tuple[UUID, UUID]],
    board_obj: Board,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Collapse every cross-board connection into a synthetic ``board:<key>`` node.

    Reach sources (all aggregated to ONE ``board`` edge per (in_scope_node,
    foreign_board) pair): shared LABEL (PH-281), foreign ticket reference
    (``key_map``). One query loads board metadata for the involved foreign boards.
    Foreign tickets/labels are NEVER expanded into the node set.
    """
    label_reach = await _shared_label_reach(session, included_labels, board_obj.id)
    pairs = _collapse_pairs_from_labels(tickets, label_reach)
    pairs |= _collapse_pairs_from_refs(tickets, key_map, board_obj.id)
    if not pairs:
        return [], []

    foreign_board_ids = {board_id for _, board_id in pairs}
    boards = (
        await session.execute(
            select(Board.id, Board.key, Board.name).where(
                Board.id.in_(foreign_board_ids)
            )
        )
    ).all()
    board_meta = {bid: (key, name) for bid, key, name in boards}

    nodes = [
        GraphNode(
            id=_board_node_id(board_meta[bid][0]),
            type="board",
            label=board_meta[bid][1] or board_meta[bid][0],
            board=board_meta[bid][0],
            board_id=bid,
        )
        for bid in foreign_board_ids
        if bid in board_meta
    ]
    edges = [
        GraphEdge(
            id=f"board:{src_id}:{board_meta[bid][0]}",
            source=src_id,
            target=_board_node_id(board_meta[bid][0]),
            type="board",
            context="cross-board",
        )
        for src_id, bid in pairs
        if bid in board_meta
    ]
    return nodes, edges


async def _require_ticket_read(session: AsyncSession, actor: Actor) -> None:
    """Global read gate (PH-281): the actor must hold ``ticket.read`` (or ``*``)
    under the role of AT LEAST ONE of their board memberships.

    The graph exposes cross-board ticket key/title/state, so it is a cross-board
    ticket read — replacing the removed global ``tag.read`` gate. Loads
    ``(role, board)`` pairs with ``board.roles`` eager-loaded (``current_actor``
    only selectinloads ``Actor.memberships``, NOT the board), keeping the gate
    self-contained + async-safe.
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


async def build_graph(
    session: AsyncSession,
    actor: Actor,
    *,
    board: str | None = None,
    label: str | None = None,
    scope: Literal["global", "board"] = "global",
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Assemble the cross-board concept graph (nodes, edges).

    Permission: global ``ticket.read`` (PH-281, replaces ``tag.read``). Filters
    (both optional, AND/intersection when combined):

    - ``board`` (board KEY, case-insensitive) — restrict ticket nodes to that
      board; label nodes are the board's neighborhood (carried by an included
      ticket). Unknown board key → 404 (via ``get_board``).
    - ``label`` (raw value) — 1-hop subgraph: tickets carrying that label + the
      label node + co-occurring labels on those tickets. An UNKNOWN label value
      yields an EMPTY ticket set (NOT 404 — labels have no registry to 404
      against). Replaces the PH-274 ``?tag=<slug>`` filter.

    ``scope`` (PH-279, default ``"global"`` — least-breaking):

    - ``"global"`` — the uniform drop-dangling invariant prunes cross-board edges
      whose other endpoint fell outside the node set.
    - ``"board"`` — requires ``board``. The in-scope board stays in full detail;
      every connection leaving the board COLLAPSES into a synthetic
      ``board:<foreignKey>`` node + one aggregated ``board`` edge per
      (in_scope_node, foreign_board). Cross-board reach is detected via SHARED
      LABEL STRINGS (PH-281) + foreign ticket-reference. ``scope="board"`` without
      ``board`` → ``ValueError`` (mapped to 422 at the API boundary).

    Every edge carries a human-labelable ``context``. The uniform edge invariant
    (emit an edge IFF both endpoints survived into the node set) makes filter
    composition correct by construction — applied as a final pass.
    """
    await _require_ticket_read(session, actor)

    if scope not in ("global", "board"):
        raise ValueError(f"invalid scope: {scope!r}")
    if scope == "board" and board is None:
        raise ValueError("scope=board requires board")

    # Resolve the board filter first (404 early on unknown key).
    board_obj: Board | None = None
    if board is not None:
        board_obj = await get_board(session, board)

    # --- Q1: tickets (one-shot, selectinload board; labels read inline) ------
    ticket_stmt = (
        select(Ticket)
        .where(Ticket.deleted_at.is_(None))
        .options(*_GRAPH_TICKET_OPTIONS)
    )
    if board_obj is not None:
        ticket_stmt = ticket_stmt.where(Ticket.board_id == board_obj.id)
    tickets = list((await session.execute(ticket_stmt)).scalars())

    # ----- ?label filter: restrict to tickets carrying that exact label value -
    # Unknown value → empty ticket set (NO 404 — labels have no registry).
    if label is not None:
        tickets = [t for t in tickets if label in (t.labels or ())]

    ticket_ids: set[UUID] = {t.id for t in tickets}

    # Label node set = distinct label values across the in-scope tickets (label
    # filter mode keeps co-occurring labels naturally — they share a ticket).
    included_labels = _scope_label_values(tickets)

    # --- Q: resolve reference-key mentions (ONE batch, empty-guarded) --------
    candidate_keys = {k for t in tickets for k in _extract_keys(t.description)}
    key_map = await _resolve_keys(session, candidate_keys)

    # ----- Assemble nodes ---------------------------------------------------
    nodes: list[GraphNode] = [_ticket_node(t) for t in tickets]
    nodes.extend(_label_node(value) for value in included_labels)

    # ----- Assemble edges (pre-invariant; final filter prunes danglers) -----
    epic_edges = _epic_edges(tickets, ticket_ids)
    edges: list[GraphEdge] = [
        *_has_label_edges(tickets),
        *epic_edges,
        *_reference_edges(tickets, key_map, _epic_pairs(epic_edges)),
    ]

    if scope == "board":
        assert board_obj is not None  # guaranteed by the scope=board guard above
        board_nodes, board_edges = await _board_collapse(
            session, tickets, included_labels, key_map, board_obj
        )
        nodes.extend(board_nodes)
        node_ids = {n.id for n in nodes}
        # Keep in-scope edges (both endpoints survived) + the collapsed board
        # edges (foreign endpoints are NOT in node_ids, so they bypass the drop).
        edges = [
            e for e in edges if e.source in node_ids and e.target in node_ids
        ]
        edges.extend(board_edges)
        return nodes, edges

    # ----- Uniform edge invariant (scope=global): keep IFF both endpoints in --
    node_ids = {n.id for n in nodes}
    edges = [e for e in edges if e.source in node_ids and e.target in node_ids]

    return nodes, edges
