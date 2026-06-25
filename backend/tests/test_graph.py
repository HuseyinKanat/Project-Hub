"""Service-layer tests for the cross-board concept graph (PH-274/PH-279;
re-pointed to inline ``Ticket.labels`` in PH-281, epic PH-271 child 6/7).

Tests ``services.graph.build_graph`` DIRECTLY against an in-memory sqlite
``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env). Seeds
via ``Base.metadata.create_all``.

PH-281: the second bipartite axis is now the inline ``Ticket.labels`` ARRAY, not
the ConceptTag entity. Coverage:
- bipartite nodes + ``ticket:``/``label:`` prefixed ids (collision guard).
- a label value on tickets in TWO boards → 2 has_label edges, 1 shared label node.
- epic parent→child edge; cross-board (parent absent under ?board) → no edge.
- ?board / ?label / intersection filters; unknown board → 404, unknown label → empty.
- N+1 — statement count CONSTANT across 1 vs 50 tickets (inline ARRAY, no junction).
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
# bipartite + prefixed id scheme (collision guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bipartite_nodes_and_prefixed_ids(
    mem_session: AsyncSession, env: Env
) -> None:
    ticket = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["async-safety"])
    mem_session.add(ticket)
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin)

    types = {n.type for n in nodes}
    assert types == {"ticket", "label"}  # bipartite — both node kinds present

    ticket_nodes = [n for n in nodes if n.type == "ticket"]
    label_nodes = [n for n in nodes if n.type == "label"]
    assert all(n.id.startswith("ticket:") for n in ticket_nodes)
    assert all(n.id.startswith("label:") for n in label_nodes)

    # No id appears under two node kinds (prefix is the SOLE disambiguator).
    assert len({n.id for n in nodes}) == len(nodes)

    # ticket node carries board/key/state/title; label node carries raw value.
    tn = ticket_nodes[0]
    assert tn.board == "PH" and tn.key == "PH-1" and tn.state == "backlog"
    assert tn.title == "Ticket PH-1" and tn.board_id == env.board_ph.id
    ln = label_nodes[0]
    assert ln.id == "label:async-safety" and ln.label == "async-safety"
    # label nodes have no slug/color (frontend hashes the string).
    assert ln.color is None

    # Edge endpoints reference the prefixed ids verbatim; no relation field.
    assert len(edges) == 1
    edge = edges[0]
    assert edge.type == "has_label" and edge.context == "has-label"
    assert edge.source == f"ticket:{ticket.id}"
    assert edge.target == "label:async-safety"
    assert not hasattr(edge, "relation") or edge.model_dump().get("relation") is None


# ---------------------------------------------------------------------------
# raw-value label id encoding round-trips (colon / space inside value).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_label_id_keeps_raw_value(mem_session: AsyncSession, env: Env) -> None:
    ticket = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["foo:bar", "has space"]
    )
    mem_session.add(ticket)
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin)
    label_ids = {n.id for n in nodes if n.type == "label"}
    # Raw value verbatim after the FIRST `label:` prefix — injective, no slugify.
    assert label_ids == {"label:foo:bar", "label:has space"}
    assert {n.label for n in nodes if n.type == "label"} == {"foo:bar", "has space"}


# ---------------------------------------------------------------------------
# cross-board has_label — one label on tickets in 2 boards → 2 edges, 1 node.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_board_has_label(mem_session: AsyncSession, env: Env) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin)

    label_nodes = [n for n in nodes if n.type == "label"]
    assert len(label_nodes) == 1  # single shared label node
    assert label_nodes[0].id == "label:shared"

    has_label = [e for e in edges if e.type == "has_label"]
    assert len(has_label) == 2  # one per board ticket
    # Both edges point at the SAME label node (cross-board join via shared string).
    assert {e.target for e in has_label} == {"label:shared"}
    assert {e.source for e in has_label} == {
        f"ticket:{t_ph.id}",
        f"ticket:{t_kim.id}",
    }


# ---------------------------------------------------------------------------
# epic parent→child edge; cross-board parent absent → no edge.
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

    # Unfiltered: both tickets present → epic edge emitted parent→child.
    _, edges = await build_graph(mem_session, env.admin)
    epic = [e for e in edges if e.type == "epic"]
    assert len(epic) == 1
    assert epic[0].source == f"ticket:{parent.id}"  # parent
    assert epic[0].target == f"ticket:{child.id}"  # child

    # ?board=PH: parent (on KIM) absent → no epic edge (dangling guard).
    _, edges_ph = await build_graph(mem_session, env.admin, board="PH")
    assert not [e for e in edges_ph if e.type == "epic"]


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
    label_nodes = [n for n in nodes if n.type == "label"]
    # Only PH ticket + the label it carries; KIM ticket + kim-label absent.
    assert {n.key for n in ticket_nodes} == {"PH-1"}
    assert {n.label for n in label_nodes} == {"ph-label"}

    # Case-insensitive board key.
    nodes_lower, _ = await build_graph(mem_session, env.admin, board="ph")
    assert {n.key for n in nodes_lower if n.type == "ticket"} == {"PH-1"}


@pytest.mark.asyncio
async def test_label_filter_subgraph(mem_session: AsyncSession, env: Env) -> None:
    # PH-1 carries focus + co-occurring; PH-2 carries far only → excluded by ?label.
    t1 = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["focus", "cooccur"])
    t2 = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["far"])
    mem_session.add_all([t1, t2])
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin, label="focus")
    label_values = {n.label for n in nodes if n.type == "label"}
    # focus + co-occurring label on the same ticket; far excluded.
    assert label_values == {"focus", "cooccur"}
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # only ticket carrying focus


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

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", label="focus"
    )
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # KIM ticket narrowed out by board
    assert {n.label for n in nodes if n.type == "label"} == {"focus"}
    has_label = [e for e in edges if e.type == "has_label"]
    assert len(has_label) == 1
    assert has_label[0].source == f"ticket:{t_ph.id}"


@pytest.mark.asyncio
async def test_unknown_board_404(mem_session: AsyncSession, env: Env) -> None:
    with pytest.raises(NotFound):
        await build_graph(mem_session, env.admin, board="NOPE")


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs 50 tickets (inline ARRAY).
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
    # CONSTANT statement count: labels read inline off the ticket SELECT → no
    # per-ticket lazy load (N+1) and no junction query.
    assert counter_1.count == counter_50.count, (
        f"N+1 detected: 1 ticket={counter_1.count} stmts, "
        f"50 tickets={counter_50.count} stmts"
    )
    # Strictly LOWER than the PH-279 ConceptTag count. Unscoped global graph =
    # ticket.read gate (membership SELECT + board selectinload) + Q1 tickets +
    # board selectinload = 4 statements; the empty-guarded reference batch adds
    # none. PH-279 also issued the ConceptTagLink (Q2) + ConceptTag map (Q3)
    # queries — those are gone. Pin the new lower count.
    assert counter_1.count == 4, counter_1.count


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
    # Cross-board shared label, as in the cross-board test.
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([t_ph, t_kim])
    await mem_session.commit()

    # Default scope == explicit scope="global": NO board node, NO board edge,
    # foreign ticket+label fully expanded.
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

    # In-scope detail intact: PH-1 ticket + its label fully expanded.
    assert {n.key for n in nodes if n.type == "ticket"} == {"PH-1"}
    assert {n.label for n in nodes if n.type == "label"} == {"shared"}
    # NO board-B ticket expanded.
    assert "KIM-1" not in {n.key for n in nodes if n.type == "ticket"}

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


@pytest.mark.asyncio
async def test_reference_edges_resolution(mem_session: AsyncSession, env: Env) -> None:
    # PH-100 description references PH-101 (same board) + KIM-7 (cross-board) +
    # a non-existent key ZZ-999 + its OWN key PH-100 (self) + PH-101 again (dup).
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-100",
        description="blocks PH-101 and relates to KIM-7; ignore ZZ-999 "
        "and PH-100 itself; mention PH-101 twice",
    )
    dst_same = _make_ticket(env.board_ph, env.admin, "PH-101")
    dst_cross = _make_ticket(env.board_kim, env.admin, "KIM-7")
    mem_session.add_all([src, dst_same, dst_cross])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    refs = [e for e in edges if e.type == "reference"]

    by_target = {e.target: e for e in refs}
    assert f"ticket:{dst_same.id}" in by_target
    assert f"ticket:{dst_cross.id}" in by_target
    # Mention dedupe: PH-101 mentioned twice → exactly ONE edge.
    assert len([e for e in refs if e.target == f"ticket:{dst_same.id}"]) == 1
    # ZZ-999 unresolved → no edge; PH-100 self → no edge.
    assert all(e.source == f"ticket:{src.id}" for e in refs)
    assert all(e.target != f"ticket:{src.id}" for e in refs)
    assert len(refs) == 2
    assert all(e.context == "ticket-reference" for e in refs)


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
    assert not [e for e in edges if e.type == "reference"]  # neither resolves


@pytest.mark.asyncio
async def test_reference_epic_wins_ordered_pair(
    mem_session: AsyncSession, env: Env
) -> None:
    # Parent mentions child's key (SAME ordered pair as epic parent→child) → epic
    # wins, no duplicate reference. Child mentions parent (reverse pair) → kept.
    parent = _make_ticket(env.board_ph, env.admin, "PH-1")
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-2",
        epic_id=parent.id,
        description="child of PH-1",  # child → parent reference (reverse of epic)
    )
    parent.description = "parent of PH-2"
    mem_session.add(child)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    epic = [e for e in edges if e.type == "epic"]
    refs = [e for e in edges if e.type == "reference"]

    assert len(epic) == 1
    assert epic[0].source == f"ticket:{parent.id}"  # parent → child
    assert epic[0].target == f"ticket:{child.id}"
    # Parent→child reference SUPPRESSED (epic wins ordered pair).
    assert not [
        e
        for e in refs
        if e.source == f"ticket:{parent.id}" and e.target == f"ticket:{child.id}"
    ]
    # Child→parent reference KEPT (different ordered pair).
    assert [
        e
        for e in refs
        if e.source == f"ticket:{child.id}" and e.target == f"ticket:{parent.id}"
    ]


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
    # No raw cross-board reference edge leaks in board-scope.
    assert not [e for e in edges if e.type == "reference"]


@pytest.mark.asyncio
async def test_edge_context_per_type(mem_session: AsyncSession, env: Env) -> None:
    parent = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["a"])
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-2", epic_id=parent.id)
    mem_session.add(child)
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    by_type = {e.type: e for e in edges}
    assert by_type["has_label"].context == "has-label"
    assert by_type["epic"].context == "epic"
    # No tag_link edge type exists anymore (labels have no label↔label relation).
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

    # Reference edges WERE produced (proves the batch resolved, not skipped).
    assert [e for e in edges_1 if e.type == "reference"]
    assert [e for e in edges_50 if e.type == "reference"]
    # CONSTANT statement count: reference keys resolved in ONE bounded batch.
    assert counter_1.count == counter_50.count, (
        f"N+1 detected in reference resolution: 1={counter_1.count} stmts, "
        f"51={counter_50.count} stmts"
    )
