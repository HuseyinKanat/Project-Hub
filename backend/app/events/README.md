# events/

Event bus + WebSocket gateway.

- `bus.py` — Redis pub-sub wrapper (`publish`, `subscribe`).
- `websocket.py` — WebSocket endpoint, fans out events to connected clients.
- `mcp_stream.py` — MCP `subscribe_events` tool implementation; streams events to agents.

Rules: never put business logic here. This layer only **forwards**.
See `skills.md` § 5.
