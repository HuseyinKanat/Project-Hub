"""Tests for CLI commands — update_board_roles, create_jarwis_actors, create_board."""

import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import (
    JARWIS_MODE_ROLES,
    JARWIS_SHARED_ROLES,
    _jarwis_actor_name,
    _validate_owner_slug,
    create_board,
    create_jarwis_actors,
    jarwis_roles_for_mode,
    main,
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

def test_ml_mode_roles_and_actor_names() -> None:
    """PH-293: the CLI 'ml' choice landed on main in PH-289 but the live-mounted
    working chain lacked it — create_jarwis_actors --mode ml died with 'invalid
    choice' on GXG re-init. Lock the role set and the jarwis-<role> display-name
    derivation the live board relies on, so neither side regresses again."""
    from app.cli import _jarwis_actor_name, jarwis_roles_for_mode

    assert jarwis_roles_for_mode("ml") == [
        "pm", "architect", "reviewer", "qa",
        "data_engineer", "data_labeler", "ml_engineer", "ml_analyst",
    ]
    assert _jarwis_actor_name("data_engineer", "jarwis") == "jarwis-data-engineer"
    assert _jarwis_actor_name("ml_analyst", "jarwis") == "jarwis-ml-analyst"


# --- create_jarwis_actors --owner (PH-317, per-owner namespacing) --------------


def _bare_name(role: str) -> str:
    """Suffix-less jarwis actor name — mirror of _jarwis_actor_name(role, prefix)."""
    if role in {"backend_dev", "frontend_dev"}:
        return f"jarwis-{role.removesuffix('_dev')}"
    return f"jarwis-{role.replace('_', '-')}"


def test_jarwis_actor_name_owner_suffix() -> None:
    """AC-1/AC-2/AC-5: owner appends @<owner>; owner=None (or omitted) is
    byte-identical to the historical 2-arg form (no @ suffix)."""
    # AC-2: byte-identical no-owner — omitted arg and explicit None agree with today
    assert _jarwis_actor_name("pm", "jarwis") == "jarwis-pm"
    assert _jarwis_actor_name("pm", "jarwis", None) == "jarwis-pm"
    assert _jarwis_actor_name("backend_dev", "jarwis") == "jarwis-backend"
    assert _jarwis_actor_name("data_engineer", "jarwis") == "jarwis-data-engineer"
    # AC-1/AC-5: owner-namespaced (incl. _dev shortcut + hyphenated role + hyphen slug)
    assert _jarwis_actor_name("pm", "jarwis", "alice") == "jarwis-pm@alice"
    assert _jarwis_actor_name("backend_dev", "jarwis", "alice") == "jarwis-backend@alice"
    assert (
        _jarwis_actor_name("data_engineer", "jarwis", "team-blue")
        == "jarwis-data-engineer@team-blue"
    )
    # orthogonal to --name-prefix
    assert _jarwis_actor_name("pm", "acme", "bob") == "acme-pm@bob"


@pytest.mark.parametrize("slug", ["alice", "alice2", "a", "team-blue", "a" * 20])
def test_validate_owner_slug_accepts(slug: str) -> None:
    """AC-5: valid slugs (lowercase/digits/hyphen, 1-20, alnum start) pass and
    are returned unchanged."""
    assert _validate_owner_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    ["Alice!", "ALICE", "-alice", "a" * 21, "", "al ice", "ali_ce", "alice@x"],
)
def test_validate_owner_slug_rejects(slug: str) -> None:
    """AC-4: invalid slugs — punctuation / uppercase / leading hyphen / >20 /
    empty / space / underscore / '@' — raise ValueError."""
    with pytest.raises(ValueError, match="invalid owner slug"):
        _validate_owner_slug(slug)


