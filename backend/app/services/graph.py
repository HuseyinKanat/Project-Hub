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


def _ticket_node_id(ticket_id: UUID) -> str:
    return f"ticket:{ticket_id}"


def _tag_node_id(tag_id: UUID) -> str:
    return f"tag:{tag_id}"


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


async def build_graph(
    session: AsyncSession,
    actor: Actor,
    *,
    board: str | None = None,
    tag: str | None = None,
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

    The uniform edge invariant (emit an edge IFF both endpoints survived into the
    node set) makes filter composition correct by construction — applied as a
    final pass so no per-filter edge bookkeeping is needed.
    """
    await _require_tag_read(session, actor)

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
    # excluded).
    if focus_tag is not None:
        # 1-hop subgraph around the focus tag: the focus + its link-neighbors,
        # restricted to tickets attached to the focus tag (then board-narrowed
        # below if ?board also given).
        neighbor_tag_ids: set[UUID] = {focus_tag.id}
        for link in links:
            if link.source_tag_id == focus_tag.id:
                neighbor_tag_ids.add(link.target_tag_id)
            elif link.target_tag_id == focus_tag.id:
                neighbor_tag_ids.add(link.source_tag_id)
        # Tickets restricted to those attached to the focus tag.
        tickets = [
            t
            for t in tickets
            if any(j.concept_tag_id == focus_tag.id for j in t.concept_tag_links)
        ]
    else:
        neighbor_tag_ids = set()

    ticket_ids: set[UUID] = {t.id for t in tickets}

    # Tag node set:
    if focus_tag is not None:
        included_tag_ids = neighbor_tag_ids
    elif board_obj is not None:
        # Board neighborhood: tags attached to an included ticket.
        included_tag_ids = {
            j.concept_tag_id for t in tickets for j in t.concept_tag_links
        }
    else:
        # Unfiltered: every tag (orphans included as standalone nodes).
        included_tag_ids = set(tag_map.keys())

    # ----- Assemble nodes ---------------------------------------------------
    nodes: list[GraphNode] = [_ticket_node(t) for t in tickets]
    nodes.extend(
        _tag_node(tag_map[tid]) for tid in included_tag_ids if tid in tag_map
    )

    node_ids: set[str] = {n.id for n in nodes}

    # ----- Assemble edges (pre-invariant; final filter prunes danglers) -----
    edges: list[GraphEdge] = []

    # has_tag — from the junctions already eager-loaded on each ticket.
    for t in tickets:
        for junction in t.concept_tag_links:
            edges.append(
                GraphEdge(
                    id=f"has_tag:{junction.ticket_id}:{junction.concept_tag_id}",
                    source=_ticket_node_id(junction.ticket_id),
                    target=_tag_node_id(junction.concept_tag_id),
                    type="has_tag",
                )
            )

    # tag_link — directed source→target, relation carried, reverse NOT deduped.
    for link in links:
        edges.append(
            GraphEdge(
                id=f"tag_link:{link.id}",
                source=_tag_node_id(link.source_tag_id),
                target=_tag_node_id(link.target_tag_id),
                type="tag_link",
                relation=link.relation,
            )
        )

    # epic — parent→child, emitted only when BOTH ticket nodes are present.
    for t in tickets:
        if t.epic_id is not None and t.epic_id in ticket_ids:
            edges.append(
                GraphEdge(
                    id=f"epic:{t.epic_id}:{t.id}",
                    source=_ticket_node_id(t.epic_id),
                    target=_ticket_node_id(t.id),
                    type="epic",
                )
            )

    # ----- Uniform edge invariant: keep an edge IFF both endpoints survived --
    # Single pass makes every filter mode (board / tag / intersection / orphan
    # exclusion / cross-board dangling-epic) correct by construction.
    edges = [e for e in edges if e.source in node_ids and e.target in node_ids]

    return nodes, edges
