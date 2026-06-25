"""Service-layer tests for the cross-board concept graph (PH-274, epic PH-271 3/7).

Tests ``services.graph.build_graph`` DIRECTLY against an in-memory sqlite
``db_session`` (NOT via HTTP TestClient — it hangs in this Docker env; mirrors
the PH-272/273 service-test approach). Seeds via ``Base.metadata.create_all``.

Coverage maps to the ACs:
- AC1: bipartite nodes + ``ticket:``/``tag:`` prefixed ids (collision guard).
- AC2: a tag on tickets in TWO boards → 2 has_tag edges sharing one tag node.
- AC3: tag_link edges carry ``relation``; directed source→target; reverse NOT deduped.
- AC4: epic parent→child edge; cross-board (parent absent under ?board) → no edge.
- AC5: ?board / ?tag / intersection filters; unknown key/slug → 404.
- AC6: N+1 — statement count CONSTANT across 1 vs 50 tickets (selectinload, no lazy).
- orphan policy: standalone in unfiltered graph, excluded under filters.
- permission: actor lacking tag.read → 403 PermissionDenied.
- empty graph: no crash, {nodes: [], edges: []}.
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
    ConceptTag,
    ConceptTagLink,
    Ticket,
    TicketConceptTag,
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
    """Two boards (PH + KIM), an admin member of PH, and a tag-read actor."""

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
    # `stranger` has NO board membership → no tag.read → 403 (permission test).
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

    # admin is a member of BOTH boards (admin role grants tag.read via "*").
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
        deleted_at=datetime.now(UTC) if deleted else None,
    )


def _make_tag(slug: str, name: str, *, color: str | None = None) -> ConceptTag:
    return ConceptTag(id=uuid.uuid4(), slug=slug, name=name, color=color)


def _attach(ticket: Ticket, tag: ConceptTag) -> TicketConceptTag:
    return TicketConceptTag(
        id=uuid.uuid4(), ticket_id=ticket.id, concept_tag_id=tag.id
    )


# ---------------------------------------------------------------------------
# AC1: bipartite + prefixed id scheme (collision guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bipartite_nodes_and_prefixed_ids(
    mem_session: AsyncSession, env: Env
) -> None:
    ticket = _make_ticket(env.board_ph, env.admin, "PH-1")
    tag = _make_tag("async-safety", "Async Safety", color="#7dd3fc")
    mem_session.add_all([ticket, tag])
    await mem_session.flush()
    mem_session.add(_attach(ticket, tag))
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin)

    types = {n.type for n in nodes}
    assert types == {"ticket", "tag"}  # bipartite — both node kinds present

    ticket_nodes = [n for n in nodes if n.type == "ticket"]
    tag_nodes = [n for n in nodes if n.type == "tag"]
    assert all(n.id.startswith("ticket:") for n in ticket_nodes)
    assert all(n.id.startswith("tag:") for n in tag_nodes)

    # No id appears under two node kinds (prefix is the SOLE disambiguator).
    assert len({n.id for n in nodes}) == len(nodes)

    # ticket node carries board/key/state/title; tag node carries slug/color.
    tn = ticket_nodes[0]
    assert tn.board == "PH" and tn.key == "PH-1" and tn.state == "backlog"
    assert tn.title == "Ticket PH-1" and tn.board_id == env.board_ph.id
    gn = tag_nodes[0]
    assert gn.slug == "async-safety" and gn.color == "#7dd3fc" and gn.label == "Async Safety"

    # Edge endpoints reference the prefixed ids verbatim.
    assert len(edges) == 1
    edge = edges[0]
    assert edge.type == "has_tag"
    assert edge.source == f"ticket:{ticket.id}"
    assert edge.target == f"tag:{tag.id}"


# ---------------------------------------------------------------------------
# AC2: cross-board has_tag — one tag on tickets in 2 boards → 2 edges, 1 tag node.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_board_has_tag(mem_session: AsyncSession, env: Env) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1")
    tag = _make_tag("shared", "Shared Concept")
    mem_session.add_all([t_ph, t_kim, tag])
    await mem_session.flush()
    mem_session.add_all([_attach(t_ph, tag), _attach(t_kim, tag)])
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin)

    tag_nodes = [n for n in nodes if n.type == "tag"]
    assert len(tag_nodes) == 1  # single shared tag node

    has_tag = [e for e in edges if e.type == "has_tag"]
    assert len(has_tag) == 2  # one per board ticket
    # Both edges point at the SAME tag node (cross-board join).
    assert {e.target for e in has_tag} == {f"tag:{tag.id}"}
    assert {e.source for e in has_tag} == {
        f"ticket:{t_ph.id}",
        f"ticket:{t_kim.id}",
    }


# ---------------------------------------------------------------------------
# AC3: tag_link edges carry relation; directed; reverse NOT deduped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_link_relation_and_direction(
    mem_session: AsyncSession, env: Env
) -> None:
    a = _make_tag("a", "A")
    b = _make_tag("b", "B")
    mem_session.add_all([a, b])
    await mem_session.flush()
    # Forward + reverse rows (distinct by design) + a different relation.
    fwd = ConceptTagLink(
        id=uuid.uuid4(), source_tag_id=a.id, target_tag_id=b.id, relation="relates"
    )
    rev = ConceptTagLink(
        id=uuid.uuid4(), source_tag_id=b.id, target_tag_id=a.id, relation="parent"
    )
    mem_session.add_all([fwd, rev])
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)

    links = [e for e in edges if e.type == "tag_link"]
    assert len(links) == 2  # reverse pair NOT deduped

    by_dir = {(e.source, e.target): e for e in links}
    fwd_edge = by_dir[(f"tag:{a.id}", f"tag:{b.id}")]
    rev_edge = by_dir[(f"tag:{b.id}", f"tag:{a.id}")]
    assert fwd_edge.relation == "relates"
    assert rev_edge.relation == "parent"
    # has_tag/epic edges never carry a relation.
    assert all(e.relation is None for e in edges if e.type != "tag_link")


# ---------------------------------------------------------------------------
# AC4: epic parent→child edge; cross-board parent absent → no edge.
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
# AC5: filters — ?board, ?tag, intersection, unknown key/slug → 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_filter(mem_session: AsyncSession, env: Env) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1")
    tag_ph = _make_tag("ph-tag", "PH Tag")
    tag_kim = _make_tag("kim-tag", "KIM Tag")
    mem_session.add_all([t_ph, t_kim, tag_ph, tag_kim])
    await mem_session.flush()
    mem_session.add_all([_attach(t_ph, tag_ph), _attach(t_kim, tag_kim)])
    await mem_session.commit()

    nodes, _ = await build_graph(mem_session, env.admin, board="PH")
    ticket_nodes = [n for n in nodes if n.type == "ticket"]
    tag_nodes = [n for n in nodes if n.type == "tag"]
    # Only PH ticket + the tag attached to it; KIM ticket + kim-tag absent.
    assert {n.key for n in ticket_nodes} == {"PH-1"}
    assert {n.slug for n in tag_nodes} == {"ph-tag"}

    # Case-insensitive board key.
    nodes_lower, _ = await build_graph(mem_session, env.admin, board="ph")
    assert {n.key for n in nodes_lower if n.type == "ticket"} == {"PH-1"}


@pytest.mark.asyncio
async def test_tag_filter_subgraph(mem_session: AsyncSession, env: Env) -> None:
    focus = _make_tag("focus", "Focus")
    neighbor = _make_tag("neighbor", "Neighbor")
    far = _make_tag("far", "Far")  # not linked to focus → excluded
    mem_session.add_all([focus, neighbor, far])
    await mem_session.flush()

    t1 = _make_ticket(env.board_ph, env.admin, "PH-1")  # attached to focus
    t2 = _make_ticket(env.board_ph, env.admin, "PH-2")  # attached to far only
    mem_session.add_all([t1, t2])
    await mem_session.flush()
    mem_session.add_all(
        [
            _attach(t1, focus),
            _attach(t2, far),
            ConceptTagLink(
                id=uuid.uuid4(),
                source_tag_id=focus.id,
                target_tag_id=neighbor.id,
                relation="relates",
            ),
        ]
    )
    await mem_session.commit()

    nodes, edges = await build_graph(mem_session, env.admin, tag="focus")
    tag_slugs = {n.slug for n in nodes if n.type == "tag"}
    assert tag_slugs == {"focus", "neighbor"}  # focus + 1-hop neighbor; far excluded
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # only ticket attached to focus
    # tag_link edge to neighbor present; both endpoints in node set.
    assert any(e.type == "tag_link" for e in edges)


@pytest.mark.asyncio
async def test_intersection_board_and_tag(
    mem_session: AsyncSession, env: Env
) -> None:
    focus = _make_tag("focus", "Focus")
    mem_session.add(focus)
    await mem_session.flush()
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1")
    mem_session.add_all([t_ph, t_kim])
    await mem_session.flush()
    # Both tickets carry the focus tag; intersection ?board=PH must drop KIM.
    mem_session.add_all([_attach(t_ph, focus), _attach(t_kim, focus)])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", tag="focus"
    )
    ticket_keys = {n.key for n in nodes if n.type == "ticket"}
    assert ticket_keys == {"PH-1"}  # KIM ticket narrowed out by board
    # Focus tag node still present; has_tag edge only to the surviving PH ticket.
    assert {n.slug for n in nodes if n.type == "tag"} == {"focus"}
    has_tag = [e for e in edges if e.type == "has_tag"]
    assert len(has_tag) == 1
    assert has_tag[0].source == f"ticket:{t_ph.id}"


@pytest.mark.asyncio
async def test_unknown_filters_404(mem_session: AsyncSession, env: Env) -> None:
    with pytest.raises(NotFound):
        await build_graph(mem_session, env.admin, board="NOPE")
    with pytest.raises(NotFound):
        await build_graph(mem_session, env.admin, tag="does-not-exist")


# ---------------------------------------------------------------------------
# AC6: N+1 — statement count CONSTANT across 1 vs 50 tickets.
# ---------------------------------------------------------------------------


class _StatementCounter:
    """Counts SQL statements via the engine's before_cursor_execute event."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


