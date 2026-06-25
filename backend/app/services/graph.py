"""Cross-board concept-graph assembly (PH-274, epic PH-271 child 3/7).

Builds the bipartite topology that feeds the Obsidian-style ``/space`` view
(PH-277). Read-only over the FROZEN PH-272 tables; no migration. Reuses the
PH-273 global ``tag.read`` gate (the graph exposes cross-board ticket keys/
titles/states, so it is a GLOBAL read).

Node/edge shape is the STABLE contract PH-277 consumes (``schemas.GraphNode`` /
``GraphEdge``): node ids are TYPE-PREFIXED (``ticket:<uuid>`` / ``tag:<uuid>``)
so a ticket UUID and a tag UUID never collide; edges reference those prefixed
ids verbatim.

N+1 discipline (AC6): three bounded one-shot queries with selectinload-only
eager loading (NO per-node lazy access). ``selectinload`` issues a FIXED number
of follow-up SELECTs regardless of row count → the total statement count is
constant across 1 vs 50 tickets. Every relationship hop walked during assembly
(``ticket.concept_tag_links`` → ``.concept_tag``, ``ticket.board``,
``link.source_tag`` / ``.target_tag``) is eager-loaded — never a lazy access
outside the loaded set (PH-272 MissingGreenlet rule). The full
``_ticket_load_options`` is deliberately NOT reused (it over-fetches reporter/
assignee/workflow the graph never serializes); a graph-local minimal options
tuple is defined instead.
"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Actor,
    Board,
    ConceptTag,
    ConceptTagLink,
    Ticket,
    TicketConceptTag,
)
from app.schemas import GraphEdge, GraphNode
from app.services.boards import get_board
from app.services.concept_tags import _require_tag_read, get_concept_tag

# Graph-local minimal eager-load options. Mirrors the selectinload discipline of
# services/concept_tags but loads ONLY the hops the graph walks: junction →
# concept_tag (has_tag edges + tag-node identity) and board (board.key on a
# ticket node). Intentionally NOT _ticket_load_options() (over-fetches).
_GRAPH_TICKET_OPTIONS = (
    selectinload(Ticket.concept_tag_links).selectinload(TicketConceptTag.concept_tag),
    selectinload(Ticket.board),
)

# PH-279: candidate ticket-key extractor for `reference` edges. Word-boundary +
# 1-6 uppercase letters + "-" + digits (matches "<BOARD_KEY>-<n>", core.py:229).
# This is ONLY a candidate filter — the authoritative gate is "does the matched
# string equal a real, non-deleted Ticket.key?" (resolved in _resolve_keys), so
# false-positives (COVID-19, UTF-8, SHA-256) that don't match a real key are
# dropped for free. Module-level compile → no per-call regex build.
_TICKET_KEY_RE = re.compile(r"\b[A-Z]{1,6}-\d+\b")


def _ticket_node_id(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}"


def _tag_node_id(tag_id: UUID) -> str:
    return f"tag:{tag_id}"


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


def _tag_node(tag: ConceptTag) -> GraphNode:
    return GraphNode(
        id=_tag_node_id(tag.id),
        type="tag",
        label=tag.name,
        slug=tag.slug,
        color=tag.color,
    )


def _focus_neighbor_tag_ids(
    focus_tag: ConceptTag, links: list[ConceptTagLink]
) -> set[UUID]:
    """1-hop tag neighborhood around ``focus_tag`` (focus + directed link peers)."""
    neighbor_tag_ids: set[UUID] = {focus_tag.id}
    for link in links:
        if link.source_tag_id == focus_tag.id:
            neighbor_tag_ids.add(link.target_tag_id)
        elif link.target_tag_id == focus_tag.id:
            neighbor_tag_ids.add(link.source_tag_id)
    return neighbor_tag_ids


def _resolve_tag_node_set(
    tickets: list[Ticket],
    *,
    focus_tag: ConceptTag | None,
    neighbor_tag_ids: set[UUID],
    board_obj: Board | None,
    all_tag_ids: set[UUID],
) -> set[UUID]:
    """Pick the tag-node set for the current filter mode (orphan policy lives here).

    - ?tag → the 1-hop neighborhood (focus + link peers).
    - ?board → tags attached to an included ticket (board neighborhood; orphans out).
    - unfiltered → every tag (orphans included as standalone nodes).
    """
    if focus_tag is not None:
        return neighbor_tag_ids
    if board_obj is not None:
        return {j.concept_tag_id for t in tickets for j in t.concept_tag_links}
    return all_tag_ids


def _has_tag_edges(tickets: list[Ticket]) -> list[GraphEdge]:
    """ticket↔tag edges from the junctions already eager-loaded on each ticket."""
    return [
        GraphEdge(
            id=f"has_tag:{junction.ticket_id}:{junction.concept_tag_id}",
            source=_ticket_node_id(junction.ticket_id),
            target=_tag_node_id(junction.concept_tag_id),
            type="has_tag",
            context="has-tag",
        )
        for t in tickets
        for junction in t.concept_tag_links
    ]


def _tag_link_edges(links: list[ConceptTagLink]) -> list[GraphEdge]:
    """tag↔tag edges, directed source→target, relation carried, reverse NOT deduped."""
    return [
        GraphEdge(
            id=f"tag_link:{link.id}",
            source=_tag_node_id(link.source_tag_id),
            target=_tag_node_id(link.target_tag_id),
            type="tag_link",
            relation=link.relation,
            context=link.relation,  # mirror; `relation` stays populated (frozen)
        )
        for link in links
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
    """Q4: ONE batched ``WHERE key IN (...)`` → ``{key: (ticket_id, board_id)}``.

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


