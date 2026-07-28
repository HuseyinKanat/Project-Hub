"""Board queries and bootstrap helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import Conflict, NotFound
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    BoardWorkflow,
    SonarQubeMetric,
    Workflow,
)
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


def mask_webhook_secret(roles: dict[str, object]) -> dict[str, object]:
    """Mask webhook_secret and refresh_secret in roles dict for API responses."""
    if not isinstance(roles, dict):
        return roles
    result = dict(roles)
    if result.get("webhook_secret"):
        result["webhook_secret"] = "*****"
    if result.get("refresh_secret"):
        result["refresh_secret"] = "*****"
    return result


def parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


async def list_boards(session: AsyncSession, actor: Actor) -> list[Board]:
    """Return the boards ``actor`` is a member of (ANY role), ordered by key.

    PH-327 (broken access control): the list is now MEMBERSHIP-SCOPED — an actor
    only sees boards it belongs to, closing the board-enumeration leak on both the
    REST ``GET /api/boards`` and the MCP ``list_boards`` channels (they share this
    seam). Scope comes from the already eager-loaded ``actor.memberships``
    (``current_actor`` / ``resolve_actor_by_token`` selectinload them), so no extra
    query. An admin is seeded into EVERY board at creation time, so scoping is
    regression-free for them (they still see all 11). Zero memberships → empty list
    (short-circuited so no ``IN ()`` is emitted).
    """
    member_board_ids = {membership.board_id for membership in actor.memberships}
    if not member_board_ids:
        return []
    result = await session.execute(
        select(Board)
        .where(Board.id.in_(member_board_ids))
        .options(
            selectinload(Board.workflow),
            selectinload(Board.repositories),  # PH-221: eager-load repos (primary_repository)
            # PH-193/PH-246: eager-load per-repo health + each metric's repo link for
            # board_response (health = primary, repo_health = breakdown).
            selectinload(Board.sonarqube_metrics).selectinload(SonarQubeMetric.repository),
        )
        .order_by(Board.key)
    )
    return list(result.scalars())


async def get_board(session: AsyncSession, board_id: str) -> Board:
    board_uuid = parse_uuid(board_id)
    statement = select(Board).options(
        selectinload(Board.workflow),
        selectinload(Board.repositories),  # PH-221: eager-load repos (primary_repository)
        # PH-193/PH-246: eager-load per-repo health + each metric's repo link for
        # board_response (health = primary, repo_health = breakdown).
        selectinload(Board.sonarqube_metrics).selectinload(SonarQubeMetric.repository),
    )
    if board_uuid is None:
        statement = statement.where(Board.key == board_id.upper())
    else:
        statement = statement.where(Board.id == board_uuid)

    board = (await session.execute(statement)).scalar_one_or_none()
    if board is None:
        raise NotFound("board")
    return board


async def create_board_with_defaults(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    description: str = "",
    project_type: str = "web_app",
    admin_actor: Actor,
) -> Board:
    """Create a board seeded with the default workflow + roles, adding ``admin_actor``
    as its ``admin`` member.

    PH-331: THE ONE create-board path, shared by ``cli.create_board`` (bootstrap) and
    the REST ``POST /api/boards`` (admin self-service). Behavior mirrors the previous
    inline CLI logic exactly — resolve-or-create the default ``Workflow``, seed
    ``roles=DEFAULT_WEB_ROLES`` + ``created_by=admin_actor.id``, then make the admin a
    board ``admin`` member — with ONE difference: the admin identity is a PARAMETER
    (the calling actor for REST; the oldest-human actor for the CLI) instead of always
    the first human.

    Idempotency/uniqueness: ``Board.key`` is ``String(5)`` UNIQUE. A duplicate key
    raises :class:`Conflict` (409) — both via a pre-check AND via an ``IntegrityError``
    caught on flush (covering the TOCTOU race between the pre-check and the insert). On
    the race path the failed unit is rolled back so no orphan board/workflow/membership
    rows leak. The CLI catches the pre-check ``Conflict`` and maps it to its idempotent
    ``status:existing`` no-raise contract.

    Does NOT commit — the CALLER owns the transaction boundary (the CLI commits when it
    opened its own session; the REST handler commits once). Flushes so ``board.id`` and
    the membership row are materialized before return.
    """
    key_upper = key.upper()
    existing = (
        await session.execute(select(Board).where(Board.key == key_upper))
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"board key {key_upper!r} already exists")

    workflow = (
        await session.execute(
            select(Workflow)
            .where(Workflow.is_default.is_(True))
            .order_by(Workflow.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if workflow is None:
        workflow = Workflow(
            name="Default ProjectHub Workflow",
            states=DEFAULT_STATES,
            transitions=DEFAULT_TRANSITIONS,
            is_default=True,
        )
        session.add(workflow)
        await session.flush()

    board = Board(
        key=key_upper,
        name=name,
        description=description,
        project_type=project_type,
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin_actor.id,
    )
    session.add(board)
    try:
        await session.flush()
        session.add(
            BoardMembership(board_id=board.id, actor_id=admin_actor.id, role="admin")
        )
        await session.flush()
    except IntegrityError as exc:
        # TOCTOU: another writer committed the same key between our pre-check and
        # flush → the UNIQUE(board.key) constraint fires. Roll back the partial unit
        # (no orphan board/membership) and surface the same 409 as the pre-check.
        await session.rollback()
        raise Conflict(f"board key {key_upper!r} already exists") from exc
    return board


async def get_default_workflow(session: AsyncSession) -> Workflow:
    workflow = (
        await session.execute(select(Workflow).where(Workflow.is_default.is_(True)).limit(1))
    ).scalar_one_or_none()
    if workflow is None:
        raise NotFound("default workflow")
    return workflow


async def get_active_workflow(session: AsyncSession, board_id: UUID) -> Workflow:
    """Get the active workflow for a board.

    Falls back to board.workflow_id for backward compatibility
    until the migration to many-to-many is complete.
    """
    # Try to get active workflow from junction table
    board_workflow = (
        await session.execute(
            select(BoardWorkflow)
            .options(selectinload(BoardWorkflow.workflow))
            .where(
                BoardWorkflow.board_id == board_id,
                BoardWorkflow.is_active.is_(True)
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if board_workflow:
        return board_workflow.workflow

    # Fallback to the legacy workflow_id column
    board = await get_board(session, str(board_id))
    return board.workflow


async def update_board(
    session: AsyncSession,
    board: Board,
    name: str | None = None,
    description: str | None = None,
    project_type: str | None = None,
    roles: dict[str, object] | None = None,
    repos_path: str | None = None,
) -> Board:
    """Update board fields. Only updates provided fields.

    PH-230: ``repos_path`` is the board's HOST filesystem root. When the caller
    passes a value (``None`` means "not provided" here), an empty/whitespace
    string clears the path to NULL (a board with no path is a valid state —
    detection simply disabled); a non-empty value is stored stripped. Callers
    that allow clearing pass an explicit ``""``; the API handler validates a
    non-empty path via ``repo_paths`` BEFORE this point.
    """
    if name is not None:
        board.name = name
    if description is not None:
        board.description = description
    if project_type is not None:
        board.project_type = project_type
    if roles is not None:
        # Merge roles dict to preserve existing role definitions
        if isinstance(board.roles, dict):
            board.roles = {**board.roles, **roles}
        else:
            board.roles = roles
    if repos_path is not None:
        # Empty / whitespace-only → clear to NULL; otherwise store stripped.
        board.repos_path = repos_path.strip() or None

    await session.flush()
    return board
