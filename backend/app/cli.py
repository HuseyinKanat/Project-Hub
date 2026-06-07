"""ProjectHub operational CLI."""

import argparse
import asyncio
import copy
import json as _json
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.db.session import SessionLocal
from app.services.boards import get_active_workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# Roles wired into Jarwis sub-agent isolation. Each gets its own actor + token
# + board membership; agents authenticate exclusively as their assigned role.
# See contracts/git.md §6 and templates/project-CLAUDE.md.
# Roles wired into Jarwis sub-agent isolation. Split by mode so
# create_jarwis_actors can provision only what the project needs
# (e.g. a Unity project doesn't need backend_dev/frontend_dev actors).
JARWIS_SHARED_ROLES: list[str] = ["pm", "architect", "reviewer", "qa"]
JARWIS_MODE_ROLES: dict[str, list[str]] = {
    "web": ["backend_dev", "frontend_dev"],
    "unity": ["unity_dev", "unity_scene_manager", "unity_platform"],
    # mobile mode reuses backend/frontend (mobile dev often spans both)
    "mobile": ["backend_dev", "frontend_dev"],
    # native SDK/bridge repos — single implementer per platform
    # (Jarwis modes/android.md + modes/ios.md)
    "android": ["android_dev"],
    "ios": ["ios_dev"],
}
# Backwards-compatible alias — pre-mode callers got the full web set.
JARWIS_ROLES: list[str] = JARWIS_SHARED_ROLES + JARWIS_MODE_ROLES["web"]


def jarwis_roles_for_mode(mode: str) -> list[str]:
    """Return the list of project-hub role names a given mode needs actors for."""
    extra = JARWIS_MODE_ROLES.get(mode)
    if extra is None:
        raise ValueError(
            f"unknown jarwis mode {mode!r}; known: {sorted(JARWIS_MODE_ROLES)}"
        )
    return JARWIS_SHARED_ROLES + extra

BACKLOG_SEED: list[dict[str, Any]] = [
    {
        "type": "feature",
        "title": "Stale claim cron: süresi dolan claim'leri otomatik release et",
        "description": (
            "Agent claim alıp heartbeat göndermezse ticket kilitli kalıyor. "
            "Periyodik bir görev `claimed_at` + timeout'u geçen ticket'ları otomatik release etmeli. "
            "Öneri: APScheduler veya asyncio background task, her 60s kontrol."
        ),
        "priority": "medium",
        "labels": ["backend", "reliability"],
    },
    {
        "type": "feature",
        "title": "Custom workflow editor UI: board workflow'unu frontend'den düzenleme",
        "description": (
            "Admin kullanıcı board ayarlarından state ve transition'ları görsel olarak düzenleyebilmeli. "
            "Backend zaten workflow CRUD destekliyor, sadece frontend UI eksik."
        ),
        "priority": "low",
        "labels": ["frontend", "ux"],
    },
    {
        "type": "feature",
        "title": "Responsive mobile: board ve ticket detail mobil görünümü",
        "description": (
            "Kanban board ve TicketDetail sayfaları mobil ekranlarda kırılıyor. "
            "Tailwind responsive breakpoint'leri ile sidebar collapse, kanban scroll düzeltmesi yapılmalı."
        ),
        "priority": "low",
        "labels": ["frontend", "mobile", "ux"],
    },
    {
        "type": "feature",
        "title": "Notification sistemi: state geçişlerinde email/in-app bildirim",
        "description": (
            "Ticket state değiştiğinde veya comment eklendiğinde ilgili actor'lara "
            "in-app (WebSocket push) ve opsiyonel email bildirimi gönderilmeli."
        ),
        "priority": "low",
        "labels": ["backend", "frontend", "notifications"],
    },
    {
        "type": "feature",
        "title": "GitHub webhook secret konfigürasyonu: board bazlı HMAC doğrulama",
        "description": (
            "Webhook endpoint şu an secret yoksa HMAC'i atlıyor. "
            "Board roles'a `webhook_secret` alanı eklenmeli, admin UI'dan ayarlanabilmeli."
        ),
        "priority": "medium",
        "labels": ["backend", "security", "git"],
    },
    {
        "type": "feature",
        "title": "PR merge → auto state transition: in_test sonrası done otomasyonu (v2)",
        "description": (
            "GitHub pull_request merged event'inde ticket otomatik olarak `done`'a geçirilmeli. "
            "Gate: ticket `in_test` state'inde olmalı. v1'de manuel yapıldı (rules.md 7.12), v2 için."
        ),
        "priority": "low",
        "labels": ["backend", "git", "automation"],
    },
]


