import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, boards, git, notifications, preferences, tickets, websocket
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.events.bus import EventBus
from app.mcp import server as mcp_server
from app.services.stale_claims import stale_claim_cron

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Start EventBus for WebSocket support
    await EventBus.startup()

    # Start stale claims cron
    cron_task = asyncio.create_task(stale_claim_cron())
    try:
        yield
    finally:
        # Cleanup
        cron_task.cancel()
        try:
            await cron_task
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
app.include_router(boards.router)
app.include_router(tickets.router)
app.include_router(notifications.router)
app.include_router(preferences.router)
app.include_router(git.router)
app.include_router(mcp_server.router)
app.include_router(websocket.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
