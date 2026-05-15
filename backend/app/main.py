from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import boards, git, tickets, websocket
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.mcp import server as mcp_server

settings = get_settings()

app = FastAPI(
    title="ProjectHub",
    version="0.1.0",
    description="Local Jira-like project management with MCP-first agent integration.",
)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(boards.router)
app.include_router(tickets.router)
app.include_router(git.router)
app.include_router(mcp_server.router)
app.include_router(websocket.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
