"""Tests for CLI commands — update_board_roles, create_jarwis_actors, create_board,
backfill_project_paths."""

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cli import (
    JARWIS_MODE_ROLES,
    JARWIS_SHARED_ROLES,
    BackfillResult,
    _jarwis_actor_name,
    _print_backfill_result,
    _validate_owner_slug,
    backfill_project_paths,
    create_board,
    create_jarwis_actors,
    jarwis_roles_for_mode,
    main,
    seed_backlog,
    update_board_roles,
)
from app.db.models import Actor, Board, BoardMembership, ProjectPath, Ticket, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.project_paths import get_project_path


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
async def test_update_board_roles_merges_template_and_preserves_secrets(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PH-328: update_board_roles MERGES the template role map in, preserving the
    sibling top-level secrets a repo-bound board carries. ``board.roles`` holds the
    role→permission map under ``"roles"`` but ALSO ``refresh_secret`` (git-hook auth)
    and ``webhook_secret`` (GitHub HMAC) as top-level siblings; the historical
    ``deepcopy(DEFAULT_WEB_ROLES)`` clobber wiped them, breaking git-refresh + webhook
    auth. The merge must (a) refresh the ``"roles"`` sub-dict to the template (which now
    carries pr_reviewer) and (b) leave both secrets intact."""
    stale = {
        # stale/incomplete role map (crucially, no pr_reviewer) → forces an update
        "roles": {"admin": {"permissions": ["*"]}},
        "refresh_secret": "a" * 48,      # git-hook auth (services/repositories)
        "webhook_secret": "hook-secret", # GitHub HMAC (api/git)
    }
    board = await _make_board_with_roles(db_session, stale)
    board_id = board.id

    await update_board_roles(db_session)
    # Caller owns commit: flush + expire to verify DB-level persistence.
    await db_session.flush()
    db_session.expire_all()

    # Reload from DB to verify flag_modified took effect.
    reloaded = (
        await db_session.execute(select(Board).where(Board.id == board_id))
    ).scalar_one()
    # (a) roles sub-dict refreshed to the template — and pr_reviewer backfilled in.
    assert reloaded.roles["roles"] == DEFAULT_WEB_ROLES["roles"]
    assert "pr_reviewer" in reloaded.roles["roles"]
    # (b) CRITICAL: the sibling secrets SURVIVED the merge (auth still works).
    assert reloaded.roles["refresh_secret"] == "a" * 48
    assert reloaded.roles["webhook_secret"] == "hook-secret"

    captured = capsys.readouterr()
    assert "Updated 1 board(s), 0 unchanged." in captured.out


@pytest.mark.asyncio
async def test_update_board_roles_backfills_pr_reviewer_into_clean_default(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A board already carrying DEFAULT_WEB_ROLES (which now includes pr_reviewer) is
    left unchanged — the template IS the pr_reviewer-bearing shape, so re-running is a
    no-op (idempotency judged on the roles sub-dict)."""
    await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    await update_board_roles(db_session)

    captured = capsys.readouterr()
    assert "Updated 0 board(s), 1 unchanged." in captured.out


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


# --- PH-329: multi-user (2+ human) regression guards -------------------------

# Distinct created_at so "oldest human" (= bootstrap admin) is DETERMINISTIC and
# the fixed ``order_by(created_at).limit(1)`` lookup has an unambiguous first row.
_EARLIER = datetime(2020, 1, 1, tzinfo=UTC)
_LATER = datetime(2021, 6, 1, tzinfo=UTC)


async def _make_two_humans(session: AsyncSession) -> tuple[Actor, Actor]:
    """Insert two human actors with DISTINCT created_at (older first).

    PH-329: reproduces the multi-user (PH-316/317) environment — an Admin plus a
    second human — that made the ``limit(1)``-less ``scalar_one_or_none()`` admin
    lookup raise ``MultipleResultsFound``. Returns ``(older, newer)``.
    """
    older = Actor(
        kind="human",
        display_name="Admin",
        token_hash="x" * 64,
        is_active=True,
        owner_slug="huseyin",
        created_at=_EARLIER,
    )
    newer = Actor(
        kind="human",
        display_name="emrehan",
        token_hash="y" * 64,
        is_active=True,
        owner_slug="emrehan",
        created_at=_LATER,
    )
    session.add_all([older, newer])
    await session.flush()
    return older, newer


@pytest.mark.asyncio
async def test_create_board_with_multiple_humans_selects_oldest(
    db_session: AsyncSession,
) -> None:
    """PH-329 REGRESSION (live trigger): multi-user (PH-316/317) added a second human
    actor, but the create_board admin lookup used ``scalar_one_or_none()`` WITHOUT
    ``limit(1)`` — so 2+ humans raised ``MultipleResultsFound`` and EVERY new
    create_board crashed. With the fix the oldest human (min created_at = the
    bootstrap admin) is selected and the board is created + linked to a workflow."""
    older, _newer = await _make_two_humans(db_session)

    result = await create_board("MA", "MyApp", session=db_session)

    assert result["status"] == "created"
    board = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalar_one()
    assert board.workflow_id is not None
    # Membership is keyed to the OLDEST human — not a MultipleResultsFound crash.
    membership = (
        await db_session.execute(
            select(BoardMembership).where(BoardMembership.board_id == board.id)
        )
    ).scalar_one()
    assert membership.actor_id == older.id
    assert membership.role == "admin"


@pytest.mark.asyncio
async def test_create_board_with_multiple_default_workflows_is_deterministic(
    db_session: AsyncSession,
) -> None:
    """PH-329 (latent sibling): ``Workflow.is_default`` has NO unique constraint, so
    2+ ``is_default=True`` rows are reachable via workflow CRUD. The create_board
    workflow lookup lacked ``limit(1)`` → ``MultipleResultsFound``. With the fix a
    deterministic default (oldest by created_at) is REUSED — no crash and no NEW
    workflow row is minted."""
    await _ensure_admin(db_session)
    wf_old = Workflow(
        name="Default A",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
        created_at=_EARLIER,
    )
    wf_new = Workflow(
        name="Default B",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
        created_at=_LATER,
    )
    db_session.add_all([wf_old, wf_new])
    await db_session.flush()

    result = await create_board("MA", "MyApp", session=db_session)

    assert result["status"] == "created"
    board = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalar_one()
    # Reused the OLDEST existing default; did NOT mint a third default workflow.
    assert board.workflow_id == wf_old.id
    defaults = (
        await db_session.execute(
            select(Workflow).where(Workflow.is_default.is_(True))
        )
    ).scalars().all()
    assert len(defaults) == 2


@pytest.mark.asyncio
async def test_create_board_idempotent_does_not_duplicate_board_or_workflow(
    db_session: AsyncSession,
) -> None:
    """PH-329 AC: a second create_board with the same key stays idempotent — status
    'existing', with neither a new Board NOR a new Workflow row added."""
    await _ensure_admin(db_session)

    first = await create_board("MA", "MyApp", session=db_session)
    wf_count_after_first = len(
        (await db_session.execute(select(Workflow))).scalars().all()
    )

    second = await create_board("MA", "MyAppRenamed", session=db_session)

    assert first["id"] == second["id"]
    assert second["status"] == "existing"
    boards = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalars().all()
    assert len(list(boards)) == 1
    wf_count_after_second = len(
        (await db_session.execute(select(Workflow))).scalars().all()
    )
    assert wf_count_after_second == wf_count_after_first


@pytest.mark.asyncio
async def test_create_board_does_not_change_existing_board_workflow(
    db_session: AsyncSession,
) -> None:
    """PH-329 AC (regression-free): opening a SECOND board reuses the shared default
    workflow and leaves the FIRST board's ``workflow_id`` untouched."""
    await _ensure_admin(db_session)
    await create_board("MA", "MyApp", session=db_session)
    board_a = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalar_one()
    wf_a = board_a.workflow_id

    await create_board("SHOP", "Shop", session=db_session)

    board_a_reloaded = (
        await db_session.execute(select(Board).where(Board.key == "MA"))
    ).scalar_one()
    assert board_a_reloaded.workflow_id == wf_a
    board_b = (
        await db_session.execute(select(Board).where(Board.key == "SHOP"))
    ).scalar_one()
    assert board_b.workflow_id == wf_a  # both boards share the one default


def test_ml_mode_roles_and_actor_names() -> None:
    """PH-293: the CLI 'ml' choice landed on main in PH-289 but the live-mounted
    working chain lacked it — create_jarwis_actors --mode ml died with 'invalid
    choice' on GXG re-init. Lock the role set and the jarwis-<role> display-name
    derivation the live board relies on, so neither side regresses again."""
    from app.cli import _jarwis_actor_name, jarwis_roles_for_mode

    assert jarwis_roles_for_mode("ml") == [
        "pm", "architect", "reviewer", "pr_reviewer", "qa",
        "data_engineer", "data_labeler", "ml_engineer", "ml_analyst",
    ]
    assert _jarwis_actor_name("data_engineer", "jarwis") == "jarwis-data-engineer"
    assert _jarwis_actor_name("ml_analyst", "jarwis") == "jarwis-ml-analyst"


def test_pr_reviewer_is_a_shared_role_with_kebab_actor_name() -> None:
    """PH-328: pr_reviewer is minted in EVERY mode (a shared role, mode-agnostic) and
    its actor name derives via the replace('_','-') branch → jarwis-pr-reviewer /
    jarwis-pr-reviewer@<owner>. Guards both the shared-role membership and the naming
    the .mcp.json wiring (jarwis-init suffix_map) depends on."""
    for mode in ("web", "unity", "android", "ios", "ml", "mobile"):
        assert "pr_reviewer" in jarwis_roles_for_mode(mode), mode
    assert _jarwis_actor_name("pr_reviewer", "jarwis") == "jarwis-pr-reviewer"
    assert _jarwis_actor_name("pr_reviewer", "jarwis", "alice") == "jarwis-pr-reviewer@alice"


@pytest.mark.asyncio
async def test_create_jarwis_actors_provisions_pr_reviewer(
    db_session: AsyncSession,
) -> None:
    """PH-328: a plain create_jarwis_actors run mints the jarwis-pr-reviewer actor with
    an isolated token, agent_role_hint='pr_reviewer', and a role=pr_reviewer membership;
    the {role: token} map carries the bare 'pr_reviewer' key. Second run without --rotate
    mints no new token (idempotent)."""
    board = await _make_board_with_roles(db_session, DEFAULT_WEB_ROLES)

    tokens = await create_jarwis_actors(board.key, session=db_session)

    assert "pr_reviewer" in tokens
    assert len(tokens["pr_reviewer"]) == 48  # secrets.token_hex(24)

    actor = (
        await db_session.execute(
            select(Actor).where(Actor.display_name == "jarwis-pr-reviewer")
        )
    ).scalar_one()
    assert actor.kind == "agent"
    assert actor.agent_role_hint == "pr_reviewer"

    membership = (
        await db_session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor.id,
            )
        )
    ).scalar_one()
    assert membership.role == "pr_reviewer"

    # Idempotent: second call without --rotate mints no new token for pr_reviewer.
    second = await create_jarwis_actors(board.key, session=db_session)
    assert second["pr_reviewer"] == ""  # existing actor, no rotation → empty placeholder


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


