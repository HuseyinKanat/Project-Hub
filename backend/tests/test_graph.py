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
) -> Ticket:
    from datetime import UTC, datetime

    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=f"Ticket {key}",
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
