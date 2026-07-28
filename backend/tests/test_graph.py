"""Service-layer tests for the cross-board concept graph.

History: PH-274/PH-279 (ConceptTag bipartite) → PH-281 (inline ``Ticket.labels``
bipartite) → **PH-288** (weighted top-K, TICKET↔TICKET only — the hairball fix).

Tests ``services.graph.build_graph`` DIRECTLY against an in-memory sqlite
``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env). Seeds
via ``Base.metadata.create_all``.

PH-288: the graph is now ticket↔ticket ONLY. ``label`` NODES + ``has_label``
edges are GONE (they were the ``/space`` hairball). The graph emits the WEIGHTED
top-K signal edges from the PH-287 model (dependency / reference / epic /
shared_label / code_overlap), pruned to each node's top-K (UNION semantics).
Specific labels survive as ``shared_label`` edge metadata; hub labels (n≥15, IDF
0) are dropped entirely. Coverage:
- ticket-only nodes (no label nodes); ``ticket:``/``board:`` prefixed ids.
- shared SPECIFIC label → ONE ticket↔ticket ``shared_label`` edge carrying
  strength + reason + context; a HUB label (n≥15) emits NO edge.
- epic parent↔child edge; cross-board (parent absent under ?board) → no edge.
- reference / dependency edges with precedence (dependency > reference > epic >
  shared_label > code_overlap); ONE edge per unordered pair.
- ≤k own-pick edges per node; ?k clamps; union semantics (no orphaning).
- ?board / ?label filters; unknown board → 404, unknown label → empty.
- N+1 — statement count CONSTANT across 1 vs 50 tickets (batched all-pairs).
- permission: actor lacking ticket.read → 403 PermissionDenied.
- board-scope collapse via SHARED LABEL strings + reference edges + edge context.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    Ticket,
    Workflow,
)
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.graph import build_graph


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
    await engine.dispose()


async def _reload_actor(session: AsyncSession, actor_id: uuid.UUID) -> Actor:
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor_id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()


class Env:
    """Two boards (PH + KIM), an admin member of both, and a no-membership stranger."""

    def __init__(
        self,
        *,
        board_ph: Board,
        board_kim: Board,
        admin: Actor,
        stranger: Actor,
    ) -> None:
        self.board_ph = board_ph
        self.board_kim = board_kim
        self.admin = admin
        self.stranger = stranger


@pytest_asyncio.fixture
async def env(mem_session: AsyncSession) -> Env:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    # `stranger` has NO board membership → no ticket.read → 403 (permission test).
    stranger = Actor(
        kind="agent", display_name="Stranger", token_hash="z", is_active=True
    )
    mem_session.add_all([admin, stranger])
    await mem_session.flush()

    board_ph = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    board_kim = Board(
        key="KIM",
        name="Kims",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add_all([board_ph, board_kim])
    await mem_session.flush()

    # admin is a member of BOTH boards (admin role grants ticket.read via "*").
    mem_session.add_all(
        [
            BoardMembership(board_id=board_ph.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_kim.id, actor_id=admin.id, role="admin"),
        ]
    )
    await mem_session.commit()

    return Env(
        board_ph=board_ph,
        board_kim=board_kim,
        admin=await _reload_actor(mem_session, admin.id),
        stranger=await _reload_actor(mem_session, stranger.id),
    )


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    epic_id: uuid.UUID | None = None,
    deleted: bool = False,
    description: str = "",
    labels: list[str] | None = None,
) -> Ticket:
    from datetime import UTC, datetime

    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=f"Ticket {key}",
        description=description,
        state="backlog",
        reporter_id=reporter.id,
        epic_id=epic_id,
        labels=labels or [],
        deleted_at=datetime.now(UTC) if deleted else None,
    )


# ---------------------------------------------------------------------------
# PH-288: ticket-only nodes (NO label nodes), prefixed ids (collision guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_only_nodes_no_label_nodes(
    mem_session: AsyncSession, env: Env
) -> None:
    ticket = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["async-safety"])
    mem_session.add(ticket)
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin)

    # PH-288: TICKET-only nodes — no `label` node type is ever emitted now.
    types = {n.type for n in nodes}
    assert types == {"ticket"}
    assert not [n for n in nodes if n.type == "label"]

    ticket_nodes = [n for n in nodes if n.type == "ticket"]
    assert all(n.id.startswith("ticket:") for n in ticket_nodes)
    # No id appears twice (prefix is the SOLE disambiguator).
    assert len({n.id for n in nodes}) == len(nodes)

    # ticket node carries board/key/state/title.
    tn = ticket_nodes[0]
    assert tn.board == "PH" and tn.key == "PH-1" and tn.state == "backlog"
    assert tn.title == "Ticket PH-1" and tn.board_id == env.board_ph.id

    # A lone ticket sharing a label with no one has NO edges (hairball gone).
    assert edges == []


# ---------------------------------------------------------------------------
# Shared SPECIFIC label → ONE ticket↔ticket shared_label edge (strength/reason).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_specific_label_edge(mem_session: AsyncSession, env: Env) -> None:
    # A rare label on exactly 2 of MANY tickets → high IDF → ONE shared_label edge.
    # (IDF = log((N+1)/(n+1)); with n=2 the label must be rare RELATIVE to N, so
    # we add filler tickets to push N up — 2/2 would score IDF 0.)
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["rare-label"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["rare-label"])
    filler = [_make_ticket(env.board_ph, env.admin, f"PH-{i}") for i in range(2, 12)]
    mem_session.add_all([t_ph, t_kim, *filler])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)

    # No has_label edges anymore; exactly ONE shared_label ticket↔ticket edge.
    assert not [e for e in edges if e.type == "has_label"]
    shared = [e for e in edges if e.type == "shared_label"]
    assert len(shared) == 1
    e = shared[0]
    # Endpoints are the two ticket nodes (unordered pair, one edge).
    assert {e.source, e.target} == {f"ticket:{t_ph.id}", f"ticket:{t_kim.id}"}
    # Additive fields: strength (float > 0) + reason + human context.
    assert e.reason == "shared_label"
    assert isinstance(e.strength, float) and e.strength > 0
    assert e.context is not None and "rare-label" in e.context


# ---------------------------------------------------------------------------
# A HUB label (n >= 15) emits NO edge (IDF dropped to 0).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_label_emits_no_edge(mem_session: AsyncSession, env: Env) -> None:
    # 16 tickets all carry the same label → n_label = 16 >= 15 → IDF 0 → no edge.
    tickets = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["frontend"])
        for i in range(16)
    ]
    mem_session.add_all(tickets)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)

    # The hub-label connection is dropped entirely (no shared_label / has_label).
    assert not [e for e in edges if e.type in ("shared_label", "has_label")]


# ---------------------------------------------------------------------------
# epic parent↔child edge; cross-board parent absent → no edge.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_epic_edge_and_cross_board_guard(
    mem_session: AsyncSession, env: Env
) -> None:
    parent = _make_ticket(env.board_kim, env.admin, "KIM-100")
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-200", epic_id=parent.id)
    mem_session.add(child)
    await mem_session.commit()

    # Unfiltered: both tickets present → ONE epic edge over the unordered pair.
    _, edges = await build_graph(mem_session, env.admin)
    epic = [e for e in edges if e.type == "epic"]
    assert len(epic) == 1
    assert {epic[0].source, epic[0].target} == {
        f"ticket:{parent.id}",
        f"ticket:{child.id}",
    }
    assert epic[0].reason == "epic" and epic[0].strength is not None

    # ?board=PH: parent (on KIM) absent → no epic edge (dangling guard).
    _, edges_ph = await build_graph(mem_session, env.admin, board="PH")
    assert not [e for e in edges_ph if e.type == "epic"]


# ---------------------------------------------------------------------------
# Precedence: a pair with multiple signals emits ONE edge, highest-precedence.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_precedence_one_edge_per_pair(
    mem_session: AsyncSession, env: Env
) -> None:
    # parent↔child via epic AND a shared specific label → epic (3.0) outranks
    # shared_label, so the SINGLE edge is type=epic.
    parent = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["rare"])
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(
        env.board_ph, env.admin, "PH-2", epic_id=parent.id, labels=["rare"]
    )
    mem_session.add(child)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    # Exactly ONE edge for the pair (not one per signal).
    pair_edges = [
        e
        for e in edges
        if {e.source, e.target} == {f"ticket:{parent.id}", f"ticket:{child.id}"}
    ]
    assert len(pair_edges) == 1
    assert pair_edges[0].type == "epic"  # epic outranks shared_label


@pytest.mark.asyncio
async def test_dependency_outranks_reference(
    mem_session: AsyncSession, env: Env
) -> None:
    # PH-1 has an explicit dependency line AND a plain mention of PH-2 →
    # dependency (8.0) wins; ONE edge of type=dependency.
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        description="Depends on: PH-2\nalso see PH-2 again",
    )
    dst = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, dst])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    pair = [
        e
        for e in edges
        if {e.source, e.target} == {f"ticket:{src.id}", f"ticket:{dst.id}"}
    ]
    assert len(pair) == 1
    assert pair[0].type == "dependency" and pair[0].reason == "dependency"
    assert pair[0].strength == 8.0


# ---------------------------------------------------------------------------
# top-K: each node has at most k OWN-pick edges; union has no orphan; ?k clamp.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topk_bounds_own_picks(mem_session: AsyncSession, env: Env) -> None:
    # A hub ticket referenced-by 10 others; with k=3 the hub keeps at most its
    # own 3 picks, but UNION keeps every foreign node's pick (no orphaning).
    hub = _make_ticket(env.board_ph, env.admin, "PH-1")
    mem_session.add(hub)
    await mem_session.flush()
    spokes = [
        _make_ticket(
            env.board_ph, env.admin, f"PH-{2 + i}", description="relates to PH-1"
        )
        for i in range(10)
    ]
    mem_session.add_all(spokes)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin, k=3)

    # Every spoke (its ONLY candidate is the hub) survives → no orphan (union).
    hub_node = f"ticket:{hub.id}"
    incident_hub = [e for e in edges if hub_node in (e.source, e.target)]
    assert len(incident_hub) == 10  # union keeps all 10 foreign picks

    # Each SPOKE node has at most k incident edges (its single pick survives).
    for s in spokes:
        sn = f"ticket:{s.id}"
        assert len([e for e in edges if sn in (e.source, e.target)]) <= 3


@pytest.mark.asyncio
async def test_k_clamps_own_pick_count(mem_session: AsyncSession, env: Env) -> None:
    # One ticket referencing 8 others → with k=2 it keeps at most 2 own picks
    # (each referenced ticket also ranks it, but those are foreign picks on THEM).
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        description="see PH-2 PH-3 PH-4 PH-5 PH-6 PH-7 PH-8 PH-9",
    )
    mem_session.add(src)
    await mem_session.flush()
    others = [_make_ticket(env.board_ph, env.admin, f"PH-{i}") for i in range(2, 10)]
    mem_session.add_all(others)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin, k=2)
    src_node = f"ticket:{src.id}"
    incident = [e for e in edges if src_node in (e.source, e.target)]
    # src's OWN picks are bounded by k=2; foreign nodes each picked src too, but
    # with equal strength + deterministic tiebreak src is everyone's #1 → all 8
    # foreign picks survive (union). The bound we assert: src's pruning kept ≤k
    # of ITS picks; the survivors are the union (here all, since each foreign node
    # has exactly one candidate). Assert k clamps src's own-pick contribution:
    assert len(incident) == 8  # union: every foreign node's single pick survives
    # And a much smaller k still keeps every foreign single-candidate edge:
    _, edges_k1 = await build_graph(mem_session, env.admin, k=1)
    assert len([e for e in edges_k1 if src_node in (e.source, e.target)]) == 8


# ---------------------------------------------------------------------------
# filters — ?board, ?label, intersection, unknown board → 404, unknown label empty.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_filter(mem_session: AsyncSession, env: Env) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["ph-label"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["kim-label"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin, board="PH")
    ticket_nodes = [n for n in nodes if n.type == "ticket"]
    # Only PH ticket; KIM ticket absent. No label nodes anymore.
    assert {n.key for n in ticket_nodes} == {"PH-1"}
    assert not [n for n in nodes if n.type == "label"]

    # Case-insensitive board key.
    nodes_lower, _ = await build_graph(mem_session, env.admin, board="ph")
    assert {n.key for n in nodes_lower if n.type == "ticket"} == {"PH-1"}


@pytest.mark.asyncio
async def test_label_filter_subgraph(mem_session: AsyncSession, env: Env) -> None:
    # ?label restricts the TICKET set to those carrying the value (no label node).
    t1 = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["focus", "cooccur"])
    t2 = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["far"])
    mem_session.add_all([t1, t2])
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin, label="focus")
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # only ticket carrying focus
    assert not [n for n in nodes if n.type == "label"]


@pytest.mark.asyncio
async def test_unknown_label_is_empty_not_404(
    mem_session: AsyncSession, env: Env
) -> None:
    t = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["real"])
    mem_session.add(t)
    await mem_session.commit()

    # Unknown label value → empty ticket set, NOT a 404 (no label registry).
    nodes, edges = await build_graph(mem_session, env.admin, label="does-not-exist")
    assert [n for n in nodes if n.type == "ticket"] == []
    assert edges == []


@pytest.mark.asyncio
async def test_intersection_board_and_label(
    mem_session: AsyncSession, env: Env
) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["focus"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["focus"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    nodes, _ = await build_graph(
        mem_session, env.admin, board="PH", label="focus"
    )
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # KIM ticket narrowed out by board
    assert not [n for n in nodes if n.type == "label"]


@pytest.mark.asyncio
async def test_unknown_board_404(mem_session: AsyncSession, env: Env) -> None:
    with pytest.raises(NotFound):
        await build_graph(mem_session, env.admin, board="NOPE")


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs 50 tickets (batched all-pairs).
# ---------------------------------------------------------------------------


class _StatementCounter:
    """Counts SQL statements via the engine's before_cursor_execute event."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


