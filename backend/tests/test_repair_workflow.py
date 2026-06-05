"""Tests for the `repair_workflow` CLI command (PH-168).

The PH board's active workflow was corrupted by an E2E test that stripped
``allowed_roles`` from the backlog→to_do transition and injected a
``technical_depth`` field_gate, locking the whole pipeline. ``repair_workflow``
restores that single transition to its known-good shape.
"""

import copy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    KNOWN_GOOD_BACKLOG_TO_DO,
    repair_backlog_to_do_transitions,
    repair_workflow,
)
from app.db.models import Board, BoardWorkflow, Workflow
from app.services.boards import get_active_workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# The exact corruption observed on PH ~2026-06-05: allowed_roles gone,
# a technical_depth field_gate injected in its place.
CORRUPT_BACKLOG_TO_DO: dict[str, object] = {
    "from": "backlog",
    "to": "to_do",
    "field_gates": {
        "required_fields": ["technical_depth"],
        "exempt_ticket_types": [],
    },
}


def _corrupt_transitions() -> list[dict[str, object]]:
    """A copy of DEFAULT_TRANSITIONS with backlog→to_do corrupted."""
    transitions = copy.deepcopy(DEFAULT_TRANSITIONS)
    for i, t in enumerate(transitions):
        if t.get("from") == "backlog" and t.get("to") == "to_do":
            transitions[i] = copy.deepcopy(CORRUPT_BACKLOG_TO_DO)
            break
    return transitions


async def _make_board(
    session: AsyncSession,
    transitions: list[dict[str, object]],
    *,
    key: str = "PH",
    junction: bool = False,
) -> Board:
    """Insert a Workflow + Board (optionally with an active BoardWorkflow row)."""
    workflow = Workflow(
        name="Repair Test Workflow",
        states=copy.deepcopy(DEFAULT_STATES),
        transitions=transitions,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()

    board = Board(
        key=key,
        name="Repair Test Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
    )
    session.add(board)
    await session.flush()
    if junction:
        session.add(
            BoardWorkflow(board_id=board.id, workflow_id=workflow.id, is_active=True)
        )
    await session.commit()
    return board


def _backlog_to_do(transitions: list[dict[str, object]]) -> dict[str, object]:
    for t in transitions:
        if t.get("from") == "backlog" and t.get("to") == "to_do":
            return t
    raise AssertionError("backlog->to_do transition missing")


# --- pure helper --------------------------------------------------------------


def test_helper_repairs_corrupt_transition() -> None:
    new_transitions, changed = repair_backlog_to_do_transitions(_corrupt_transitions())
    assert changed is True
    assert _backlog_to_do(new_transitions) == KNOWN_GOOD_BACKLOG_TO_DO
    assert "field_gates" not in _backlog_to_do(new_transitions)


def test_helper_is_idempotent_on_healthy() -> None:
    new_transitions, changed = repair_backlog_to_do_transitions(
        copy.deepcopy(DEFAULT_TRANSITIONS)
    )
    assert changed is False
    assert _backlog_to_do(new_transitions) == KNOWN_GOOD_BACKLOG_TO_DO


def test_helper_leaves_other_transitions_untouched() -> None:
    source = _corrupt_transitions()
    new_transitions, _ = repair_backlog_to_do_transitions(source)
    # Every non-(backlog→to_do) transition is preserved verbatim.
    others_in = [t for t in source if (t.get("from"), t.get("to")) != ("backlog", "to_do")]
    others_out = [
        t for t in new_transitions if (t.get("from"), t.get("to")) != ("backlog", "to_do")
    ]
    assert others_in == others_out
    # Same count overall — nothing added or dropped.
    assert len(new_transitions) == len(source)


def test_helper_returns_new_list_not_mutating_input() -> None:
    source = _corrupt_transitions()
    snapshot = copy.deepcopy(source)
    new_transitions, _ = repair_backlog_to_do_transitions(source)
    assert source == snapshot  # input untouched
    assert new_transitions is not source


def test_helper_raises_when_transition_missing() -> None:
    transitions = [
        t
        for t in copy.deepcopy(DEFAULT_TRANSITIONS)
        if (t.get("from"), t.get("to")) != ("backlog", "to_do")
    ]
    with pytest.raises(ValueError, match="no backlog->to_do transition"):
        repair_backlog_to_do_transitions(transitions)


# --- CLI command (DB-level) ---------------------------------------------------


@pytest.mark.asyncio
async def test_repair_workflow_fixes_corrupt_board(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupted active workflow gets backlog→to_do restored in the real DB."""
    board = await _make_board(db_session, _corrupt_transitions())
    board_id = board.id

    changed = await repair_workflow("PH", session=db_session)
    assert changed is True
    await db_session.flush()
    db_session.expire_all()

    workflow = await get_active_workflow(db_session, board_id)
    transition = _backlog_to_do(workflow.transitions)
    assert transition["allowed_roles"] == ["pm", "architect"]
    assert "field_gates" not in transition

    assert "repaired" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repair_workflow_idempotent(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Second run on an already-healthy board is a no-op with no error."""
    await _make_board(db_session, _corrupt_transitions())

    first = await repair_workflow("PH", session=db_session)
    assert first is True
    capsys.readouterr()
    await db_session.flush()

    second = await repair_workflow("PH", session=db_session)
    assert second is False
    assert "already healthy" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repair_workflow_already_healthy_no_change(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Healthy board reports already healthy and writes nothing."""
    await _make_board(db_session, copy.deepcopy(DEFAULT_TRANSITIONS))

    changed = await repair_workflow("PH", session=db_session)
    assert changed is False
    assert "already healthy" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repair_workflow_does_not_touch_other_transitions(
    db_session: AsyncSession,
) -> None:
    """Only backlog→to_do changes; all other transitions remain byte-for-byte."""
    board = await _make_board(db_session, _corrupt_transitions())
    board_id = board.id
    before = copy.deepcopy(
        (await get_active_workflow(db_session, board_id)).transitions
    )

    await repair_workflow("PH", session=db_session)
    await db_session.flush()
    db_session.expire_all()

    after = (await get_active_workflow(db_session, board_id)).transitions
    others_before = [
        t for t in before if (t.get("from"), t.get("to")) != ("backlog", "to_do")
    ]
    others_after = [
        t for t in after if (t.get("from"), t.get("to")) != ("backlog", "to_do")
    ]
    assert others_before == others_after
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_repair_workflow_repairs_active_junction_workflow(
    db_session: AsyncSession,
) -> None:
    """When the board uses a BoardWorkflow junction, the active one is repaired."""
    board = await _make_board(db_session, _corrupt_transitions(), junction=True)
    board_id = board.id

    changed = await repair_workflow("PH", session=db_session)
    assert changed is True
    await db_session.flush()
    db_session.expire_all()

    workflow = await get_active_workflow(db_session, board_id)
    assert _backlog_to_do(workflow.transitions)["allowed_roles"] == ["pm", "architect"]


@pytest.mark.asyncio
async def test_repair_workflow_missing_board(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown board key returns False, prints not-found, writes nothing."""
    changed = await repair_workflow("NOSUCH", session=db_session)
    assert changed is False
    assert "not found" in capsys.readouterr().out