async def _seed_n_tagged_tickets(
    session: AsyncSession, board: Board, reporter: Actor, n: int
) -> None:
    tag = _make_tag(f"bulk-{uuid.uuid4().hex[:6]}", "Bulk Tag")
    session.add(tag)
    await session.flush()
    tickets = [
        _make_ticket(board, reporter, f"{board.key}-{i}") for i in range(n)
    ]
    session.add_all(tickets)
    await session.flush()
    session.add_all([_attach(t, tag) for t in tickets])
    await session.commit()


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    # Graph over 1 ticket.
    await _seed_n_tagged_tickets(mem_session, env.board_ph, env.admin, 1)
    counter_1 = _StatementCounter()
    engine = mem_session.bind  # the AsyncEngine's sync engine
    sync_engine = engine.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    # Graph over 50 MORE tickets (51 total).
    await _seed_n_tagged_tickets(mem_session, env.board_kim, env.admin, 50)
    counter_50 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_50)
    try:
        nodes, _ = await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_50)

    # 51 ticket nodes assembled (proves the 50 were loaded, not lazily skipped).
    assert len([n for n in nodes if n.type == "ticket"]) == 51
    # CONSTANT statement count: selectinload issues a FIXED number of SELECTs
    # regardless of row count → no per-ticket lazy load (N+1).
    assert counter_1.count == counter_50.count, (
        f"N+1 detected: 1 ticket={counter_1.count} stmts, "
        f"50 tickets={counter_50.count} stmts"
    )