async def update_board_roles(session: AsyncSession | None = None) -> None:
    """Update all boards' roles JSON to match DEFAULT_WEB_ROLES. Idempotent.

    If *session* is provided it is used directly (caller owns commit/rollback).
    Otherwise a new SessionLocal context is opened and committed internally.
    """
    async def _run(sess: AsyncSession, *, owned: bool) -> None:
        boards = (await sess.execute(select(Board))).scalars().all()
        updated = 0
        unchanged = 0
        for board in boards:
            if board.roles == DEFAULT_WEB_ROLES:
                unchanged += 1
            else:
                board.roles = copy.deepcopy(DEFAULT_WEB_ROLES)
                flag_modified(board, "roles")
                updated += 1
        if owned:
            await sess.commit()
        print(f"Updated {updated} board(s), {unchanged} unchanged.")

    if session is not None:
        await _run(session, owned=False)
    else:
        async with SessionLocal() as sess:
            await _run(sess, owned=True)


# Known-good shape for the backlog->to_do transition. This is the original
# session-start form (mirrors DEFAULT_TRANSITIONS[0]) that G1-G14 ran against:
# pm/architect may move a ticket out of backlog, and there is NO field gate.
# An E2E test corrupted PH's active workflow by stripping allowed_roles and
# injecting a technical_depth field_gate, which locked the whole pipeline
# (engine rejects every actor when allowed_roles is missing). See PH-168.
KNOWN_GOOD_BACKLOG_TO_DO: dict[str, Any] = {
    "from": "backlog",
    "to": "to_do",
    "allowed_roles": ["pm", "architect"],
}


