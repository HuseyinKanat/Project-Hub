"""ProjectHub operational CLI."""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.db.session import SessionLocal
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="projecthub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap")
    args = parser.parse_args()

    if args.command == "bootstrap":
        asyncio.run(bootstrap())


if __name__ == "__main__":
    main()
