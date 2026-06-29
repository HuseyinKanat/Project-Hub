"""Tests for CLI commands — update_board_roles, create_jarwis_actors, create_board."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    JARWIS_MODE_ROLES,
    JARWIS_SHARED_ROLES,
    create_board,
    create_jarwis_actors,
    jarwis_roles_for_mode,
    update_board_roles,
)
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


async def _make_board_with_roles(session: AsyncSession, roles: object) -> Board:
    """Insert a Workflow + Board with given roles, return the Board."""
    workflow = Workflow(
        name="CLI Test Workflow",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()

    board = Board(
        key="CLT",
        name="CLI Test Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=roles,
    )
    session.add(board)
    await session.commit()
    return board


@pytest.mark.asyncio
async def test_update_board_roles_dirty_becomes_default(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A board with non-default roles is updated to DEFAULT_WEB_ROLES in the real DB."""
    board = await _make_board_with_roles(db_session, {"foo": "bar"})
    board_id = board.id

    await update_board_roles(db_session)
    # Caller owns commit: flush + expire to verify DB-level persistence.
    await db_session.flush()
    db_session.expire_all()

    # Reload from DB to verify flag_modified took effect.
    reloaded = (
        await db_session.execute(select(Board).where(Board.id == board_id))
    ).scalar_one()
    assert reloaded.roles == DEFAULT_WEB_ROLES

    captured = capsys.readouterr()
    assert "Updated 1 board(s), 0 unchanged." in captured.out


@pytest.mark.asyncio
async def test_update_board_roles_idempotent(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First call updates dirty board; second call on already-correct board is unchanged."""
    await _make_board_with_roles(db_session, {"dirty": True})

    # First call: dirty → updated=1
    await update_board_roles(db_session)
    out1 = capsys.readouterr().out
    assert "Updated 1 board(s), 0 unchanged." in out1

    # Flush so second call sees updated roles in the same session.
    await db_session.flush()

    # Second call: board is now DEFAULT_WEB_ROLES → updated=0, unchanged=1
    await update_board_roles(db_session)
    out2 = capsys.readouterr().out
    assert "Updated 0 board(s), 1 unchanged." in out2


@pytest.mark.asyncio
async def test_update_board_roles_no_boards(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty DB (no boards) → Updated 0 board(s), 0 unchanged."""
    await update_board_roles(db_session)

    captured = capsys.readouterr()
    assert "Updated 0 board(s), 0 unchanged." in captured.out


# --- create_jarwis_actors -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_jarwis_actors_mints_six_role_actors(
    db_session: AsyncSession,
) -> None:
    """First call provisions one actor per jarwis_roles_for_mode("web"), mints a token for each,
    and creates a board membership with the matching role."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    tokens = await create_jarwis_actors(board.key, session=db_session)

    # 6 plain tokens returned (one per role)
    assert set(tokens.keys()) == set(jarwis_roles_for_mode("web"))
    assert all(len(t) == 48 for t in tokens.values())  # secrets.token_hex(24) = 48 hex chars

    # Each actor exists with the expected display_name. Naming convention:
    # backend_dev / frontend_dev drop "_dev" (web shortcut), others kebab-case.
    def _expected_name(role: str) -> str:
        if role in {"backend_dev", "frontend_dev"}:
            return f"jarwis-{role.removesuffix('_dev')}"
        return f"jarwis-{role.replace('_', '-')}"

    for role in jarwis_roles_for_mode("web"):
        actor_name = _expected_name(role)
        actor = (
            await db_session.execute(select(Actor).where(Actor.display_name == actor_name))
        ).scalar_one_or_none()
        assert actor is not None, f"actor {actor_name} not created"
        assert actor.kind == "agent"
        assert actor.agent_role_hint == role

        membership = (
            await db_session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == actor.id,
                )
            )
        ).scalar_one_or_none()
        assert membership is not None, f"membership for {actor_name} not created"
        assert membership.role == role


@pytest.mark.asyncio
async def test_create_jarwis_actors_idempotent_without_rotate(
    db_session: AsyncSession,
) -> None:
    """Second call with rotate=False does not re-mint tokens for existing actors."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    first = await create_jarwis_actors(board.key, session=db_session)
    assert all(first.values()), "first call should mint all tokens"

    second = await create_jarwis_actors(board.key, session=db_session)
    # All slots present but empty strings (placeholder: actor existed, no token)
    assert set(second.keys()) == set(jarwis_roles_for_mode("web"))
    assert all(v == "" for v in second.values())


@pytest.mark.asyncio
async def test_create_jarwis_actors_rotate_remints_tokens(
    db_session: AsyncSession,
) -> None:
    """rotate=True re-mints tokens for all existing actors."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    first = await create_jarwis_actors(board.key, session=db_session)
    rotated = await create_jarwis_actors(board.key, session=db_session, rotate=True)

    # All roles got fresh tokens, and they differ from the first batch
    assert all(rotated[r] and rotated[r] != first[r] for r in jarwis_roles_for_mode("web"))


@pytest.mark.asyncio
async def test_create_jarwis_actors_missing_board(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Board not found → returns empty dict, prints warning, no actors created."""
    tokens = await create_jarwis_actors("NOSUCH", session=db_session)
    assert tokens == {}
    captured = capsys.readouterr()
    assert "not found" in captured.out