def repair_backlog_to_do_transitions(
    transitions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Restore the backlog->to_do transition to its known-good shape.

    Returns ``(new_transitions, changed)``. A brand-new list is returned so the
    caller can reassign the JSON column (avoids in-place mutation tracking
    pitfalls). Only the backlog->to_do entry is touched; every other transition
    is copied through verbatim. Idempotent: if the entry already matches the
    known-good shape, ``changed`` is False and the list is structurally equal.

    Raises ``ValueError`` if no backlog->to_do transition exists: repairing a
    missing transition would silently change workflow semantics, so we surface
    it instead of guessing.
    """
    new_transitions: list[dict[str, Any]] = []
    found = False
    changed = False
    for transition in transitions:
        if transition.get("from") == "backlog" and transition.get("to") == "to_do":
            found = True
            good = copy.deepcopy(KNOWN_GOOD_BACKLOG_TO_DO)
            if transition != good:
                changed = True
            new_transitions.append(good)
        else:
            new_transitions.append(copy.deepcopy(transition))
    if not found:
        raise ValueError("no backlog->to_do transition found in workflow")
    return new_transitions, changed


async def repair_workflow(board_key: str, session: AsyncSession | None = None) -> bool:
    """Restore a board's active backlog->to_do transition to known-good. Idempotent.

    Resolves the board's *active* workflow exactly as the transition engine does
    (active BoardWorkflow junction row, falling back to board.workflow_id), then
    rewrites only the backlog→to_do transition. Returns True if a change was
    written, False if the workflow was already healthy.

    If *session* is provided it is used directly (caller owns commit/rollback);
    otherwise a new SessionLocal context is opened and committed internally.
    """

    async def _run(sess: AsyncSession, *, owned: bool) -> bool:
        board = (
            await sess.execute(select(Board).where(Board.key == board_key.upper()))
        ).scalar_one_or_none()
        if board is None:
            print(f"repair_workflow: board {board_key.upper()} not found.")
            return False

        workflow = await get_active_workflow(sess, board.id)

        new_transitions, changed = repair_backlog_to_do_transitions(workflow.transitions)
        if not changed:
            print(
                f"repair_workflow: {board_key.upper()} already healthy "
                f"(workflow {workflow.id}); backlog->to_do is known-good, nothing to do."
            )
            return False

        # Reassign a fresh list so SQLAlchemy persists the JSON column; pair with
        # flag_modified for backends/attributes that need explicit dirtying.
        workflow.transitions = new_transitions
        flag_modified(workflow, "transitions")
        if owned:
            await sess.commit()
        print(
            f"repair_workflow: {board_key.upper()} repaired "
            f"(workflow {workflow.id}); backlog->to_do restored to "
            f'allowed_roles=["pm","architect"], field gate removed.'
        )
        return True

    if session is not None:
        return await _run(session, owned=False)
    async with SessionLocal() as sess:
        return await _run(sess, owned=True)


async def bootstrap() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        workflow = (
            await session.execute(select(Workflow).where(Workflow.is_default.is_(True)))
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

        admin = (
            await session.execute(
                select(Actor)
                .where(Actor.kind == "human", Actor.display_name == settings.admin_display_name)
                .options(selectinload(Actor.memberships))
            )
        ).scalar_one_or_none()
        if admin is None:
            admin = Actor(
                kind="human",
                display_name=settings.admin_display_name,
                token_hash=hash_token(settings.admin_password, settings.token_hash_rounds),
                is_active=True,
            )
            session.add(admin)
            await session.flush()

        board = (await session.execute(select(Board).where(Board.key == "PH"))).scalar_one_or_none()
        if board is None:
            board = Board(
                key="PH",
                name="ProjectHub",
                description="Default ProjectHub board",
                project_type="web_app",
                workflow_id=workflow.id,
                roles=DEFAULT_WEB_ROLES,
                created_by=admin.id,
            )
            session.add(board)
            await session.flush()

        membership = (
            await session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == admin.id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))

        await session.commit()
    print("Bootstrap complete. Use ADMIN_PASSWORD as the initial bearer token for the admin actor.")
    await seed_backlog()


async def seed_backlog() -> None:
    """Seed known backlog items into the PH board if they don't already exist."""
    from app.schemas import TicketCreate
    from app.services.tickets import create_ticket

    async with SessionLocal() as session:
        board = (await session.execute(select(Board).where(Board.key == "PH"))).scalar_one_or_none()
        if board is None:
            print("seed_backlog: PH board not found, skipping.")
            return

        admin = (
            await session.execute(
                select(Actor)
                .where(Actor.kind == "human")
                .options(selectinload(Actor.memberships))
                .order_by(Actor.created_at)
            )
        ).scalar_one_or_none()
        if admin is None:
            print("seed_backlog: no admin actor found, skipping.")
            return

        existing_titles = {
            row[0]
            for row in (
                await session.execute(
                    select(Ticket.title).where(Ticket.board_id == board.id)
                )
            ).all()
        }

        seeded = 0
        for item in BACKLOG_SEED:
            if item["title"] in existing_titles:
                continue
            payload = TicketCreate(
                board_id=str(board.id),
                type=item["type"],
                title=item["title"],
                description=item.get("description"),
                priority=item.get("priority", "medium"),
                labels=item.get("labels", []),
            )
            await create_ticket(session, actor=admin, payload=payload)
            seeded += 1

        if seeded:
            print(f"seed_backlog: {seeded} new backlog ticket(s) created.")
        else:
            print("seed_backlog: all items already exist, nothing to seed.")


async def create_jarwis_actors(
    board_key: str,
    *,
    name_prefix: str = "jarwis",
    rotate: bool = False,
    mode: str = "web",
    session: AsyncSession | None = None,
) -> dict[str, str]:
    """Provision per-role Jarwis sub-agent actors with isolated tokens.

    For each role in the selected ``mode``'s role list
    (``jarwis_roles_for_mode(mode)``), ensures an Actor named
    ``<name_prefix>-<role>`` exists and has membership on the target board with
    that role. If the actor is new (or ``rotate=True``), a fresh random token
    is minted, hashed into ``token_hash``, and the plain token is collected
    into the return dict for the operator to wire into .mcp.json.

    Returns ``{role: plain_token}`` only for actors whose token was minted in
    this call. Existing actors without rotation get an empty placeholder so
    the operator knows they're already provisioned.

    Modes select which implementer roles get actors:
      web     → pm, architect, reviewer, qa, backend_dev, frontend_dev
      unity   → pm, architect, reviewer, qa, unity_dev, unity_scene_manager, unity_platform
      mobile  → pm, architect, reviewer, qa, backend_dev, frontend_dev
      android → pm, architect, reviewer, qa, android_dev
      ios     → pm, architect, reviewer, qa, ios_dev
    """
    roles = jarwis_roles_for_mode(mode)

    async def _run(sess: AsyncSession, *, owned: bool) -> dict[str, str]:
        settings = get_settings()
        board = (
            await sess.execute(select(Board).where(Board.key == board_key.upper()))
        ).scalar_one_or_none()
        if board is None:
            print(f"create_jarwis_actors: board {board_key!r} not found, aborting.")
            return {}

        tokens: dict[str, str] = {}
        for role in roles:
            # Actor display name convention:
            #   backend_dev / frontend_dev → drop "_dev" (single-implementer
            #     web mode, kept short for backwards-compat)
            #   anything else → keep verbatim, just kebab-case (jarwis-pm,
            #     jarwis-unity-dev, jarwis-unity-scene-manager)
            if role in {"backend_dev", "frontend_dev"}:
                suffix = role.removesuffix("_dev")
            else:
                suffix = role.replace("_", "-")
            actor_name = f"{name_prefix}-{suffix}"
            actor = (
                await sess.execute(select(Actor).where(Actor.display_name == actor_name))
            ).scalar_one_or_none()

            minted = False
            if actor is None:
                token = secrets.token_hex(24)
                actor = Actor(
                    kind="agent",
                    display_name=actor_name,
                    token_hash=hash_token(token, settings.token_hash_rounds),
                    is_active=True,
                    agent_role_hint=role,
                )
                sess.add(actor)
                await sess.flush()
                tokens[role] = token
                minted = True
            elif rotate:
                token = secrets.token_hex(24)
                actor.token_hash = hash_token(token, settings.token_hash_rounds)
                tokens[role] = token
                minted = True
            else:
                tokens[role] = ""

            membership = (
                await sess.execute(
                    select(BoardMembership).where(
                        BoardMembership.board_id == board.id,
                        BoardMembership.actor_id == actor.id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                sess.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
            elif membership.role != role:
                membership.role = role

            status = "minted" if minted else "existing (use --rotate to refresh token)"
            print(f"  {actor_name:25s}  role={role:14s}  {status}")

        if owned:
            await sess.commit()
        else:
            await sess.flush()
        return tokens

    if session is not None:
        return await _run(session, owned=False)
    async with SessionLocal() as new_session:
        return await _run(new_session, owned=True)


async def create_board(
    key: str,
    name: str,
    *,
    description: str = "",
    project_type: str = "web_app",
    session: AsyncSession | None = None,
) -> dict[str, str]:
    """Create a board with default workflow + roles, idempotent on key.

    Used by Jarwis's per-project bootstrap (`jarwis-init.sh`) so new projects
    don't require manual UI/DB intervention to spin up their tracking board.
    """

    async def _run(sess: AsyncSession, *, owned: bool) -> dict[str, str]:
        key_upper = key.upper()
        existing = (
            await sess.execute(select(Board).where(Board.key == key_upper))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"create_board: {key_upper} already exists (id={existing.id}), nothing to do.")
            return {"key": key_upper, "id": str(existing.id), "status": "existing"}

        workflow = (
            await sess.execute(select(Workflow).where(Workflow.is_default.is_(True)))
        ).scalar_one_or_none()
        if workflow is None:
            workflow = Workflow(
                name="Default ProjectHub Workflow",
                states=DEFAULT_STATES,
                transitions=DEFAULT_TRANSITIONS,
                is_default=True,
            )
            sess.add(workflow)
            await sess.flush()

        admin = (
            await sess.execute(
                select(Actor).where(Actor.kind == "human").order_by(Actor.created_at)
            )
        ).scalar_one_or_none()
        if admin is None:
            print("create_board: no human admin actor found; run bootstrap first.")
            return {"status": "no_admin"}

        board = Board(
            key=key_upper,
            name=name,
            description=description,
            project_type=project_type,
            workflow_id=workflow.id,
            roles=DEFAULT_WEB_ROLES,
            created_by=admin.id,
        )
        sess.add(board)
        await sess.flush()
        sess.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))

        if owned:
            await sess.commit()
        print(f"create_board: created {key_upper} ({name}) id={board.id}")
        return {"key": key_upper, "id": str(board.id), "status": "created"}

    if session is not None:
        return await _run(session, owned=False)
    async with SessionLocal() as new_session:
        return await _run(new_session, owned=True)


@dataclass
class ConnectRepositoryResult:
    """Result of connect_repository CLI command."""

    repo_id: str
    board_key: str
    local_path: str
    remote_url: str | None
    default_branch: str
    provider: str
    last_synced_sha: str | None
    new_commits: int
    """refresh_secret is only set when newly minted or rotated; None otherwise."""
    refresh_secret: str | None = None


async def connect_repository(
    board_key: str,
    local_path: str,
    *,
    remote_url: str | None = None,
    default_branch: str = "main",
    provider: str = "local",
    rotate_secret: bool = False,
    no_backfill: bool = False,
) -> ConnectRepositoryResult:
    """Connect (or reconfigure) a git repository for a board.

    - Creates or updates the Repository row via ``upsert_repository``.
    - Manages ``board.roles["refresh_secret"]``: generates a new 48-hex secret
      when first connecting or when ``rotate_secret=True``; otherwise preserves
      the existing secret (never re-prints it for security).
    - Runs initial backfill via ``sync_repo`` unless ``no_backfill=True``.

    Idempotent: calling twice with the same arguments is safe.
    CLI bypass note: direct service call (no admin auth) — operator must have
    ``docker compose exec`` access (host trust assumed for ops commands).
    """
    from app.git.sync import sync_repo
    from app.schemas import RepositoryUpsert
    from app.services.repositories import upsert_repository

    async with SessionLocal() as session:
        # 1. Resolve board.
        board = (
            await session.execute(select(Board).where(Board.key == board_key.upper()))
        ).scalar_one_or_none()
        if board is None:
            raise SystemExit(
                f"connect_repository: board {board_key!r} not found "
                "— run bootstrap first (exit 2)"
            )

        # 2. Build and validate the upsert payload (Pydantic validates /repos/ prefix).
        # Validate provider is a recognised literal before constructing the Pydantic model.
        valid_providers = ("github", "gitlab", "local")
        if provider not in valid_providers:
            raise SystemExit(
                f"connect_repository: invalid provider {provider!r}; "
                f"choose from {valid_providers}"
            )
        try:
            payload = RepositoryUpsert(
                provider=provider,  # type: ignore[arg-type]  # validated above
                remote_url=remote_url or None,
                default_branch=default_branch,
                local_path=local_path,
            )
        except Exception as exc:
            raise SystemExit(f"connect_repository: invalid arguments — {exc} (exit 2)") from exc

        # 3. Upsert repository row.
        repo = await upsert_repository(session, board, payload)

        # 4. Manage refresh_secret.
        minted_secret: str | None = None
        roles_dict: dict[str, Any] = dict(board.roles) if board.roles else {}
        existing_secret: str | None = roles_dict.get("refresh_secret")

        if existing_secret is None or rotate_secret:
            minted_secret = secrets.token_hex(24)  # 48 hex chars
            roles_dict["refresh_secret"] = minted_secret
            board.roles = roles_dict
            flag_modified(board, "roles")

        await session.commit()

        # 5. Backfill.
        new_commits = 0
        last_synced_sha = repo.last_synced_sha
        if not no_backfill:
            try:
                result = await sync_repo(session, board)
                new_commits = result.new_commits
                last_synced_sha = result.last_synced_sha
                if not result.skipped:
                    print(
                        f"connect_repository: backfill complete — "
                        f"{new_commits} new commit(s), last_sha={last_synced_sha or 'none'}"
                    )
            except Exception as exc:
                # Backfill failure is non-fatal (repo may not be readable from
                # inside the container yet). Warn and continue.
                print(
                    f"connect_repository: WARNING backfill failed ({exc}) "
                    "— repo connected, sync will retry via poller"
                )

        return ConnectRepositoryResult(
            repo_id=str(repo.id),
            board_key=board.key,
            local_path=repo.local_path,
            remote_url=repo.remote_url,
            default_branch=repo.default_branch,
            provider=repo.provider,
            last_synced_sha=last_synced_sha,
            new_commits=new_commits,
            refresh_secret=minted_secret,
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="projecthub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap")
    subparsers.add_parser("seed_backlog")
    subparsers.add_parser("update_board_roles")
    repair_parser = subparsers.add_parser(
        "repair_workflow",
        help="Restore a board's active backlog->to_do transition to known-good (idempotent)",
    )
    repair_parser.add_argument("--board", required=True, help="Board key (e.g. PH)")
    board_parser = subparsers.add_parser(
        "create_board",
        help="Create a board with default workflow + roles (used by jarwis-init)",
    )
    board_parser.add_argument("--key", required=True, help="Board key (uppercase short code, e.g. MA)")
    board_parser.add_argument("--name", required=True, help='Human-readable name, e.g. "MyApp"')
    board_parser.add_argument("--description", default="", help="Optional description")
    board_parser.add_argument(
        "--project-type",
        default="web_app",
        help="Project type tag (default: web_app)",
    )
    jarwis_parser = subparsers.add_parser(
        "create_jarwis_actors",
        help="Provision per-role Jarwis sub-agent actors with isolated tokens",
    )
    jarwis_parser.add_argument("--board", default="PH", help="Board key (default: PH)")
    jarwis_parser.add_argument(
        "--mode",
        default="web",
        choices=sorted(JARWIS_MODE_ROLES),
        help="Jarwis project mode — selects implementer roles (default: web)",
    )
    jarwis_parser.add_argument(
        "--rotate",
        action="store_true",
        help="Re-mint tokens for existing actors (default: only new ones get tokens)",
    )
    jarwis_parser.add_argument(
        "--name-prefix",
        default="jarwis",
        help="Actor display_name prefix (default: jarwis -> jarwis-pm, jarwis-architect, ...)",
    )
    jarwis_parser.add_argument(
        "--json",
        action="store_true",
        help="Output minted tokens as JSON to stdout (for jarwis-init.sh consumption)",
    )
    conn_parser = subparsers.add_parser(
        "connect_repository",
        help="Connect (or reconfigure) a git repository for a board; runs initial backfill",
    )
    conn_parser.add_argument("--board", required=True, help="Board key (e.g. PH)")
    conn_parser.add_argument(
        "--local-path",
        required=True,
        help="Container-side path to the git repo (must start with /repos/)",
    )
    conn_parser.add_argument(
        "--remote",
        default=None,
        dest="remote_url",
        help="Remote URL (e.g. https://github.com/org/repo.git); optional for local provider",
    )
    conn_parser.add_argument(
        "--default-branch", default="main", help="Default branch name (default: main)"
    )
    conn_parser.add_argument(
        "--provider",
        default="local",
        choices=["local", "github", "gitlab"],
        help="Repository provider (default: local)",
    )
    conn_parser.add_argument(
        "--rotate-secret",
        action="store_true",
        help="Regenerate refresh_secret even if one already exists (invalidates installed hook)",
    )
    conn_parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip initial sync_repo backfill (for testing or when repo not yet mounted)",
    )
    conn_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit result as JSON to stdout (pipe-friendly; includes refresh_secret if minted)",
    )
    args = parser.parse_args()

    if args.command == "bootstrap":
        asyncio.run(bootstrap())
    elif args.command == "seed_backlog":
        asyncio.run(seed_backlog())
    elif args.command == "update_board_roles":
        asyncio.run(update_board_roles())
    elif args.command == "repair_workflow":
        asyncio.run(repair_workflow(args.board))
    elif args.command == "create_board":
        asyncio.run(
            create_board(
                args.key,
                args.name,
                description=args.description,
                project_type=args.project_type,
            )
        )
    elif args.command == "create_jarwis_actors":
        tokens = asyncio.run(
            create_jarwis_actors(
                args.board,
                name_prefix=args.name_prefix,
                rotate=args.rotate,
                mode=args.mode,
            )
        )
        new_tokens = {role: tok for role, tok in tokens.items() if tok}
        if args.json:
            # Machine-readable: emit only newly minted tokens; empty dict if none.
            print(_json.dumps(new_tokens))
        elif new_tokens:
            print()
            print("=" * 60)
            print("NEW TOKENS — wire these into project's .mcp.json now.")
            print("They will NEVER be printed again. Store the file outside git.")
            print("=" * 60)
            for role, tok in new_tokens.items():
                print(f"  {role:14s}  {tok}")
            print()
            print("Suggested .mcp.json entry per role:")
            print('  "project-hub-<role>": {')
            print('    "type": "http",')
            print('    "url": "http://localhost:8000/mcp",')
            print('    "headers": {"Authorization": "Bearer <token>"}')
            print("  }")
        else:
            print("No new tokens minted (all actors pre-existed; pass --rotate to refresh).")
    elif args.command == "connect_repository":
        result = asyncio.run(
            connect_repository(
                args.board,
                args.local_path,
                remote_url=args.remote_url,
                default_branch=args.default_branch,
                provider=args.provider,
                rotate_secret=args.rotate_secret,
                no_backfill=args.no_backfill,
            )
        )
        if args.output_json:
            # Machine-readable output — include refresh_secret only when minted.
            out: dict[str, Any] = {
                "repo_id": result.repo_id,
                "board_key": result.board_key,
                "local_path": result.local_path,
                "remote_url": result.remote_url,
                "default_branch": result.default_branch,
                "provider": result.provider,
                "last_synced_sha": result.last_synced_sha,
                "new_commits": result.new_commits,
                "refresh_secret": result.refresh_secret,  # None if not minted this run
            }
            print(_json.dumps(out))
        else:
            print()
            print("=" * 60)
            print(f"Repository connected for board: {result.board_key}")
            print(f"  repo_id:       {result.repo_id}")
            print(f"  local_path:    {result.local_path}")
            print(f"  remote_url:    {result.remote_url or '(none)'}")
            print(f"  provider:      {result.provider}")
            print(f"  default_branch:{result.default_branch}")
            print(f"  new_commits:   {result.new_commits}")
            print(f"  last_synced:   {result.last_synced_sha or '(none)'}")
            if result.refresh_secret:
                print()
                print("  refresh_secret (NEW — store securely, not in git):")
                print(f"    {result.refresh_secret}")
                print()
                print("  Install the git hook:")
                print(
                    f"    ./scripts/install-git-hook.sh <repo-path> {result.board_key} "
                    f"http://localhost:8000 {result.refresh_secret}"
                )
            else:
                print("  refresh_secret: (unchanged — use --rotate-secret to regenerate)")
            print("=" * 60)


if __name__ == "__main__":
    main()
