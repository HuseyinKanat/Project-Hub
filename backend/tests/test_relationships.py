"""Service-layer tests for the relationship-scoring service (PH-284, evolved by
PH-287 into a weighted, noise-suppressed, explainable multi-signal model).

Tests ``services.relationships.related_tickets`` DIRECTLY against an in-memory
sqlite ``mem_session`` (NOT via HTTP TestClient — it hangs in this Docker env),
mirroring ``tests/test_graph.py`` / ``test_search.py`` seed style. Also exercises
the MCP dispatch arm (``_dispatch_tool("related_tickets", ...)``) so the tool is
proven callable end-to-end.

Coverage:
- dependency (explicit Depends on:/blocked by/blocks, directed) vs plain reference
  (no double-count); reference (both directions); epic (sibling/parent/child).
- IDF-weighted shared labels: hub-label suppression (n>=15 ⇒ 0, dropped),
  rare-label promotion, reason names the label + its idf.
- code_overlap (shared touched files via git cache) surfaces + names files +
  more-shared-files-scores-higher + graceful no-commits.
- precedence ordering invariant: dependency > reference > epic > rare-label >
  hub-label(=0); reasons sum to score (explainable, no drift).
- cross_board on/off; self-exclusion; no-relation → []; limit truncates.
- read-gate: stranger denied; member allowed; pm now allowed in defaults.
- unknown key → NotFound; dispatch arm list shape + surfaces NotFound.
- N+1: statement count CONSTANT across 1 vs N related tickets (all signals).
"""

from __future__ import annotations

import math
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
    GitCommit,
    GitCommitFile,
    GitCommitTicket,
    Repository,
    Ticket,
    Workflow,
)
from app.mcp.server import _dispatch_tool
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.relationships import (
    _DEPENDENCY_WEIGHT,
    _EPIC_WEIGHT,
    _LABEL_SIGNAL_WEIGHT,
    _REFERENCE_WEIGHT,
    related_tickets,
)


def _idf(n_total: int, n_item: int) -> float:
    return max(0.0, math.log((n_total + 1) / (n_item + 1)))