# --- backfill_project_paths (PH-325) ------------------------------------------


async def _make_board_with_repos_path(
    session: AsyncSession, key: str, repos_path: str | None
) -> Board:
    """Insert a Workflow + Board carrying ``repos_path`` (the backfill source)."""
    workflow = Workflow(
        name=f"WF-{key}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
    )
    session.add(workflow)
    await session.flush()

    board = Board(
        key=key,
        name=f"Board {key}",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        repos_path=repos_path,
    )
    session.add(board)
    await session.flush()
    return board


@pytest.mark.asyncio
async def test_backfill_absent_only_preserves_existing(
    db_session: AsyncSession,
) -> None:
    """AC-1/AC-2: boards missing a (owner, board) row get local_path == repos_path;
    a board that ALREADY has a row (user-set, different value) is preserved verbatim
    — never overwritten (absent-only upsert)."""
    ph = await _make_board_with_repos_path(db_session, "PH", "/host/ph")
    gxa = await _make_board_with_repos_path(db_session, "GXA", "/host/gxa")
    fn = await _make_board_with_repos_path(db_session, "FN", "/host/fn")

    # PH is already registered under huseyin with a DIFFERENT (user-set) path.
    db_session.add(
        ProjectPath(owner_slug="huseyin", board_id=ph.id, local_path="/user/custom/ph")
    )
    await db_session.flush()

    result = await backfill_project_paths(owner="huseyin", session=db_session)

    assert result.owner == "huseyin"
    assert result.inserted == 2  # GXA + FN
    assert result.skipped_existing == 1  # PH preserved
    assert result.skipped_no_path == 0
    assert result.skipped_too_long == 0

    # PH row untouched (overwrite would have made it "/host/ph").
    ph_row = (
        await db_session.execute(
            select(ProjectPath).where(ProjectPath.board_id == ph.id)
        )
    ).scalar_one()
    assert ph_row.local_path == "/user/custom/ph"

    # GXA + FN inherited repos_path under the right owner.
    for board, expected in [(gxa, "/host/gxa"), (fn, "/host/fn")]:
        row = (
            await db_session.execute(
                select(ProjectPath).where(ProjectPath.board_id == board.id)
            )
        ).scalar_one()
        assert row.owner_slug == "huseyin"
        assert row.local_path == expected