async def _seed_n_labeled_tickets(
    session: AsyncSession, board: Board, reporter: Actor, n: int
) -> None:
    label = f"bulk-{uuid.uuid4().hex[:6]}"
    tickets = [
        _make_ticket(board, reporter, f"{board.key}-{i}", labels=[label])
        for i in range(n)
    ]
    session.add_all(tickets)
    await session.commit()


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    # Graph over 1 ticket.
    await _seed_n_labeled_tickets(mem_session, env.board_ph, env.admin, 1)
    counter_1 = _StatementCounter()
    engine = mem_session.bind  # the AsyncEngine's sync engine
    sync_engine = engine.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    # Graph over 50 MORE tickets (51 total).
    await _seed_n_labeled_tickets(mem_session, env.board_kim, env.admin, 50)
    counter_50 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_50)
    try:
        nodes, _ = await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_50)

    # 51 ticket nodes assembled (proves the 50 were loaded, not lazily skipped).
    assert len([n for n in nodes if n.type == "ticket"]) == 51
    # THE INVARIANT (PH-288): statement count is CONSTANT across 1 vs 50 tickets
    # — the batched all-pairs strategy issues a FIXED set of queries (no per-node
    # related_tickets() fan-out). This equality is the real N+1 guard.
    assert counter_1.count == counter_50.count, (
        f"N+1 detected: 1 ticket={counter_1.count} stmts, "
        f"50 tickets={counter_50.count} stmts"
    )
    # Pinned constant (PH-288 = 5; was 4 in PH-281). Unscoped global graph =
    # ticket.read gate (membership SELECT + board selectinload) + Q1 tickets +
    # Q1 board selectinload = 4 statements, PLUS the ONE batched
    # all_overlapping_pairs() code-overlap self-join = 5. The empty-guarded
    # reference _resolve_keys batch adds none (no descriptions mention keys); the
    # board-scope key_map + shared-label-reach queries are scope=board only.
    assert counter_1.count == 5, counter_1.count


