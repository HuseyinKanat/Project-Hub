import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    actors,
    auth,
    boards,
    git,
    notifications,
    preferences,
    repositories,
    tickets,
    websocket,
)
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger
from app.events.bus import EventBus
from app.git.refresh import git_poll_cron
from app.mcp import server as mcp_server
from app.services.stale_claims import stale_claim_cron

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Start EventBus for WebSocket support
    await EventBus.startup()

    # Start stale claims cron
    cron_task = asyncio.create_task(stale_claim_cron())

    # Start git poller (G6) — skipped when disabled or interval <= 0
    poll_task: asyncio.Task[None] | None = None
    if settings.git_refresh_enabled and settings.git_poll_interval_seconds > 0:
        poll_task = asyncio.create_task(git_poll_cron())
    else:
        logger.info(
            "git_poll_cron disabled (git_refresh_enabled=%s, interval=%d)",
            settings.git_refresh_enabled,
            settings.git_poll_interval_seconds,
        )

    try:
        yield
    finally:
        # Cleanup stale claim cron
        cron_task.cancel()
        try:
            await cron_task
        except asyncio.CancelledError:
            pass

        # Cleanup git poller
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        # Cleanup EventBus
        await EventBus.cleanup()


app = FastAPI(
    title="ProjectHub",
    version="0.1.0",
    description="Local Jira-like project management with MCP-first agent integration.",
    lifespan=lifespan,
)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(actors.router)
app.include_router(boards.router)
app.include_router(tickets.router)
app.include_router(notifications.router)
app.include_router(preferences.router)
app.include_router(git.router)
app.include_router(repositories.router)  # PH-150: G1 repo config endpoints
app.include_router(mcp_server.router)
app.include_router(websocket.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