# ---------------------------------------------------------------------------
# Orphan policy: standalone in unfiltered graph, excluded under filters.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_tag_policy(mem_session: AsyncSession, env: Env) -> None:
    orphan = _make_tag("orphan", "Orphan")  # no junction, no link
    ticket = _make_ticket(env.board_ph, env.admin, "PH-1")
    attached = _make_tag("attached", "Attached")
    mem_session.add_all([orphan, ticket, attached])
    await mem_session.flush()
    mem_session.add(_attach(ticket, attached))
    await mem_session.commit()

    # Unfiltered: orphan present as a standalone node.
    nodes, _ = await build_graph(mem_session, env.admin)
    assert "orphan" in {n.slug for n in nodes if n.type == "tag"}

    # ?board=PH: orphan (attached to nothing on PH) EXCLUDED.
    nodes_b, _ = await build_graph(mem_session, env.admin, board="PH")
    assert "orphan" not in {n.slug for n in nodes_b if n.type == "tag"}
    assert "attached" in {n.slug for n in nodes_b if n.type == "tag"}


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
# Permission: actor lacking tag.read → 403; default-role actor → 200.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_global_tag_read(
    mem_session: AsyncSession, env: Env
) -> None:
    # Stranger has no membership → no tag.read → PermissionDenied.
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
# PH-279 — Graph v2: board-scope collapse + reference edges + edge context.
# ===========================================================================