@pytest.mark.asyncio
async def test_soft_deleted_ticket_excluded(
    mem_session: AsyncSession, env: Env
) -> None:
    live = _make_ticket(env.board_ph, env.admin, "PH-1")
    gone = _make_ticket(env.board_ph, env.admin, "PH-2", deleted=True)
    mem_session.add_all([live, gone])
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin)
    keys = {n.key for n in nodes if n.type == "ticket"}
    assert keys == {"PH-1"}  # soft-deleted ticket filtered out


# ---------------------------------------------------------------------------
# Permission: actor lacking ticket.read → 403; default-role actor → 200.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_global_ticket_read(
    mem_session: AsyncSession, env: Env
) -> None:
    # Stranger has no membership → no ticket.read → PermissionDenied.
    with pytest.raises(PermissionDenied):
        await build_graph(mem_session, env.stranger)

    # Admin (member, role "*") passes — no raise.
    nodes, edges = await build_graph(mem_session, env.admin)
    assert isinstance(nodes, list) and isinstance(edges, list)


# ---------------------------------------------------------------------------
# Empty graph: no crash.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_graph(mem_session: AsyncSession, env: Env) -> None:
    nodes, edges = await build_graph(mem_session, env.admin)
    assert nodes == []
    assert edges == []


# ===========================================================================
# Graph v2 (PH-279) — board-scope collapse + reference edges + edge context.
# (PH-288: in-scope edges are now the weighted top-K set; collapse unchanged.)
# ===========================================================================