@pytest.mark.asyncio
async def test_backfill_skips_null_repos_path(db_session: AsyncSession) -> None:
    """AC-3: a board with repos_path NULL is skipped — no row is created for it."""
    aaa = await _make_board_with_repos_path(db_session, "AAA", None)
    await _make_board_with_repos_path(db_session, "BBB", "/host/bbb")

    result = await backfill_project_paths(owner="huseyin", session=db_session)

    assert result.inserted == 1
    assert result.skipped_no_path == 1

    aaa_rows = (
        await db_session.execute(
            select(ProjectPath).where(ProjectPath.board_id == aaa.id)
        )
    ).scalars().all()
    assert list(aaa_rows) == []


@pytest.mark.asyncio
async def test_backfill_defaults_to_oldest_human_owner(
    db_session: AsyncSession,
) -> None:
    """AC (owner resolution): with no --owner, the oldest human actor's owner_slug
    is used as the row key."""
    admin = Actor(
        kind="human",
        display_name="Admin",
        token_hash="x",
        is_active=True,
        owner_slug="huseyin",
    )
    db_session.add(admin)
    await db_session.flush()
    board = await _make_board_with_repos_path(db_session, "FN", "/host/fn")

    result = await backfill_project_paths(session=db_session)  # no owner → default

    assert result.owner == "huseyin"
    assert result.inserted == 1
    row = (
        await db_session.execute(
            select(ProjectPath).where(ProjectPath.board_id == board.id)
        )
    ).scalar_one()
    assert row.owner_slug == "huseyin"
    assert row.local_path == "/host/fn"


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing(db_session: AsyncSession) -> None:
    """AC (dry-run): --dry-run counts the would-be inserts but commits NOTHING."""
    await _make_board_with_repos_path(db_session, "AAA", "/host/aaa")
    await _make_board_with_repos_path(db_session, "BBB", "/host/bbb")

    result = await backfill_project_paths(owner="huseyin", dry_run=True, session=db_session)

    assert result.dry_run is True
    assert result.inserted == 2  # would-be inserts

    rows = (await db_session.execute(select(ProjectPath))).scalars().all()
    assert list(rows) == [], "dry-run must leave the registry unchanged"


