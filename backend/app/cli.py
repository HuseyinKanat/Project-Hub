"""ProjectHub operational CLI."""

import argparse
import asyncio
import copy
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.db.session import SessionLocal
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# Roles wired into Jarwis sub-agent isolation. Each gets its own actor + token
# + board membership; agents authenticate exclusively as their assigned role.
# See contracts/git.md §6 and templates/project-CLAUDE.md.
JARWIS_ROLES: list[str] = ["pm", "architect", "backend_dev", "frontend_dev", "reviewer", "qa"]

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

        existing_titles = set(
            row[0]
            for row in (
                await session.execute(
                    select(Ticket.title).where(Ticket.board_id == board.id)
                )
            ).all()
        )

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
    session: AsyncSession | None = None,
) -> dict[str, str]:
    """Provision per-role Jarwis sub-agent actors with isolated tokens.

    For each role in JARWIS_ROLES, ensures an Actor named
    ``<name_prefix>-<role>`` exists and has membership on the target board with
    that role. If the actor is new (or ``rotate=True``), a fresh random token
    is minted, hashed into ``token_hash``, and the plain token is collected
    into the return dict for the operator to wire into .mcp.json.

    Returns ``{role: plain_token}`` only for actors whose token was minted in
    this call. Existing actors without rotation get an empty placeholder so
    the operator knows they're already provisioned.
    """

    async def _run(sess: AsyncSession, *, owned: bool) -> dict[str, str]:
        settings = get_settings()
        board = (
            await sess.execute(select(Board).where(Board.key == board_key.upper()))
        ).scalar_one_or_none()
        if board is None:
            print(f"create_jarwis_actors: board {board_key!r} not found, aborting.")
            return {}

        tokens: dict[str, str] = {}
        for role in JARWIS_ROLES:
            actor_name = f"{name_prefix}-{role.replace('_dev', '')}"
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="projecthub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap")
    subparsers.add_parser("seed_backlog")
    subparsers.add_parser("update_board_roles")
    jarwis_parser = subparsers.add_parser(
        "create_jarwis_actors",
        help="Provision per-role Jarwis sub-agent actors with isolated tokens",
    )
    jarwis_parser.add_argument("--board", default="PH", help="Board key (default: PH)")
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
    args = parser.parse_args()

    if args.command == "bootstrap":
        asyncio.run(bootstrap())
    elif args.command == "seed_backlog":
        asyncio.run(seed_backlog())
    elif args.command == "update_board_roles":
        asyncio.run(update_board_roles())
    elif args.command == "create_jarwis_actors":
        tokens = asyncio.run(
            create_jarwis_actors(
                args.board, name_prefix=args.name_prefix, rotate=args.rotate
            )
        )
        new_tokens = {role: tok for role, tok in tokens.items() if tok}
        if new_tokens:
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


if __name__ == "__main__":
    main()