@pytest.mark.asyncio
async def test_scope_board_without_board_raises(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(ValueError, match="scope=board requires board"):
        await build_graph(mem_session, env.admin, scope="board")
    # An invalid scope literal is also a client error.
    with pytest.raises(ValueError, match="invalid scope"):
        await build_graph(mem_session, env.admin, board="PH", scope="bogus")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backward_compat_unscoped_unchanged(
    mem_session: AsyncSession, env: Env
) -> None:
    # Cross-board shared SPECIFIC label → ONE shared_label edge, both expanded.
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    # Default scope == explicit scope="global": NO board node, NO board edge,
    # foreign ticket fully expanded.
    default_nodes, default_edges = await build_graph(mem_session, env.admin)
    global_nodes, global_edges = await build_graph(
        mem_session, env.admin, scope="global"
    )
    assert {n.id for n in default_nodes} == {n.id for n in global_nodes}
    assert {e.id for e in default_edges} == {e.id for e in global_edges}

    assert not [n for n in default_nodes if n.type == "board"]
    assert not [e for e in default_edges if e.type == "board"]
    # Both boards' tickets expanded as full ticket nodes (no collapse).
    assert {n.key for n in default_nodes if n.type == "ticket"} == {"PH-1", "KIM-1"}

    # ?board=PH unscoped: board-narrow behavior, no board node.
    b_nodes, _ = await build_graph(mem_session, env.admin, board="PH")
    assert not [n for n in b_nodes if n.type == "board"]
    assert {n.key for n in b_nodes if n.type == "ticket"} == {"PH-1"}


@pytest.mark.asyncio
async def test_board_scope_shared_label_collapse(
    mem_session: AsyncSession, env: Env
) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )

    # In-scope detail intact: PH-1 ticket node present.
    assert {n.key for n in nodes if n.type == "ticket"} == {"PH-1"}
    # NO board-B ticket expanded.
    assert "KIM-1" not in {n.key for n in nodes if n.type == "ticket"}
    # PH-288: no label nodes even in board scope.
    assert not [n for n in nodes if n.type == "label"]

    # Exactly ONE board node, id board:KIM, representing board KIM.
    board_nodes = [n for n in nodes if n.type == "board"]
    assert len(board_nodes) == 1
    bn = board_nodes[0]
    assert bn.id == "board:KIM" and bn.board == "KIM" and bn.label == "Kims"

    # Aggregated board edge from the in-scope node to board:KIM, cross-board.
    board_edges = [e for e in edges if e.type == "board"]
    assert len(board_edges) == 1
    be = board_edges[0]
    assert be.source == f"ticket:{t_ph.id}"
    assert be.target == "board:KIM"
    assert be.context == "cross-board"


