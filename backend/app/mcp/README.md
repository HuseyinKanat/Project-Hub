# mcp/

MCP server implementation.

- `server.py` — FastAPI router, JSON-RPC handler, auth middleware.
- `registry.py` — `@register_tool` decorator + tool catalog.
- `tools/` — one module per tool. See `skills.md` § 1.

Tool naming: `<verb>_<resource>[_<qualifier>]`.
Response shape always includes `_links` for next-step discovery.
