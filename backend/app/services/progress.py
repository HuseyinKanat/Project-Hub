"""Epic-progress rollup (PH-335) — read-only, derived from child-ticket state.

No migration, no new table: the rollup is computed on the fly from the tickets
already grouped by the ``epic_id`` self-FK. Uses ONE board-wide column-select +
in-memory group-by, so the query count is O(1) in the number of epics (NO N+1 —
there is no per-epic query). Mirrors the aggregation shape of
``relationships._epic_candidates`` (``deleted_at IS NULL`` + board-scope) but
returns computed buckets rather than ORM rows.

``done`` is derived from the board's WORKFLOW (states whose ``category == "done"``),
never a hardcoded ``"done"`` literal — so a board that renames its terminal state
still rolls up correctly (mirrors ``defaults.initial_state``'s state scan).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_board_member
from app.db.models import Actor, Board, Ticket
from app.schemas import EpicProgressItem, EpicProgressResponse, ProgressBucket
from app.services.boards import get_board


@dataclass(frozen=True)
class _Row:
    """A lightweight projection of the columns the rollup needs (no ORM load)."""

    id: UUID
    key: str
    title: str
    type: str
    state: str
    epic_id: UUID | None
    story_points: int | None


def _done_state_names(board: Board) -> set[str]:
    """State names whose workflow ``category`` is ``"done"`` (NOT a literal string).

    ``board.workflow.states`` is a JSON list of ``{name, category, ...}`` dicts
    eager-loaded for free by ``get_board`` (``selectinload(Board.workflow)``). The
    default workflow has exactly one done-category state (``done``), but reading the
    category keeps this correct if a board renames it (robust done-detection, AC3).
    """
    return {str(s["name"]) for s in board.workflow.states if s.get("category") == "done"}


def _weighted_pct(children: list[_Row], done_states: set[str], done: int, total: int) -> float:
    """Percent-complete over a bucket's children (single rule, no knobs — AC2).

    IF every child carries ``story_points`` -> story-point-weighted
    (``100 * Σsp(done) / Σsp(all)``); ELSE (any child unpointed, i.e. mixed/partial
    points) -> count-based (``100 * done / total``). A child-less bucket (or an
    all-zero-points bucket) yields ``0.0`` / the count value with NO div-by-zero.
    Weighting only when ALL children are pointed is deliberate: partial points would
    silently weight unpointed children as 0 and distort the percentage.
    """
    if total == 0:
        return 0.0
    sp_all = 0
    sp_done = 0
    for child in children:
        points = child.story_points
        if points is None:
            # A single unpointed child -> the whole bucket falls back to count.
            return 100.0 * done / total
        sp_all += points
        if child.state in done_states:
            sp_done += points
    if sp_all <= 0:
        # All children pointed but total points is 0 -> degenerate; count instead.
        return 100.0 * done / total
    return 100.0 * sp_done / sp_all


def _bucket(children: list[_Row], done_states: set[str]) -> ProgressBucket:
    total = len(children)
    done = sum(1 for child in children if child.state in done_states)
    histogram: dict[str, int] = dict(Counter(child.state for child in children))
    return ProgressBucket(
        done=done,
        total=total,
        weighted_pct=_weighted_pct(children, done_states, done, total),
        state_histogram=histogram,
    )


async def epic_progress(
    session: AsyncSession, *, actor: Actor, board_id: str
) -> EpicProgressResponse:
    """Per-epic progress rollup for a board (read-only).

    Auth ordering mirrors the 5 existing board-scoped gates: unknown board -> 404
    FIRST (``get_board``), resolved-but-non-member -> 403 SECOND
    (``require_board_member``).
    """
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_board_member(actor, board)  # non-member -> 403 SECOND (PH-327)
    done_states = _done_state_names(board)

    # ONE board-wide column-select (scalars only — no ORM load / no selectinload).
    # ``deleted_at IS NULL`` excludes soft-deleted tickets from BOTH numerator and
    # denominator (AC3); board-scope keeps other boards out (AC3).
    result = await session.execute(
        select(
            Ticket.id,
            Ticket.key,
            Ticket.title,
            Ticket.type,
            Ticket.state,
            Ticket.epic_id,
            Ticket.story_points,
        ).where(Ticket.board_id == board.id, Ticket.deleted_at.is_(None))
    )
    rows: list[_Row] = []
    for r in result.all():
        rid, rkey, rtitle, rtype, rstate, repic, rsp = r
        rows.append(
            _Row(
                id=rid,
                key=rkey,
                title=rtitle,
                type=rtype,
                state=rstate,
                epic_id=repic,
                story_points=rsp,
            )
        )

    present_ids = {row.id for row in rows}

    # Group children by their epic (only when the referenced epic is a present,
    # non-deleted row — a child pointing at a soft-deleted/absent epic is NOT
    # grouped here and falls through to ``ungrouped`` below, avoiding a phantom
    # bucket). NO per-epic query — this is the single in-memory pass (AC4).
    children_by_epic: dict[UUID, list[_Row]] = defaultdict(list)
    for row in rows:
        if row.epic_id is not None and row.epic_id in present_ids:
            children_by_epic[row.epic_id].append(row)

    # Epic set = rows that are themselves ``type == "epic"`` UNION rows referenced by
    # some child's ``epic_id`` (and present). The union guarantees a child-less
    # ``type == "epic"`` still shows 0/0 (AC4), and a non-epic parent referenced by a
    # child still surfaces as a bucket (mirrors the mermaid "epic OR referenced").
    referenced = {row.epic_id for row in rows if row.epic_id is not None}
    epic_ids = {row.id for row in rows if row.type == "epic"} | (referenced & present_ids)

    row_by_id = {row.id: row for row in rows}
    epics: list[EpicProgressItem] = []
    for eid in epic_ids:
        epic_row = row_by_id[eid]
        bucket = _bucket(children_by_epic.get(eid, []), done_states)
        epics.append(
            EpicProgressItem(
                epic_id=str(epic_row.id),
                epic_key=epic_row.key,
                epic_title=epic_row.title,
                done=bucket.done,
                total=bucket.total,
                weighted_pct=bucket.weighted_pct,
                state_histogram=bucket.state_histogram,
            )
        )
    epics.sort(key=lambda item: item.epic_key)  # deterministic output order

    # Ungrouped = non-epic rows with no epic (or a dangling/soft-deleted epic ref).
    ungrouped_rows = [
        row
        for row in rows
        if row.type != "epic" and (row.epic_id is None or row.epic_id not in present_ids)
    ]

    # Board rollup = same bucket shape over ALL non-deleted board tickets (cheap
    # in-memory total; satisfies AC5's board-level rollup without a FE recompute).
    return EpicProgressResponse(
        board_id=str(board.id),
        board=_bucket(rows, done_states),
        epics=epics,
        ungrouped=_bucket(ungrouped_rows, done_states),
    )