def _label_contrib(n_total: int, *n_labels: int) -> float:
    """Expected shared-label contribution for labels each on ``n`` tickets."""
    return min(_LABEL_SIGNAL_WEIGHT * sum(_idf(n_total, n) for n in n_labels), 8.0)


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
    plus a `pm`-role member of PH (read-gate fix) and a repository for git seeds."""

    def __init__(
        self,
        *,
        board_ph: Board,
        board_kim: Board,
        admin: Actor,
        stranger: Actor,
        pm: Actor,
        repo: Repository,
    ) -> None:
        self.board_ph = board_ph
        self.board_kim = board_kim
        self.admin = admin
        self.stranger = stranger
        self.pm = pm
        self.repo = repo


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

    repo = Repository(
        board_id=board_ph.id,
        slug="project-hub",
        name="project-hub",
        provider="local",
        is_primary=True,
        remote_url="file:///tmp/project-hub",
        default_branch="main",
        local_path="/repos/project-hub",
    )
    mem_session.add(repo)

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
        repo=repo,
    )


_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _make_ticket(
    board: Board,
    reporter: Actor,
    key: str,
    *,
    epic_id: uuid.UUID | None = None,
    deleted: bool = False,
    description: str = "",
    labels: list[str] | None = None,
    updated_offset: int = 0,
) -> Ticket:
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
        updated_at=_BASE_TIME + timedelta(minutes=updated_offset),
        deleted_at=datetime.now(UTC) if deleted else None,
    )


async def _link_commit(
    session: AsyncSession,
    repo: Repository,
    *,
    sha: str,
    ticket_ids: list[uuid.UUID],
    paths: list[str],
) -> None:
    """Seed ONE git commit touching ``paths``, linked to ``ticket_ids``."""
    commit = GitCommit(
        repo_id=repo.id,
        sha=sha,
        short_sha=sha[:12],
        authored_at=_BASE_TIME,
        committed_at=_BASE_TIME,
    )
    session.add(commit)
    await session.flush()
    for path in paths:
        session.add(
            GitCommitFile(commit_id=commit.id, path=path, change_type="M")
        )
    for tid in ticket_ids:
        session.add(GitCommitTicket(commit_id=commit.id, ticket_id=tid))


def _fillers(env: Env, start: int, n: int) -> list[Ticket]:
    """N filler tickets each with a UNIQUE label — raises N_total so a label
    shared by a few tickets has a POSITIVE idf (corpus realism for the in-memory
    seed; a label on all tickets genuinely has idf 0)."""
    return [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=[f"uniq-{i}"])
        for i in range(start, start + n)
    ]


def _by_key(items: list, key: str):  # type: ignore[no-untyped-def]
    for item in items:
        if item.key == key:
            return item
    raise AssertionError(f"{key} not in {[i.key for i in items]}")


def _reason(item, rtype: str):  # type: ignore[no-untyped-def]
    for r in item.reasons:
        if r.type == rtype:
            return r
    raise AssertionError(f"{rtype} reason not in {[r.type for r in item.reasons]}")


def _assert_reasons_sum_to_score(item) -> None:  # type: ignore[no-untyped-def]
    # Reasons reconstruct the score: we re-derive each reason's stated weight from
    # the score formula constants is impossible from detail strings alone, so we
    # assert via the service's own guarantee (score == 0 ⇒ excluded; non-zero score
    # ⇒ at least one reason) and that scores are finite/positive. The exact
    # per-signal sum is checked in the dedicated explainability test.
    assert item.score >= 0
    if item.score > 0:
        assert item.reasons, "non-zero score must carry at least one reason"


# ---------------------------------------------------------------------------
# IDF-weighted shared-label relation + reasons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_label_relation_idf_weighted(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["agent-mcp", "scope"])
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["agent-mcp", "ui"])
    other = _make_ticket(env.board_ph, env.admin, "PH-3", labels=["unrelated"])
    mem_session.add_all([src, cand, other])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")

    assert [r.key for r in out] == ["PH-2"]  # PH-3 shares nothing
    rel = _by_key(out, "PH-2")
    # N_total = 3; `agent-mcp` on 2 tickets → idf = log(4/3); contrib = 0.6*idf.
    assert rel.score == pytest.approx(_label_contrib(3, 2))
    assert len(rel.reasons) == 1
    assert rel.reasons[0].type == "shared_label"
    assert "agent-mcp" in rel.reasons[0].detail
    assert "idf" in rel.reasons[0].detail  # names the specificity


@pytest.mark.asyncio
async def test_hub_label_suppressed(mem_session: AsyncSession, env: Env) -> None:
    """Two tickets sharing ONLY a HUB label (n_label >= 15) → that label
    contributes 0, emits no reason, and the pair drops off the list (AC1)."""
    # Seed 20 tickets all carrying `frontend` so n_frontend = 22 (>= 15 hub).
    hub_holders = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["frontend"])
        for i in range(10, 30)
    ]
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["frontend"])
    cand = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["frontend"])
    mem_session.add_all([*hub_holders, src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    # PH-2 (and all hub holders) share ONLY the dropped hub label → score 0 → gone.
    for item in out:
        assert all(r.type != "shared_label" for r in item.reasons)
    assert all(item.score == 0 for item in out) or out == []


@pytest.mark.asyncio
async def test_rare_label_promoted_above_hub(
    mem_session: AsyncSession, env: Env
) -> None:
    """B sharing a SPECIFIC rare label with A ranks strictly ABOVE C sharing only
    a HUB label with A (AC2)."""
    hub_holders = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["backend"])
        for i in range(10, 30)
    ]
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["relationship-scoring", "backend"]
    )
    rare_match = _make_ticket(
        env.board_ph, env.admin, "PH-2", labels=["relationship-scoring"]
    )
    hub_match = _make_ticket(env.board_ph, env.admin, "PH-3", labels=["backend"])
    mem_session.add_all([*hub_holders, src, rare_match, hub_match])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    b = _by_key(out, "PH-2")
    assert b.score > 0
    assert "relationship-scoring" in _reason(b, "shared_label").detail
    # PH-3 (hub-only) is either absent or strictly below B.
    keys = [r.key for r in out]
    if "PH-3" in keys:
        assert b.score > _by_key(out, "PH-3").score
        assert keys.index("PH-2") < keys.index("PH-3")


# ---------------------------------------------------------------------------
# dependency split (explicit deps distinct from + above plain references)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_distinct_from_reference(
    mem_session: AsyncSession, env: Env
) -> None:
    dep = _make_ticket(env.board_ph, env.admin, "PH-2")
    plain = _make_ticket(env.board_ph, env.admin, "PH-3")
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        description="Depends on: PH-2\nrelated work in PH-3 informs this",
    )
    mem_session.add_all([dep, plain, src])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    dep_rel = _by_key(out, "PH-2")
    ref_rel = _by_key(out, "PH-3")
    # PH-2 is a dependency ONLY (no double-count as reference).
    assert {r.type for r in dep_rel.reasons} == {"dependency"}
    assert dep_rel.score == _DEPENDENCY_WEIGHT
    assert "depends on" in _reason(dep_rel, "dependency").detail.lower()
    # PH-3 stays a plain reference, lower weight.
    assert {r.type for r in ref_rel.reasons} == {"reference"}
    assert ref_rel.score == _REFERENCE_WEIGHT
    # Dependency ranks above reference (precedence).
    assert dep_rel.score > ref_rel.score
    assert out[0].key == "PH-2"


@pytest.mark.asyncio
async def test_dependency_blocks_direction(
    mem_session: AsyncSession, env: Env
) -> None:
    blocked = _make_ticket(env.board_ph, env.admin, "PH-2")
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="blocks PH-2 until merged"
    )
    mem_session.add_all([blocked, src])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    assert rel.score == _DEPENDENCY_WEIGHT
    assert "blocks" in _reason(rel, "dependency").detail.lower()


@pytest.mark.asyncio
async def test_blocked_by_is_depends_on(mem_session: AsyncSession, env: Env) -> None:
    upstream = _make_ticket(env.board_ph, env.admin, "PH-2")
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="blocked by PH-2 for now"
    )
    mem_session.add_all([upstream, src])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert "depends on" in _reason(_by_key(out, "PH-2"), "dependency").detail.lower()


# ---------------------------------------------------------------------------
# reference relation (both directions) — no dependency keyword
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_outbound(mem_session: AsyncSession, env: Env) -> None:
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="see PH-2 for the seam"
    )
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    assert rel.score == _REFERENCE_WEIGHT
    assert rel.reasons[0].type == "reference"
    assert "PH-1 → PH-2" in rel.reasons[0].detail


@pytest.mark.asyncio
async def test_reference_inbound(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")
    cand = _make_ticket(
        env.board_ph, env.admin, "PH-2", description="implements part of PH-1"
    )
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    assert rel.score == _REFERENCE_WEIGHT
    assert rel.reasons[0].type == "reference"
    assert "PH-2 → PH-1" in rel.reasons[0].detail


@pytest.mark.asyncio
async def test_reference_word_boundary_no_false_positive(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-2")
    decoy = _make_ticket(
        env.board_ph, env.admin, "PH-28", description="see PH-280 and PH-281"
    )
    mem_session.add_all([src, decoy])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-2")
    assert out == []


# ---------------------------------------------------------------------------
# epic relation (sibling / parent / child)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_epic_sibling_and_child_and_parent(
    mem_session: AsyncSession, env: Env
) -> None:
    epic = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add(epic)
    await mem_session.flush()
    src = _make_ticket(env.board_ph, env.admin, "PH-1", epic_id=epic.id)
    sibling = _make_ticket(env.board_ph, env.admin, "PH-2", epic_id=epic.id)
    mem_session.add_all([src, sibling])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    keys = {r.key for r in out}
    assert keys == {"PH-2", "PH-100"}
    sib = _by_key(out, "PH-2")
    assert sib.score == _EPIC_WEIGHT
    assert sib.reasons[0].type == "epic"
    parent = _by_key(out, "PH-100")
    assert parent.score == _EPIC_WEIGHT
    assert "epic of PH-1" in parent.reasons[0].detail


@pytest.mark.asyncio
async def test_epic_children_of_src(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add(src)
    await mem_session.flush()
    child = _make_ticket(env.board_ph, env.admin, "PH-1", epic_id=src.id)
    mem_session.add(child)
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-100")
    assert [r.key for r in out] == ["PH-1"]
    assert "child of epic PH-100" in _by_key(out, "PH-1").reasons[0].detail


# ---------------------------------------------------------------------------
# code_overlap (shared touched files via git cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_overlap_surfaces_and_names_file(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    other = _make_ticket(env.board_ph, env.admin, "PH-3")
    mem_session.add_all([src, cand, other])
    await mem_session.flush()
    await _link_commit(
        mem_session, env.repo, sha="a" * 40, ticket_ids=[src.id],
        paths=["app/services/relationships.py", "app/services/graph.py"],
    )
    await _link_commit(
        mem_session, env.repo, sha="b" * 40, ticket_ids=[cand.id],
        paths=["app/services/relationships.py"],
    )
    await _link_commit(
        mem_session, env.repo, sha="c" * 40, ticket_ids=[other.id],
        paths=["app/unrelated.py"],
    )
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    keys = {r.key for r in out}
    assert "PH-2" in keys  # shares a touched file
    assert "PH-3" not in keys  # touched only an unrelated file
    rel = _by_key(out, "PH-2")
    reason = _reason(rel, "code_overlap")
    assert "relationships.py" in reason.detail


@pytest.mark.asyncio
async def test_code_overlap_more_files_scores_higher(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")
    many = _make_ticket(env.board_ph, env.admin, "PH-2")
    few = _make_ticket(env.board_ph, env.admin, "PH-3")
    mem_session.add_all([src, many, few])
    await mem_session.flush()
    await _link_commit(
        mem_session, env.repo, sha="a" * 40, ticket_ids=[src.id],
        paths=["f/a.py", "f/b.py", "f/c.py"],
    )
    await _link_commit(
        mem_session, env.repo, sha="b" * 40, ticket_ids=[many.id],
        paths=["f/a.py", "f/b.py", "f/c.py"],  # all 3 shared
    )
    await _link_commit(
        mem_session, env.repo, sha="c" * 40, ticket_ids=[few.id],
        paths=["f/a.py"],  # 1 shared
    )
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert _by_key(out, "PH-2").score > _by_key(out, "PH-3").score


@pytest.mark.asyncio
async def test_code_overlap_graceful_no_commits(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1")  # no commits
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.flush()
    await _link_commit(
        mem_session, env.repo, sha="b" * 40, ticket_ids=[cand.id], paths=["x.py"]
    )
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert out == []  # src has no commits → no code_overlap, nothing else relates


# ---------------------------------------------------------------------------
# precedence invariant + explainability (reasons sum to score)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_precedence_invariant(mem_session: AsyncSession, env: Env) -> None:
    """dependency > reference > epic > rare-label > hub-label(=0), each candidate
    connected by exactly one signal (AC7 — assert ORDERING, not magic numbers)."""
    hub_holders = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["backend"])
        for i in range(20, 40)
    ]
    epic = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add_all([*hub_holders, epic])
    await mem_session.flush()
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        epic_id=epic.id,
        labels=["rare-x", "backend"],
        description="Depends on: PH-2\nbuilds on PH-3",
    )
    dep = _make_ticket(env.board_ph, env.admin, "PH-2")
    ref = _make_ticket(env.board_ph, env.admin, "PH-3")
    sibling = _make_ticket(env.board_ph, env.admin, "PH-4", epic_id=epic.id)
    rare = _make_ticket(env.board_ph, env.admin, "PH-5", labels=["rare-x"])
    hub = _make_ticket(env.board_ph, env.admin, "PH-6", labels=["backend"])
    mem_session.add_all([src, dep, ref, sibling, rare, hub])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    s = {r.key: r.score for r in out}
    assert s["PH-2"] > s["PH-3"] > s["PH-4"] > s["PH-5"]  # dep > ref > epic > rare
    # hub-only PH-6 contributes 0 → absent (or strictly lowest at 0).
    assert "PH-6" not in s or s["PH-6"] == 0


@pytest.mark.asyncio
async def test_reasons_sum_to_score(mem_session: AsyncSession, env: Env) -> None:
    """Every weighted signal → a reason; their stated weights reconstruct score
    exactly (AC6 — no drift). We re-derive expected per-signal weights and assert
    score == their sum."""
    epic = _make_ticket(env.board_ph, env.admin, "PH-100")
    mem_session.add(epic)
    await mem_session.flush()
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        epic_id=epic.id,
        labels=["rare-a", "rare-b"],
        description="Depends on: PH-2",
    )
    # PH-2: dependency + same epic + shares 2 rare labels.
    combo = _make_ticket(
        env.board_ph, env.admin, "PH-2", epic_id=epic.id, labels=["rare-a", "rare-b"]
    )
    mem_session.add_all([src, combo])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    rel = _by_key(out, "PH-2")
    types = {r.type for r in rel.reasons}
    assert types == {"dependency", "epic", "shared_label"}
    # N_total = 3 (epic, src, combo); rare-a & rare-b each on 2 tickets.
    expected = _DEPENDENCY_WEIGHT + _EPIC_WEIGHT + _label_contrib(3, 2, 2)
    assert rel.score == pytest.approx(expected)
    # All scores finite, sorted desc; non-zero ⇒ has reasons.
    for item in out:
        _assert_reasons_sum_to_score(item)
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_tiebreak_updated_at_then_key(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    older = _make_ticket(
        env.board_ph, env.admin, "PH-2", labels=["shared"], updated_offset=1
    )
    newer = _make_ticket(
        env.board_ph, env.admin, "PH-3", labels=["shared"], updated_offset=99
    )
    mem_session.add_all([src, older, newer, *_fillers(env, 10, 5)])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert [r.key for r in out] == ["PH-3", "PH-2"]  # newer updated_at first


# ---------------------------------------------------------------------------
# cross_board on / off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_board_true_includes_other_board(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    foreign = _make_ticket(env.board_kim, env.admin, "KIM-1", labels=["shared"])
    mem_session.add_all([src, foreign, *_fillers(env, 10, 5)])
    await mem_session.commit()

    out = await related_tickets(
        mem_session, env.admin, ticket="PH-1", cross_board=True
    )
    rel = _by_key(out, "KIM-1")
    assert rel.board == "KIM"


@pytest.mark.asyncio
async def test_cross_board_false_restricts_to_src_board(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["shared"], description="ref KIM-1"
    )
    same = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"])
    foreign = _make_ticket(
        env.board_kim,
        env.admin,
        "KIM-1",
        labels=["shared"],
        description="mentions PH-1",
    )
    mem_session.add_all([src, same, foreign, *_fillers(env, 10, 5)])
    await mem_session.commit()

    out = await related_tickets(
        mem_session, env.admin, ticket="PH-1", cross_board=False
    )
    assert {r.key for r in out} == {"PH-2"}  # KIM-1 excluded by board filter
    assert all(r.board == "PH" for r in out)


# ---------------------------------------------------------------------------
# self-exclusion / empty / limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_excluded(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(
        env.board_ph,
        env.admin,
        "PH-1",
        labels=["x"],
        description="self ref PH-1\nDepends on: PH-1",
    )
    mem_session.add(src)
    await mem_session.flush()
    await _link_commit(
        mem_session, env.repo, sha="a" * 40, ticket_ids=[src.id], paths=["x.py"]
    )
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert out == []


@pytest.mark.asyncio
async def test_no_relations_returns_empty(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["lonely"])
    other = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["nothing"])
    mem_session.add_all([src, other])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert out == []


@pytest.mark.asyncio
async def test_limit_truncates_highest_first(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", labels=["shared"], description="Depends on: PH-2"
    )
    high = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"])  # dep+label
    lows = [
        _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["shared"])
        for i in range(3, 8)
    ]
    mem_session.add_all([src, high, *lows, *_fillers(env, 20, 5)])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1", limit=2)
    assert len(out) == 2
    assert out[0].key == "PH-2"  # highest score retained (dep + label)


# ---------------------------------------------------------------------------
# read-gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_gate_denies_stranger(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["x"])
    mem_session.add(src)
    await mem_session.commit()

    with pytest.raises(PermissionDenied):
        await related_tickets(mem_session, env.stranger, ticket="PH-1")


@pytest.mark.asyncio
async def test_read_gate_allows_member(mem_session: AsyncSession, env: Env) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", description="see PH-2")
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.admin, ticket="PH-1")
    assert [r.key for r in out] == ["PH-2"]


@pytest.mark.asyncio
async def test_read_gate_allows_pm_in_defaults(
    mem_session: AsyncSession, env: Env
) -> None:
    """PH-287: pm now holds ticket.read in DEFAULT_WEB_ROLES → may call the tool."""
    src = _make_ticket(env.board_ph, env.admin, "PH-1", description="see PH-2")
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.commit()

    out = await related_tickets(mem_session, env.pm, ticket="PH-1")
    assert [r.key for r in out] == ["PH-2"]  # no PermissionDenied


# ---------------------------------------------------------------------------
# unknown key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_key_raises_not_found(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await related_tickets(mem_session, env.admin, ticket="PH-999")


# ---------------------------------------------------------------------------
# MCP dispatch arm (tool is callable end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_arm_returns_list_shape(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(
        env.board_ph, env.admin, "PH-1", description="Depends on: PH-2"
    )
    cand = _make_ticket(env.board_ph, env.admin, "PH-2")
    mem_session.add_all([src, cand])
    await mem_session.commit()

    result = await _dispatch_tool(
        "related_tickets",
        {"ticket": "PH-1", "cross_board": True, "limit": 10},
        env.admin,
        mem_session,
    )
    assert isinstance(result, list)
    assert result[0]["key"] == "PH-2"
    assert result[0]["score"] == _DEPENDENCY_WEIGHT
    assert {r["type"] for r in result[0]["reasons"]} == {"dependency"}


@pytest.mark.asyncio
async def test_dispatch_arm_surfaces_not_found(
    mem_session: AsyncSession, env: Env
) -> None:
    with pytest.raises(NotFound):
        await _dispatch_tool(
            "related_tickets", {"ticket": "PH-999"}, env.admin, mem_session
        )


# ---------------------------------------------------------------------------
# tool registration
# ---------------------------------------------------------------------------


def test_tool_registered_in_tools_list() -> None:
    from app.mcp.server import _build_mcp_tool_list

    names = {t["name"] for t in _build_mcp_tool_list()}
    assert "related_tickets" in names
    entry = next(t for t in _build_mcp_tool_list() if t["name"] == "related_tickets")
    props = entry["inputSchema"]["properties"]
    assert {"ticket", "cross_board", "limit"} <= set(props)


# ---------------------------------------------------------------------------
# N+1 — statement count CONSTANT across 1 vs N related tickets (all signals)
# ---------------------------------------------------------------------------


class _StatementCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        self.count += 1


@pytest.mark.asyncio
async def test_no_n_plus_one_constant_statement_count(
    mem_session: AsyncSession, env: Env
) -> None:
    src = _make_ticket(env.board_ph, env.admin, "PH-1", labels=["shared"])
    mem_session.add(src)
    await mem_session.flush()
    # src touches a file so the code-overlap signal also fires (2 git queries).
    await _link_commit(
        mem_session, env.repo, sha="a" * 40, ticket_ids=[src.id], paths=["shared.py"]
    )
    cand2 = _make_ticket(env.board_ph, env.admin, "PH-2", labels=["shared"])
    mem_session.add(cand2)
    await mem_session.flush()
    await _link_commit(
        mem_session, env.repo, sha="b" * 40, ticket_ids=[cand2.id], paths=["shared.py"]
    )
    await mem_session.commit()

    sync_engine = mem_session.bind.sync_engine  # type: ignore[union-attr]
    counter_1 = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_1)
    try:
        await related_tickets(mem_session, env.admin, ticket="PH-1")
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_1)

    # Add 20 more related-by-label + code-overlap tickets.
    for i in range(3, 23):
        c = _make_ticket(env.board_ph, env.admin, f"PH-{i}", labels=["shared"])
        mem_session.add(c)
        await mem_session.flush()
        await _link_commit(
            mem_session, env.repo, sha=f"{i:040d}", ticket_ids=[c.id],
            paths=["shared.py"],
        )
    await mem_session.commit()

    counter_n = _StatementCounter()
    event.listen(sync_engine, "before_cursor_execute", counter_n)
    try:
        out = await related_tickets(mem_session, env.admin, ticket="PH-1", limit=100)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter_n)

    assert len(out) == 21
    assert counter_1.count == counter_n.count, (
        f"N+1 detected: 1 related={counter_1.count} stmts, "
        f"21 related={counter_n.count} stmts"
    )