@pytest.mark.asyncio
async def test_backfill_skips_too_long_repos_path(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC (255 guard): a repos_path longer than the local_path column (255) is
    skipped with a warning and does NOT crash; other boards still backfill."""
    long_path = "/host/" + "x" * 300  # > 255
    assert len(long_path) > 255
    long_board = await _make_board_with_repos_path(db_session, "LONG", long_path)
    await _make_board_with_repos_path(db_session, "OK", "/host/ok")

    result = await backfill_project_paths(owner="huseyin", session=db_session)

    assert result.skipped_too_long == 1
    assert result.inserted == 1  # OK still backfilled

    captured = capsys.readouterr()
    assert "SKIP" in captured.out

    long_rows = (
        await db_session.execute(
            select(ProjectPath).where(ProjectPath.board_id == long_board.id)
        )
    ).scalars().all()
    assert list(long_rows) == []


@pytest.mark.asyncio
async def test_backfill_idempotent_second_run_zero(db_session: AsyncSession) -> None:
    """AC-2 (idempotency): a second run inserts 0 rows and skips all as existing."""
    await _make_board_with_repos_path(db_session, "AAA", "/host/aaa")
    await _make_board_with_repos_path(db_session, "BBB", "/host/bbb")

    first = await backfill_project_paths(owner="huseyin", session=db_session)
    assert first.inserted == 2

    second = await backfill_project_paths(owner="huseyin", session=db_session)
    assert second.inserted == 0
    assert second.skipped_existing == 2

    rows = (await db_session.execute(select(ProjectPath))).scalars().all()
    assert len(list(rows)) == 2, "no duplicate rows on re-run"


@pytest.mark.asyncio
async def test_backfill_unresolved_default_owner_aborts(
    db_session: AsyncSession,
) -> None:
    """AC-4: no --owner while the oldest human has no owner_slug → SystemExit BEFORE
    any write (no partial state)."""
    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    db_session.add(admin)  # owner_slug is None
    await db_session.flush()
    await _make_board_with_repos_path(db_session, "FN", "/host/fn")

    with pytest.raises(SystemExit, match="no default owner"):
        await backfill_project_paths(session=db_session)

    rows = (await db_session.execute(select(ProjectPath))).scalars().all()
    assert list(rows) == [], "unresolved owner must write nothing"


@pytest.mark.asyncio
async def test_backfill_no_human_owner_aborts(db_session: AsyncSession) -> None:
    """AC-4 (no human at all): the default-owner branch aborts identically when the
    DB has no human actor to resolve from."""
    await _make_board_with_repos_path(db_session, "FN", "/host/fn")
    with pytest.raises(SystemExit, match="no default owner"):
        await backfill_project_paths(session=db_session)


@pytest.mark.asyncio
async def test_backfill_bad_owner_slug_no_side_effect(
    db_session: AsyncSession,
) -> None:
    """AC-4: an invalid --owner slug raises ValueError BEFORE any write — zero rows
    created across every failed attempt."""
    await _make_board_with_repos_path(db_session, "FN", "/host/fn")

    for bad in ["Bad!", "ALICE", "-x", "a" * 21, ""]:
        with pytest.raises(ValueError, match="invalid owner slug"):
            await backfill_project_paths(owner=bad, session=db_session)

    rows = (await db_session.execute(select(ProjectPath))).scalars().all()
    assert list(rows) == []


def test_backfill_print_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    """AC (--json): emits exactly {owner, inserted, skipped_existing, skipped_no_path,
    skipped_too_long} for jarwis-init/deploy consumption."""
    result = BackfillResult(
        owner="huseyin",
        inserted=8,
        skipped_existing=2,
        skipped_no_path=1,
        skipped_too_long=0,
    )
    _print_backfill_result(result, as_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert set(payload.keys()) == {
        "owner",
        "inserted",
        "skipped_existing",
        "skipped_no_path",
        "skipped_too_long",
    }
    assert payload["owner"] == "huseyin"
    assert payload["inserted"] == 8
    assert payload["skipped_existing"] == 2


def test_main_backfill_bad_owner_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 (main): a bad --owner slug on backfill exits non-zero via SystemExit,
    raised BEFORE any DB access (slug validated at the top)."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["projecthub", "backfill_project_paths", "--owner", "Bad!"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    msg = str(exc_info.value)
    assert "backfill_project_paths" in msg
    assert "invalid owner slug" in msg


@pytest.mark.asyncio
async def test_backfill_read_path_returns_repos_path(
    db_session: AsyncSession,
) -> None:
    """AC (read path unchanged): after backfill, get_project_path returns the
    backfilled local_path == repos_path with NO code change to the read path."""
    board = await _make_board_with_repos_path(db_session, "FN", "/host/fn")
    human = Actor(
        kind="human",
        display_name="Huseyin",
        token_hash="x",
        is_active=True,
        owner_slug="huseyin",
    )
    db_session.add(human)
    await db_session.flush()
    db_session.add(BoardMembership(board_id=board.id, actor_id=human.id, role="admin"))
    await db_session.flush()

    await backfill_project_paths(owner="huseyin", session=db_session)

    # require_board_member iterates eager-loaded memberships — re-fetch with them.
    actor = (
        await db_session.execute(
            select(Actor)
            .where(Actor.id == human.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    owner, row = await get_project_path(db_session, actor, board)

    assert owner == "huseyin"
    assert row is not None
    assert row.local_path == "/host/fn"


@pytest.mark.asyncio
async def test_backfill_with_multiple_humans_resolves_oldest_owner(
    db_session: AsyncSession,
) -> None:
    """PH-329 sibling guard: ``backfill_project_paths(owner=None)`` resolves the
    default owner via the SAME ``limit(1)``-less admin lookup. With 2+ humans it must
    not raise ``MultipleResultsFound`` and must key rows under the OLDEST human's
    owner_slug (the hub-host admin)."""
    older, _newer = await _make_two_humans(db_session)  # older.owner_slug == "huseyin"
    await _make_board_with_repos_path(db_session, "FN", "/host/fn")

    result = await backfill_project_paths(owner=None, session=db_session)

    assert result.owner == older.owner_slug == "huseyin"  # oldest human, not a crash
    assert result.inserted == 1
    row = (
        await db_session.execute(
            select(ProjectPath).where(ProjectPath.owner_slug == "huseyin")
        )
    ).scalar_one()
    assert row.local_path == "/host/fn"


@pytest.mark.asyncio
async def test_seed_backlog_with_multiple_humans_does_not_raise(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PH-329 sibling guard: seed_backlog picks the admin via the same ``limit(1)``-
    less lookup, so with 2+ humans it must not raise ``MultipleResultsFound``.
    seed_backlog opens its OWN ``SessionLocal``; patch it to the in-memory test
    session (borrowed — the fixture owns its lifecycle). The oldest human carries the
    admin membership ``create_ticket``'s permission check needs."""
    older, _newer = await _make_two_humans(db_session)  # oldest = "Admin"
    workflow = Workflow(
        name="WF",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    db_session.add(workflow)
    await db_session.flush()
    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=older.id,
    )
    db_session.add(board)
    await db_session.flush()
    db_session.add(BoardMembership(board_id=board.id, actor_id=older.id, role="admin"))
    await db_session.commit()

    @asynccontextmanager
    async def _fake_sessionlocal() -> AsyncIterator[AsyncSession]:
        yield db_session  # borrowed: do NOT close the fixture-owned session

    monkeypatch.setattr("app.cli.SessionLocal", _fake_sessionlocal)

    await seed_backlog()  # must NOT raise MultipleResultsFound

    tickets = (
        await db_session.execute(select(Ticket).where(Ticket.board_id == board.id))
    ).scalars().all()
    assert len(tickets) > 0  # backlog seeded via the oldest (admin) human