async def _shared_tag_reach(
    session: AsyncSession, included_tag_ids: set[UUID], board_id: UUID
) -> dict[UUID, set[UUID]]:
    """Q5: foreign reach via shared tags → ``{tag_id: {foreign_board_id, ...}}``.

    ONE join over ``TicketConceptTag``→``Ticket``, restricted to in-scope tags
    attached to a NON-deleted ticket on a DIFFERENT board. Foreign ticket rows
    are never materialised — only ``(tag_id, board_id)`` pairs. Empty-guard keeps
    the statement count stable when there are no in-scope tags.
    """
    if not included_tag_ids:
        return {}
    rows = (
        await session.execute(
            select(TicketConceptTag.concept_tag_id, Ticket.board_id)
            .join(Ticket, Ticket.id == TicketConceptTag.ticket_id)
            .where(
                TicketConceptTag.concept_tag_id.in_(included_tag_ids),
                Ticket.board_id != board_id,
                Ticket.deleted_at.is_(None),
            )
        )
    ).all()
    reach: dict[UUID, set[UUID]] = {}
    for tag_id, foreign_board_id in rows:
        reach.setdefault(tag_id, set()).add(foreign_board_id)
    return reach


def _collapse_pairs_from_tags(
    tickets: list[Ticket], tag_reach: dict[UUID, set[UUID]]
) -> set[tuple[str, UUID]]:
    """(in_scope_ticket_node, foreign_board_id) pairs from shared-tag reach."""
    pairs: set[tuple[str, UUID]] = set()
    for ticket in tickets:
        src_id = _ticket_node_id(ticket.id)
        for junction in ticket.concept_tag_links:
            for foreign_board_id in tag_reach.get(junction.concept_tag_id, set()):
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
    included_tag_ids: set[UUID],
    key_map: dict[str, tuple[UUID, UUID]],
    board_obj: Board,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Collapse every cross-board connection into a synthetic ``board:<key>`` node.

    Reach sources (all aggregated to ONE ``board`` edge per (in_scope_node,
    foreign_board) pair): shared tag (Q5), foreign ticket reference (``key_map``).
    Q6 loads board metadata for the involved foreign boards in one batch. Foreign
    tickets/tags are NEVER expanded into the node set.
    """
    tag_reach = await _shared_tag_reach(session, included_tag_ids, board_obj.id)
    pairs = _collapse_pairs_from_tags(tickets, tag_reach)
    pairs |= _collapse_pairs_from_refs(tickets, key_map, board_obj.id)
    if not pairs:
        return [], []

    foreign_board_ids = {board_id for _, board_id in pairs}
    boards = (
        await session.execute(  # Q6
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


async def build_graph(
    session: AsyncSession,
    actor: Actor,
    *,
    board: str | None = None,
    tag: str | None = None,
    scope: Literal["global", "board"] = "global",
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Assemble the cross-board concept graph (nodes, edges).

    Permission: global ``tag.read`` (PH-273 gate, reused). Filters (both
    optional, AND/intersection when combined):

    - ``board`` (board KEY, case-insensitive) — restrict ticket nodes to that
      board; tag nodes are the board's neighborhood (attached to an included
      ticket); orphan/global tags excluded.
    - ``tag`` (slug) — 1-hop subgraph around the focus tag: the tag + its
      link-neighbors + tickets attached to the focus tag.

    Unknown board key / tag slug → 404 (via ``get_board`` / ``get_concept_tag``).

    ``scope`` (PH-279, default ``"global"`` — least-breaking):

    - ``"global"`` — PH-274 behavior UNCHANGED. The uniform drop-dangling
      invariant prunes cross-board edges whose other endpoint fell outside the
      node set. Byte-compatible with the response PH-277 base consumes.
    - ``"board"`` — requires ``board``. The in-scope board's tickets+tags stay in
      full detail; every connection leaving the board COLLAPSES into a synthetic
      ``board:<foreignKey>`` node + one aggregated ``board`` edge per
      (in_scope_node, foreign_board). ``scope="board"`` without ``board`` →
      ``ValueError`` (mapped to 422 at the API boundary).

    NEW ``reference`` edges (both scopes): ticket descriptions are scanned for
    bare ticket-key mentions; each mention resolving to a real, non-deleted
    ticket emits a ``reference`` edge (self-skip, epic-wins ordered-pair dedupe,
    repeated-mention dedupe). In ``scope="board"`` a foreign-board reference
    collapses into the board-node.

    Every edge carries a human-labelable ``context`` (additive, PH-279).

    The uniform edge invariant (emit an edge IFF both endpoints survived into the
    node set) makes filter composition correct by construction — applied as a
    final pass so no per-filter edge bookkeeping is needed.
    """
    await _require_tag_read(session, actor)

    if scope not in ("global", "board"):
        raise ValueError(f"invalid scope: {scope!r}")
    if scope == "board" and board is None:
        raise ValueError("scope=board requires board")

    # Resolve filters first (404 early on unknown key/slug).
    board_obj: Board | None = None
    if board is not None:
        board_obj = await get_board(session, board)
    focus_tag: ConceptTag | None = None
    if tag is not None:
        focus_tag = await get_concept_tag(session, tag)

    # --- Q1: tickets (one-shot, selectinload junction→tag + board) ----------
    ticket_stmt = select(Ticket).where(Ticket.deleted_at.is_(None)).options(
        *_GRAPH_TICKET_OPTIONS
    )
    if board_obj is not None:
        ticket_stmt = ticket_stmt.where(Ticket.board_id == board_obj.id)
    tickets = list((await session.execute(ticket_stmt)).scalars())

    # --- Q2: all directed tag↔tag links (one-shot, both endpoints eager) -----
    links = list(
        (
            await session.execute(
                select(ConceptTagLink)
                .options(
                    selectinload(ConceptTagLink.source_tag),
                    selectinload(ConceptTagLink.target_tag),
                )
                .order_by(ConceptTagLink.id)
            )
        ).scalars()
    )

    # --- Q3: full tag map (one-shot) — node identity + orphan policy ---------
    all_tags = list((await session.execute(select(ConceptTag))).scalars())
    tag_map: dict[UUID, ConceptTag] = {t.id: t for t in all_tags}

    # ----- Determine which tags belong in the node set ----------------------
    # In the unfiltered graph the tag node set is ALL tags (orphans included as
    # standalone nodes — a /space concept map shows unconnected concepts). Under
    # ?board or ?tag the set is narrowed to the relevant neighborhood (orphans
    # excluded). The ?tag mode also restricts tickets to those attached to the
    # focus tag (then board-narrowed already via Q1's board_id filter).
    neighbor_tag_ids: set[UUID] = set()
    if focus_tag is not None:
        neighbor_tag_ids = _focus_neighbor_tag_ids(focus_tag, links)
        tickets = [
            t
            for t in tickets
            if any(j.concept_tag_id == focus_tag.id for j in t.concept_tag_links)
        ]

    ticket_ids: set[UUID] = {t.id for t in tickets}

    included_tag_ids = _resolve_tag_node_set(
        tickets,
        focus_tag=focus_tag,
        neighbor_tag_ids=neighbor_tag_ids,
        board_obj=board_obj,
        all_tag_ids=set(tag_map.keys()),
    )

    # --- Q4: resolve reference-key mentions (ONE batch, empty-guarded) -------
    candidate_keys = {k for t in tickets for k in _extract_keys(t.description)}
    key_map = await _resolve_keys(session, candidate_keys)

    # ----- Assemble nodes ---------------------------------------------------
    nodes: list[GraphNode] = [_ticket_node(t) for t in tickets]
    nodes.extend(
        _tag_node(tag_map[tid]) for tid in included_tag_ids if tid in tag_map
    )

    # ----- Assemble edges (pre-invariant; final filter prunes danglers) -----
    epic_edges = _epic_edges(tickets, ticket_ids)
    edges: list[GraphEdge] = [
        *_has_tag_edges(tickets),
        *_tag_link_edges(links),
        *epic_edges,
        *_reference_edges(tickets, key_map, _epic_pairs(epic_edges)),
    ]

    if scope == "board":
        assert board_obj is not None  # guaranteed by the scope=board guard above
        board_nodes, board_edges = await _board_collapse(
            session, tickets, included_tag_ids, key_map, board_obj
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
    # Single pass makes every filter mode (board / tag / intersection / orphan
    # exclusion / cross-board dangling-epic) correct by construction.
    node_ids = {n.id for n in nodes}
    edges = [e for e in edges if e.source in node_ids and e.target in node_ids]

    return nodes, edges