@pytest.mark.asyncio
async def test_create_jarwis_actors_owner_mints_namespaced(
    db_session: AsyncSession,
) -> None:
    """AC-1/AC-7/AC-8: --owner alice mints jarwis-<role>@alice per role, each with
    bare-role membership + bare agent_role_hint; the {role: token} map keys stay
    bare roles (owner never leaks into the map), and an owner-scoped call does NOT
    create the suffix-less set."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    tokens = await create_jarwis_actors(board.key, owner="alice", session=db_session)

    # AC-8: map keys are the bare roles — no "@" anywhere in the map
    assert set(tokens.keys()) == set(jarwis_roles_for_mode("web"))
    assert all("@" not in k for k in tokens)
    assert all(len(t) == 48 for t in tokens.values())  # secrets.token_hex(24)

    for role in jarwis_roles_for_mode("web"):
        actor_name = f"{_bare_name(role)}@alice"
        actor = (
            await db_session.execute(select(Actor).where(Actor.display_name == actor_name))
        ).scalar_one_or_none()
        assert actor is not None, f"actor {actor_name} not created"
        assert actor.kind == "agent"
        # AC-7: agent_role_hint is the BARE role, not the namespaced display_name
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

    # An owner-scoped call must NOT create the suffix-less jarwis-<role> actor.
    bare = (
        await db_session.execute(
            select(Actor).where(Actor.display_name == _bare_name("pm"))
        )
    ).scalar_one_or_none()
    assert bare is None, "owner-scoped call should not create the suffix-less set"


@pytest.mark.asyncio
async def test_create_jarwis_actors_owner_rotate_isolation(
    db_session: AsyncSession,
) -> None:
    """AC-3: rotating the @alice set leaves the suffix-less set's token_hashes
    byte-identical (isolation via lookup-by-display_name), while every @alice
    token_hash changes."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)
    web_roles = jarwis_roles_for_mode("web")

    # Provision both the shared (suffix-less) and @alice sets.
    await create_jarwis_actors(board.key, session=db_session)
    await create_jarwis_actors(board.key, owner="alice", session=db_session)
    await db_session.flush()

    async def _hash(name: str) -> str:
        actor = (
            await db_session.execute(select(Actor).where(Actor.display_name == name))
        ).scalar_one()
        return actor.token_hash

    bare_before = {r: await _hash(_bare_name(r)) for r in web_roles}
    alice_before = {r: await _hash(f"{_bare_name(r)}@alice") for r in web_roles}

    # Rotate ONLY @alice.
    await create_jarwis_actors(board.key, owner="alice", rotate=True, session=db_session)
    await db_session.flush()

    bare_after = {r: await _hash(_bare_name(r)) for r in web_roles}
    alice_after = {r: await _hash(f"{_bare_name(r)}@alice") for r in web_roles}

    # Suffix-less untouched; every @alice hash changed.
    assert bare_after == bare_before, "suffix-less token_hashes must not rotate"
    for r in web_roles:
        assert alice_after[r] != alice_before[r], f"@alice {r} token_hash should rotate"


@pytest.mark.asyncio
async def test_create_jarwis_actors_bad_owner_no_side_effect(
    db_session: AsyncSession,
) -> None:
    """AC-4: an invalid slug raises ValueError BEFORE any DB write — zero
    namespaced actors created across all failed attempts (E1 no-partial-state)."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    for bad in ["Alice!", "a" * 21, "ALICE", "-alice", ""]:
        with pytest.raises(ValueError, match="invalid owner slug"):
            await create_jarwis_actors(board.key, owner=bad, session=db_session)

    namespaced = (
        await db_session.execute(
            select(Actor).where(Actor.display_name.like("jarwis-%@%"))
        )
    ).scalars().all()
    assert list(namespaced) == [], "bad slug must not create any namespaced actor"


@pytest.mark.asyncio
async def test_create_jarwis_actors_owner_idempotent_without_rotate(
    db_session: AsyncSession,
) -> None:
    """AC-6: a second owner-scoped call without --rotate creates no new actor and
    re-mints nothing (empty placeholder tokens); one actor per role is retained."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)
    web_roles = jarwis_roles_for_mode("web")

    first = await create_jarwis_actors(board.key, owner="alice", session=db_session)
    assert all(first.values()), "first owner call should mint all tokens"

    second = await create_jarwis_actors(board.key, owner="alice", session=db_session)
    assert set(second.keys()) == set(web_roles)
    assert all(v == "" for v in second.values()), "no re-mint without --rotate"

    # Exactly one @alice actor per role (no duplicates).
    actors = (
        await db_session.execute(
            select(Actor).where(Actor.display_name.like("jarwis-%@alice"))
        )
    ).scalars().all()
    assert len(list(actors)) == len(web_roles)


def test_main_bad_owner_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4 (main): a bad --owner slug exits non-zero via SystemExit, raised
    BEFORE any DB access (slug validated at the top of create_jarwis_actors)."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["projecthub", "create_jarwis_actors", "--board", "PH", "--owner", "Bad!"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    msg = str(exc_info.value)
    assert "create_jarwis_actors" in msg
    assert "invalid owner slug" in msg