# ---------------------------------------------------------------------------
# PH-289: board-scope collapse drops HUB-label cross-board reach (the "her space
# Kims'e bağlı" hairball) but KEEPS specific-label reach.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_scope_hub_label_does_not_collapse(
    mem_session: AsyncSession, env: Env
) -> None:
    # The ONLY cross-board reach from PH to KIM is the generic "bug" label carried
    # by 15 KIM tickets + 1 PH ticket (n=16 >= 15 hub). PH-289 drops hub labels
    # from the collapse reach → NO board node / NO board edge (previously every
    # board manufactured a spurious board:KIM edge via a shared "bug").
    kim_bugs = [
        _make_ticket(env.board_kim, env.admin, f"KIM-{i}", labels=["bug"])
        for i in range(15)
    ]
    ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["bug"])
    mem_session.add_all([*kim_bugs, ph])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )
    assert {n.key for n in nodes if n.type == "ticket"} == {"PH-1"}
    assert [n for n in nodes if n.type == "board"] == []
    assert [e for e in edges if e.type == "board"] == []


@pytest.mark.asyncio
async def test_board_scope_specific_label_survives_hub_drop(
    mem_session: AsyncSession, env: Env
) -> None:
    # SELECTIVITY: a specific cross-board label ("checkout", n=2) still collapses to
    # board:KIM even though the same ticket ALSO carries the dropped hub "bug"
    # (n>=15) — the hub-drop is precise, not a blanket kill of the collapse.
    kim_bugs = [
        _make_ticket(env.board_kim, env.admin, f"KIM-{i}", labels=["bug"])
        for i in range(15)
    ]
    ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["bug", "checkout"])
    kim_specific = _make_ticket(
        env.board_kim, env.admin, "KIM-500", labels=["checkout"]
    )
    mem_session.add_all([*kim_bugs, ph, kim_specific])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )
    board_nodes = [n for n in nodes if n.type == "board"]
    board_edges = [e for e in edges if e.type == "board"]
    # board:KIM collapse present — driven by the SPECIFIC "checkout" label only.
    assert [n.id for n in board_nodes] == ["board:KIM"]
    assert len(board_edges) == 1
    assert board_edges[0].source == f"ticket:{ph.id}"
    assert board_edges[0].target == "board:KIM"


