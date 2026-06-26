"""Service-layer tests for history-aware context recall (PH-286, epic PH-283
child C — the FINAL tool).

Tests ``services.recall.recall_context`` DIRECTLY against an in-memory sqlite
``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env),
mirroring ``tests/test_relationships.py`` seed style. Also exercises the MCP
dispatch arm (``_dispatch_tool("recall_context", ...)``) + tools/list sync so the
tool is proven callable + advertised end-to-end.

Coverage:
- TICKET path: bare key dispatches via ``_TICKET_KEY_RE.fullmatch`` → recalls
  related tickets + extracts [HANDOFF...] snippets + a technical_depth excerpt.
- TOPIC path: free text → search seeds (+ graph expansion); a pure search hit
  with no graph edge appears with score 0.0 / reasons [].
- topic-that-mentions-a-key stays a TOPIC (.fullmatch not .search).
- history-state-first re-rank: done/closed surface above open work at equal score.
- summary fallback chain: technical_depth → impact_analysis → description → none;
  decision-section preference.
- handoff filter: ONLY [HANDOFF...] lines, newest first, capped + truncated.
- NO N+1: statement count CONSTANT across 1 vs N recalled tickets.
- self-exclusion (input ticket absent on the ticket path); empty → [].
- read-gate (delegated to the seams): stranger denied, member + pm allowed.
- unknown key → NotFound (surfaced via the dispatch arm).
- tools/list advertises recall_context with {query, limit}.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
    Comment,
    Ticket,
    Workflow,
)
from app.mcp.server import _build_mcp_tool_list, _dispatch_tool
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.recall import (
    _HANDOFF_CHARS,
    _MAX_HANDOFFS_PER_TICKET,
    _is_ticket_key,
    recall_context,
)
from app.services.relationships import related_tickets

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


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
    """Two boards (PH + KIM), an admin member of both, a no-membership stranger,
    plus a `pm`-role member of PH (read-gate delegation)."""

    def __init__(
        self,
        *,
        board_ph: Board,
        board_kim: Board,
        admin: Actor,
        stranger: Actor,
        pm: Actor,
    ) -> None:
        self.board_ph = board_ph
        self.board_kim = board_kim
        self.admin = admin
        self.stranger = stranger
        self.pm = pm


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
    stranger = Actor(
        kind="agent", display_name="Stranger", token_hash="z", is_active=True
    )
    pm = Actor(kind="agent", display_name="jarwis-pm", token_hash="p", is_active=True)
    mem_session.add_all([admin, stranger, pm])
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

    mem_session.add_all(
        [
            BoardMembership(board_id=board_ph.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_kim.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_ph.id, actor_id=pm.id, role="pm"),
        ]
    )
    await mem_session.commit()

    return Env(
        board_ph=board_ph,
        board_kim=board_kim,
        admin=await _reload_actor(mem_session, admin.id),
        stranger=await _reload_actor(mem_session, stranger.id),
        pm=await _reload_actor(mem_session, pm.id),
    )


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    title: str | None = None,
    state: str = "backlog",
    epic_id: uuid.UUID | None = None,
    description: str = "",
    labels: list[str] | None = None,
    technical_depth: str | None = None,
    impact_analysis: str | None = None,
    updated_offset: int = 0,
) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        key=key,
        board_id=board.id,
        type="feature",
        title=title or f"Ticket {key}",
        description=description,
        state=state,
        reporter_id=reporter.id,
        epic_id=epic_id,
        labels=labels or [],
        technical_depth=technical_depth,
        impact_analysis=impact_analysis,
        updated_at=_BASE_TIME + timedelta(minutes=updated_offset),
    )


def _add_comment(
    session: AsyncSession, ticket: Ticket, author: Actor, body: str, *, offset: int
) -> None:
    session.add(
        Comment(
            ticket_id=ticket.id,
            author_id=author.id,
            body=body,
            created_at=_BASE_TIME + timedelta(minutes=offset),
        )
    )


def _by_key(items: list, key: str):  # type: ignore[no-untyped-def]
    for item in items:
        if item.key == key:
            return item
    raise AssertionError(f"{key} not in {[i.key for i in items]}")


# ---------------------------------------------------------------------------
# Dispatch detector (unit) — anchored fullmatch, not substring search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("PH-274", True),
        ("ph-274", True),  # case-folded
        ("  PH-274  ", True),  # stripped
        ("relationship scoring", False),
        ("how PH-274 did scoring", False),  # mentions a key → still a topic
        ("PH-274 PH-275", False),  # two keys → topic
    ],
)
def test_is_ticket_key_dispatch(query: str, expected: bool) -> None:
    assert _is_ticket_key(query) is expected


# ---------------------------------------------------------------------------
# TICKET path — related tickets + [HANDOFF] snippets + technical_depth excerpt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_path_recalls_related_with_context(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    cand = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-2",
        state="done",
        technical_depth=(
            "## Approach\nWe chose the weighted IDF model.\n## Risks\nnone"
        ),
    )
    mem_session.add_all([src, cand])
    await mem_session.flush()
    _add_comment(mem_session, cand, env.admin, "plain note, not a handoff", offset=1)
    _add_comment(
        mem_session,
        cand,
        env.admin,
        "[HANDOFF architect→backend] approved — go build it",
        offset=2,
    )
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="PH-1")

    item = _by_key(out, "PH-2")
    assert item.state == "done"
    assert item.score > 0
    assert {r.type for r in item.reasons} == {"dependency"}
    # [HANDOFF] line came through; the plain note did NOT.
    assert item.context.handoffs == [
        "[HANDOFF architect→backend] approved — go build it"
    ]
    # technical_depth excerpt, decision-section preferred (starts at "## Approach").
    assert item.context.summary_source == "technical_depth"
    assert item.context.summary.startswith("## Approach")
    assert "weighted IDF model" in item.context.summary


@pytest.mark.asyncio
async def test_ticket_path_self_excluded(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="PH-1")
    assert "PH-1" not in {i.key for i in out}


@pytest.mark.asyncio
async def test_ticket_path_unknown_key_raises(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await recall_context(mem_session, env.admin, query="PH-999")


# ---------------------------------------------------------------------------
# Summary fallback chain + handoff filter/caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_falls_back_impact_then_description(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2, PH-3, PH-4"
    )
    # PH-2: no tech_depth → impact_analysis
    cand_impact = _make_ticket(
        env.board_ph, env.admin, "PH-2", impact_analysis="impact note here"
    )
    # PH-3: no tech_depth + no impact → description
    cand_desc = _make_ticket(
        env.board_ph, env.admin, "PH-3", description="just a description body"
    )
    # PH-4: everything empty → "none"
    cand_none = _make_ticket(env.board_ph, env.admin, "PH-4")
    mem_session.add_all([src, cand_impact, cand_desc, cand_none])
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="PH-1", limit=10)
    assert _by_key(out, "PH-2").context.summary_source == "impact_analysis"
    assert _by_key(out, "PH-2").context.summary == "impact note here"
    assert _by_key(out, "PH-3").context.summary_source == "description"
    assert _by_key(out, "PH-4").context.summary_source == "none"
    assert _by_key(out, "PH-4").context.summary == ""


@pytest.mark.asyncio
async def test_handoffs_capped_and_truncated_newest_first(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.flush()
    # 7 handoffs (more than the cap of 5) + 1 long one to test truncation.
    for i in range(7):
        _add_comment(
            mem_session, cand, env.admin, f"[HANDOFF step {i}]", offset=i
        )
    _add_comment(
        mem_session, cand, env.admin, "[HANDOFF " + "x" * 400, offset=100
    )
    await mem_session.commit()

    item = _by_key(
        await recall_context(mem_session, env.admin, query="PH-1"), "PH-2"
    )
    assert len(item.context.handoffs) == _MAX_HANDOFFS_PER_TICKET
    # Newest first: the long offset=100 comment is first, truncated to the cap.
    assert len(item.context.handoffs[0]) == _HANDOFF_CHARS
    assert item.context.handoffs[1] == "[HANDOFF step 6]"


# ---------------------------------------------------------------------------
# History-state-first re-rank
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_state_first_ranking(
    mem_session: AsyncSession, env: Env
) -> None:
    # PH-2 (open) and PH-3 (done) are BOTH plain references → equal score; the
    # done one must sort FIRST (history bias).
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="see PH-2 and PH-3"
    )
    open_cand = _make_ticket(
        env.board_ph, env.admin, "PH-2", state="in_progress", updated_offset=5
    )
    done_cand = _make_ticket(
        env.board_ph, env.admin, "PH-3", state="done", updated_offset=1
    )
    mem_session.add_all([src, open_cand, done_cand])
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="PH-1")
    keys = [i.key for i in out]
    assert keys.index("PH-3") < keys.index("PH-2"), keys
    assert _by_key(out, "PH-2").score == _by_key(out, "PH-3").score


# ---------------------------------------------------------------------------
# TOPIC path — search seeds (+ pure search-hit fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topic_path_search_seeds(
    mem_session: AsyncSession, env: Env
) -> None:
    # A topic-only ticket with NO graph edges: it surfaces as a pure search hit
    # (score 0.0, reasons []).
    hit = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        title="relationship scoring service",
        state="done",
        technical_depth="## Design decisions\nweighted multi-signal model",
    )
    mem_session.add(hit)
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="relationship scoring")
    item = _by_key(out, "PH-1")
    assert item.score == 0.0
    assert item.reasons == []
    # Context still extracted for a pure search hit.
    assert item.context.summary_source == "technical_depth"
    assert "weighted multi-signal model" in item.context.summary


@pytest.mark.asyncio
async def test_topic_path_expands_seed_graph(
    mem_session: AsyncSession, env: Env
) -> None:
    # Seed matches the topic text; PH-2 is graph-related to the seed (dependency)
    # → it must appear WITH a non-zero score even though it doesn't match the text.
    seed = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        title="widget rendering",
        description="Depends on: PH-2",
        updated_offset=10,
    )
    related = _make_ticket(env.board_ph, env.admin, "PH-2", title="unrelated text")
    mem_session.add_all([seed, related])
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="widget")
    keys = {i.key for i in out}
    assert "PH-1" in keys  # the text seed
    assert "PH-2" in keys  # the graph expansion
    assert _by_key(out, "PH-2").score > 0


@pytest.mark.asyncio
async def test_empty_returns_empty_list(
    mem_session: AsyncSession, env: Env
) -> None:
    out = await recall_context(mem_session, env.admin, query="nothingmatchesthis")
    assert out == []


# ---------------------------------------------------------------------------
# Read-gate (delegated to the reused seams)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_gate_denies_stranger_ticket_path(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")
    mem_session.add(src)
    await mem_session.commit()
    with pytest.raises(PermissionDenied):
        await recall_context(mem_session, env.stranger, query="PH-1")


@pytest.mark.asyncio
async def test_read_gate_denies_stranger_topic_path(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", title="topic seed")
    mem_session.add(src)
    await mem_session.commit()
    with pytest.raises(PermissionDenied):
        await recall_context(mem_session, env.stranger, query="topic")


@pytest.mark.asyncio
async def test_read_gate_allows_pm(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", title="topic seed")
    mem_session.add(src)
    await mem_session.commit()
    # pm holds ticket.read in defaults → no raise.
    out = await recall_context(mem_session, env.pm, query="topic")
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# Cross-board
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_board_other_board_included(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: KIM-9"
    )
    other = _make_ticket(env.board_kim, env.admin, "KIM-9", state="done")
    mem_session.add_all([src, other])
    await mem_session.commit()

    out = await recall_context(mem_session, env.admin, query="PH-1")
    assert "KIM-9" in {i.key for i in out}
    assert _by_key(out, "KIM-9").board == "KIM"


# ---------------------------------------------------------------------------
# Dispatch arm + tools/list sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_arm_returns_list_shape(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    cand = _make_ticket(
        env.board_ph, env.admin, "PH-2", state="done", technical_depth="## Approach\nx"
    )
    mem_session.add_all([src, cand])
    await mem_session.flush()
    _add_comment(
        mem_session, cand, env.admin, "[HANDOFF qa→done] tests green", offset=1
    )
    await mem_session.commit()

    result = await _dispatch_tool(
        "recall_context",
        {"query": "PH-1", "limit": 10},
        env.admin,
        mem_session,
    )
    assert isinstance(result, list)
    assert result[0]["key"] == "PH-2"
    assert result[0]["context"]["handoffs"] == ["[HANDOFF qa→done] tests green"]
    assert result[0]["context"]["summary_source"] == "technical_depth"


@pytest.mark.asyncio
async def test_dispatch_arm_surfaces_not_found(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await _dispatch_tool(
            "recall_context", {"query": "PH-999"}, env.admin, mem_session
        )


def test_tool_registered_in_tools_list() -> None:
    names = {t["name"] for t in _build_mcp_tool_list()}
    assert "recall_context" in names
    entry = next(t for t in _build_mcp_tool_list() if t["name"] == "recall_context")
    props = entry["inputSchema"]["properties"]
    assert {"query", "limit"} <= set(props)


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs N recalled tickets
# ---------------------------------------------------------------------------


class _StatementCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


async def _count_stmts(sync_engine, coro_factory) -> tuple[int, object]:  # type: ignore[no-untyped-def]
    counter = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter)
    try:
        out = await coro_factory()
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter)
    return counter.count, out


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    """recall_context's OWN query budget (context assembly: row-load + comment
    batch) is CONSTANT regardless of how many tickets are recalled.

    We measure recall's overhead AS A DELTA over the reused ``related_tickets``
    seam (whose internal statement count is its own contract, proven in
    test_relationships): ``recall - related`` must be identical at N=1 and N=21,
    i.e. context extraction adds a FIXED number of batched queries (no per-ticket
    comment query, no per-ticket row load).

    src explicitly DEPENDS ON each candidate (a dependency edge that does NOT
    hub-suppress as the set grows). Each candidate has a comment so the batched
    comment query has real work.
    """
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    mem_session.add(src)
    await mem_session.flush()
    cand2 = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add(cand2)
    await mem_session.flush()
    _add_comment(mem_session, cand2, env.admin, "[HANDOFF a→b] x", offset=1)
    await mem_session.commit()

    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    related_1, _ = await _count_stmts(
        sync_engine, lambda: related_tickets(mem_session, env.admin, ticket="PH-1", limit=100)
    )
    recall_1, _ = await _count_stmts(
        sync_engine, lambda: recall_context(mem_session, env.admin, query="PH-1", limit=100)
    )

    keys = ", ".join(f"PH-{i}" for i in range(3, 23))
    src.description = f"Depends on: PH-2, {keys}"
    mem_session.add(src)
    for i in range(3, 23):
        c = _make_ticket(env.board_ph, env.admin, f"PH-{i}")
        mem_session.add(c)
        await mem_session.flush()
        _add_comment(mem_session, c, env.admin, f"[HANDOFF a→b] {i}", offset=i)
    await mem_session.commit()

    related_n, _ = await _count_stmts(
        sync_engine, lambda: related_tickets(mem_session, env.admin, ticket="PH-1", limit=100)
    )
    recall_n, out = await _count_stmts(
        sync_engine, lambda: recall_context(mem_session, env.admin, query="PH-1", limit=100)
    )

    assert len(out) == 21
    # recall's OWN added budget (row-load + comment batch) is constant: the delta
    # over the reused seam does not grow with the recalled-ticket count.
    assert (recall_1 - related_1) == (recall_n - related_n), (
        f"recall added {recall_1 - related_1} stmts at N=1 but "
        f"{recall_n - related_n} at N=21 — context assembly is N+1"
    )