# ---------------------------------------------------------------------------
# AC: scope param validation — scope=board without board → ValueError (→422).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_board_without_board_raises(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(ValueError, match="scope=board requires board"):
        await build_graph(mem_session, env.admin, scope="board")
    # An invalid scope literal is also a client error.
    with pytest.raises(ValueError, match="invalid scope"):
        await build_graph(mem_session, env.admin, board="PH", scope="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC: backward-compat — scope=global / unscoped UNCHANGED from PH-274.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backward_compat_unscoped_unchanged(
    mem_session: AsyncSession, env: Env
) -> None:
    # Cross-board shared tag, as in the PH-274 cross-board test.
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1")
    tag = _make_tag("shared", "Shared Concept")
    mem_session.add_all([t_ph, t_kim, tag])
    await mem_session.flush()
    mem_session.add_all([_attach(t_ph, tag), _attach(t_kim, tag)])
    await mem_session.commit()

    # Default scope == explicit scope="global": NO board node, NO board edge,
    # foreign ticket+tag fully expanded (PH-274 detailed cross-board graph).
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

    # ?board=PH unscoped: PH-274 board-narrow behavior unchanged (no board node).
    b_nodes, _ = await build_graph(mem_session, env.admin, board="PH")
    assert not [n for n in b_nodes if n.type == "board"]
    assert {n.key for n in b_nodes if n.type == "ticket"} == {"PH-1"}


# ---------------------------------------------------------------------------
# AC: board-scope collapse — foreign endpoint → board-node + aggregated edge.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_scope_shared_tag_collapse(
    mem_session: AsyncSession, env: Env
) -> None:
    t_ph = _make_ticket(env.board_ph, env.admin, "PH-1")
    t_kim = _make_ticket(env.board_kim, env.admin, "KIM-1")
    shared = _make_tag("shared", "Shared Concept")
    mem_session.add_all([t_ph, t_kim, shared])
    await mem_session.flush()
    mem_session.add_all([_attach(t_ph, shared), _attach(t_kim, shared)])
    await mem_session.commit()

    nodes, edges = await build_graph(
        mem_session, env.admin, board="PH", scope="board"
    )

    # In-scope detail intact: PH-1 ticket + its tag fully expanded.
    assert {n.key for n in nodes if n.type == "ticket"} == {"PH-1"}
    assert {n.slug for n in nodes if n.type == "tag"} == {"shared"}
    # NO board-B ticket / board-B-only tag expanded.
    assert "KIM-1" not in {n.key for n in nodes if n.type == "ticket"}

    # Exactly ONE board node, id board:KIM, representing board KIM.
    board_nodes = [n for n in nodes if n.type == "board"]
    assert len(board_nodes) == 1
    bn = board_nodes[0]
    assert bn.id == "board:KIM" and bn.board == "KIM" and bn.label == "Kims"

    # Aggregated board edge from the in-scope node to board:KIM, context cross-board.
    board_edges = [e for e in edges if e.type == "board"]
    assert len(board_edges) == 1
    be = board_edges[0]
    assert be.source == f"ticket:{t_ph.id}"
    assert be.target == "board:KIM"
    assert be.context == "cross-board"