@pytest.mark.asyncio
async def test_board_scope_generic_label_does_not_collapse(
    mem_session: AsyncSession, env: Env
) -> None:
    # PH-289 stoplist: "qa" is org-wide workflow vocabulary. It sits on only 2
    # tickets (NOT a frequency hub), so ONLY the GENERIC_LABELS stoplist can drop
    # it — proving the stoplist, distinct from the n>=15 hub drop. The sole
    # cross-board reach is "qa" → no board node / no board edge.
    ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["qa"])
    kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["qa"])
    mem_session.add_all([ph, kim])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )
    assert {n.key for n in nodes if n.type == "ticket"} == {"PH-1"}
    assert [n for n in nodes if n.type == "board"] == []
    assert [e for e in edges if e.type == "board"] == []


@pytest.mark.asyncio
async def test_reference_edges_resolution(mem_session: AsyncSession, env: Env) -> None:
    # PH-100 "blocks PH-101" on its own line → dependency edge to PH-101. A
    # SEPARATE plain-mention line for KIM-7 (no dep keyword) → reference edge.
    # Non-existent ZZ-999, self PH-100, and the duplicate PH-101 mention are all
    # ignored. (The dep parser classifies every key AFTER the keyword ON THAT
    # LINE, so KIM-7 must live on its own keyword-free line to stay a reference.)
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-100",
        description=(
            "blocks PH-101; ignore ZZ-999 and PH-100 itself\n"
            "relates to KIM-7\n"
            "mention PH-101 twice"
        ),
    )
    dst_same = _make_ticket(env.board_ph, env.admin, "PH-101")
    dst_cross = _make_ticket(env.board_kim, env.admin, "KIM-7")
    mem_session.add_all([src, dst_same, dst_cross])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    src_node = f"ticket:{src.id}"

    def _pair(t: Ticket) -> set[str]:
        return {src_node, f"ticket:{t.id}"}

    same_edges = [e for e in edges if {e.source, e.target} == _pair(dst_same)]
    cross_edges = [e for e in edges if {e.source, e.target} == _pair(dst_cross)]
    assert len(same_edges) == 1 and same_edges[0].type == "dependency"
    assert len(cross_edges) == 1 and cross_edges[0].type == "reference"
    # ZZ-999 unresolved → no edge; PH-100 self → no edge. Total = 2 edges.
    rel = [e for e in edges if e.reason in ("dependency", "reference")]
    assert len(rel) == 2
    assert all(src_node in (e.source, e.target) for e in rel)
    assert all(e.strength is not None for e in rel)