@pytest.mark.asyncio
async def test_create_jarwis_actors_unity_mode_provisions_unity_roles(
    db_session: AsyncSession,
) -> None:
    """mode='unity' skips backend_dev/frontend_dev, provisions
    unity_dev + unity_scene_manager instead. Shared roles
    (pm, architect, reviewer, qa) are always present."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    tokens = await create_jarwis_actors(board.key, mode="unity", session=db_session)

    expected = set(JARWIS_SHARED_ROLES) | set(JARWIS_MODE_ROLES["unity"])
    assert set(tokens.keys()) == expected
    assert "backend_dev" not in tokens
    assert "frontend_dev" not in tokens

    def _expected_name(role: str) -> str:
        if role in {"backend_dev", "frontend_dev"}:
            return f"jarwis-{role.removesuffix('_dev')}"
        return f"jarwis-{role.replace('_', '-')}"

    # Membership rows match
    for role in expected:
        actor_name = _expected_name(role)
        actor = (
            await db_session.execute(select(Actor).where(Actor.display_name == actor_name))
        ).scalar_one()
        membership = (
            await db_session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == actor.id,
                )
            )
        ).scalar_one()
        assert membership.role == role


@pytest.mark.asyncio
async def test_create_jarwis_actors_ml_mode_provisions_ml_roles(
    db_session: AsyncSession,
) -> None:
    """mode='ml' skips backend_dev/frontend_dev, provisions the four ML
    implementer roles (data_engineer, data_labeler, ml_engineer, ml_analyst)
    instead. Shared roles (pm, architect, reviewer, qa) are always present."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    tokens = await create_jarwis_actors(board.key, mode="ml", session=db_session)

    expected = set(JARWIS_SHARED_ROLES) | set(JARWIS_MODE_ROLES["ml"])
    assert set(tokens.keys()) == expected
    assert "backend_dev" not in tokens
    assert "frontend_dev" not in tokens

    # Membership rows match (ml role names have no _dev suffix → simple replace)
    for role in expected:
        actor_name = f"jarwis-{role.removesuffix('_dev').replace('_', '-')}"
        actor = (
            await db_session.execute(select(Actor).where(Actor.display_name == actor_name))
        ).scalar_one()
        membership = (
            await db_session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == actor.id,
                )
            )
        ).scalar_one()
        assert membership.role == role


def test_jarwis_roles_for_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown jarwis mode"):
        jarwis_roles_for_mode("nonsense")


# --- create_board -------------------------------------------------------------


async def _ensure_admin(session: AsyncSession) -> Actor:
    admin = (
        await session.execute(select(Actor).where(Actor.kind == "human"))
    ).scalar_one_or_none()
    if admin is None:
        admin = Actor(
            kind="human",
            display_name="Admin",
            token_hash="x" * 64,
            is_active=True,
        )
        session.add(admin)
        await session.flush()
    return admin


@pytest.mark.asyncio
async def test_create_board_creates_with_default_workflow_and_roles(
    db_session: AsyncSession,
) -> None:
    """Fresh board creation seeds default workflow, DEFAULT_WEB_ROLES, and admin membership."""
    await _ensure_admin(db_session)

    result = await create_board("MA", "MyApp", session=db_session)

    assert result["status"] == "created"
    assert result["key"] == "MA"

    board = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalar_one()
    assert board.name == "MyApp"
    assert board.roles == DEFAULT_WEB_ROLES
    assert board.project_type == "web_app"

    # Workflow created and linked
    workflow = (
        await db_session.execute(select(Workflow).where(Workflow.id == board.workflow_id))
    ).scalar_one()
    assert workflow.is_default is True

    # Admin got membership
    membership = (
        await db_session.execute(
            select(BoardMembership).where(BoardMembership.board_id == board.id)
        )
    ).scalar_one()
    assert membership.role == "admin"


@pytest.mark.asyncio
async def test_create_board_is_idempotent_on_key(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Second call with same key returns existing board, does not duplicate."""
    await _ensure_admin(db_session)

    first = await create_board("MA", "MyApp", session=db_session)
    second = await create_board("MA", "MyAppRenamed", session=db_session)

    assert first["id"] == second["id"]
    assert second["status"] == "existing"

    # Only one board with that key
    boards = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalars().all()
    assert len(list(boards)) == 1


@pytest.mark.asyncio
async def test_create_board_uppercases_key(
    db_session: AsyncSession,
) -> None:
    """Lowercase key is normalized to uppercase."""
    await _ensure_admin(db_session)

    result = await create_board("shop", "Shop Backend", session=db_session)

    assert result["key"] == "SHOP"
    board = (
        await db_session.execute(select(Board).where(Board.key == "SHOP"))
    ).scalar_one()
    assert board.name == "Shop Backend"