# ---------------------------------------------------------------------------
# AC: reference edges — resolve real key, ignore non-existent, self-skip,
#     cross-board, epic-wins ordered-pair dedupe, child→parent kept, mention dedupe.
# ---------------------------------------------------------------------------


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
    # PH-100 → PH-101 (resolved, same board) and PH-100 → KIM-7 (cross-board).
    assert f"ticket:{dst_same.id}" in by_target
    assert f"ticket:{dst_cross.id}" in by_target
    # Mention dedupe: PH-101 mentioned twice → exactly ONE edge.
    assert len([e for e in refs if e.target == f"ticket:{dst_same.id}"]) == 1
    # ZZ-999 unresolved → no edge; PH-100 self → no edge.
    assert all(e.source == f"ticket:{src.id}" for e in refs)
    assert all(e.target != f"ticket:{src.id}" for e in refs)
    assert len(refs) == 2
    # Every reference edge carries the labelable context.
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
    # Parent literally mentions the child key (same ordered pair as the epic edge).
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


# ---------------------------------------------------------------------------
# AC: edge context present per type (has_tag / tag_link / epic).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edge_context_per_type(mem_session: AsyncSession, env: Env) -> None:
    parent = _make_ticket(env.board_ph, env.admin, "PH-1")
    mem_session.add(parent)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-2", epic_id=parent.id)
    a = _make_tag("a", "A")
    b = _make_tag("b", "B")
    mem_session.add_all([child, a, b])
    await mem_session.flush()
    mem_session.add_all(
        [
            _attach(parent, a),
            ConceptTagLink(
                id=uuid.uuid4(),
                source_tag_id=a.id,
                target_tag_id=b.id,
                relation="relates",
            ),
        ]
    )
    await mem_session.commit()

    _, edges = await build_graph(mem_session, env.admin)
    by_type = {e.type: e for e in edges}
    assert by_type["has_tag"].context == "has-tag"
    assert by_type["tag_link"].context == "relates"  # mirrors relation
    assert by_type["tag_link"].relation == "relates"  # relation UNCHANGED (frozen)
    assert by_type["epic"].context == "epic"


# ---------------------------------------------------------------------------
# AC: N+1 — reference-key resolution stays a single batch (constant stmt count).
# ---------------------------------------------------------------------------


async def _seed_n_reference_tickets(
    session: AsyncSession, board: Board, reporter: Actor, n: int
) -> None:
    """N tickets each whose description references a real anchor ticket key."""
    anchor = _make_ticket(board, reporter, f"{board.key}-9000")
    session.add(anchor)
    await session.flush()
    tickets = [
        _make_ticket(
            board,
            reporter,
            f"{board.key}-{9001 + i}",
            description=f"depends on {anchor.key}",
        )
        for i in range(n)
    ]
    session.add_all(tickets)
    await session.commit()


@pytest.mark.asyncio
async def test_reference_resolution_no_n_plus_one(
    mem_session: AsyncSession, env: Env
) -> None:
    await _seed_n_reference_tickets(mem_session, env.board_ph, env.admin, 1)
    counter_1 = _StatementCounter()
    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        _, edges_1 = await build_graph(mem_session, env.admin)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    await _seed_n_reference_tickets(mem_session, env.board_kim, env.admin, 50)
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