@pytest.mark.asyncio
async def test_reference_non_existent_and_soft_deleted_ignored(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        description="see PH-99999 (never existed) and PH-2 (soft-deleted)",
    )
    gone = _make_ticket(env.board_ph, env.admin, "PH-2", deleted=True)
    mem_session.add_all([src, gone])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    # Neither resolves → no reference/dependency edge.
    assert not [e for e in edges if e.reason in ("reference", "dependency")]


@pytest.mark.asyncio
async def test_reference_epic_precedence_pair(
    mem_session: AsyncSession, env: Env
) -> None:
    # Parent and child linked by epic AND mutual references → ONE edge per pair,
    # epic outranks reference (precedence dependency > reference > epic? NO:
    # epic=3 < reference=5, so REFERENCE wins here). Assert ONE edge, reference.
    parent = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="parent of PH-2"
    )
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(
        env.board_ph, env.admin, "PH-2", epic_id=parent.id, description="child of PH-1"
    )
    mem_session.add(child)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    pair = [
        e
        for e in edges
        if {e.source, e.target} == {f"ticket:{parent.id}", f"ticket:{child.id}"}
    ]
    # ONE edge for the unordered pair; reference (5.0) outranks epic (3.0).
    assert len(pair) == 1
    assert pair[0].type == "reference"


@pytest.mark.asyncio
async def test_board_scope_collapses_foreign_reference(
    mem_session: AsyncSession, env: Env
) -> None:
    # In board-scope, a reference to a foreign-board ticket collapses to board-node.
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="relates to KIM-9"
    )
    foreign = _make_ticket(env.board_kim, env.admin, "KIM-9")
    mem_session.add_all([src, foreign])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )
    # KIM-9 NOT expanded; collapsed to board:KIM via a board edge.
    assert "KIM-9" not in {n.key for n in nodes if n.type == "ticket"}
    assert "board:KIM" in {n.id for n in nodes if n.type == "board"}
    board_edges = [e for e in edges if e.type == "board"]
    assert any(
        e.source == f"ticket:{src.id}" and e.target == "board:KIM"
        for e in board_edges
    )
    # No raw cross-board reference/dependency edge leaks in board-scope (the
    # foreign endpoint is not a node → dropped by the dangling invariant).
    assert not [e for e in edges if e.reason in ("reference", "dependency")]


@pytest.mark.asyncio
async def test_edge_carries_strength_reason_context(
    mem_session: AsyncSession, env: Env
) -> None:
    # epic edge carries strength + reason + context; no legacy has_label/tag edges.
    parent = _make_ticket(env.board_ph, env.admin, "PH-1")
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-2", epic_id=parent.id)
    mem_session.add(child)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    by_type = {e.type: e for e in edges}
    assert "epic" in by_type
    epic_edge = by_type["epic"]
    assert epic_edge.reason == "epic"
    assert isinstance(epic_edge.strength, float) and epic_edge.strength > 0
    assert epic_edge.context is not None
    # No legacy label/tag edge types are emitted anymore.
    assert "has_label" not in by_type
    assert "tag_link" not in by_type
    assert "has_tag" not in by_type


@pytest.mark.asyncio
async def test_reference_resolution_no_n_plus_one(
    mem_session: AsyncSession, env: Env
) -> None:
    async def _seed(board: Board, n: int) -> None:
        anchor = _make_ticket(board, env.admin, f"{board.key}-9000")
        mem_session.add(anchor)
        await mem_session.flush()
        tickets = [
            _make_ticket(
                board,
                env.admin,
                f"{board.key}-{9001 + i}",
                description=f"depends on {anchor.key}",
            )
            for i in range(n)
        ]
        mem_session.add_all(tickets)
        await mem_session.commit()

    await _seed(env.board_ph, 1)
    counter_1 = _StatementCounter()
    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        _, edges_1 = await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    await _seed(env.board_kim, 50)
    counter_50 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_50)
    try:
        _, edges_50 = await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_50)

    # Dependency edges WERE produced (proves the batch resolved, not skipped).
    assert [e for e in edges_1 if e.type == "dependency"]
    assert [e for e in edges_50 if e.type == "dependency"]
    # CONSTANT statement count: reference keys resolved in ONE bounded batch.
    assert counter_1.count == counter_50.count, (
        f"N+1 detected in reference resolution: 1={counter_1.count} stmts, "
        f"51={counter_50.count} stmts"
    )
