# MCP Tool Specification

ProjectHub exposes the first MCP-style HTTP surface at:

- `GET /mcp/tools`
- `POST /mcp/call/{tool_name}`

All calls require `Authorization: Bearer <token>`.

## Tool Catalog

| Tool | Input | Result |
|---|---|---|
| `list_boards` | `{}` | Board list with workflow and roles |
| `get_board` | `{ "board_id": "PH" }` | Board detail |
| `query_tickets` | `{ "board_id": "PH", "state": "backlog", "limit": 20 }` | Compact ticket list |
| `get_ticket` | `{ "id": "PH-1" }` | Ticket detail |
| `create_ticket` | Ticket create payload | Created ticket |
| `update_ticket` | `{ "id": "PH-1", "fields": { "priority": "high" } }` | Updated ticket |
| `assign_ticket` | `{ "id": "PH-1", "assignee_id": "claude-backend-1" }` | Updated ticket |
| `transition_state` | `{ "id": "PH-1", "to_state": "in_progress", "comment": "optional" }` | Updated ticket |
| `add_comment` | `{ "id": "PH-1", "body": "..." }` | Created comment |
| `delete_ticket` | `{ "id": "PH-1", "reason": "duplicate" }` | `{ "deleted": true }` |
| `claim_ticket` | `{ "id": "PH-1" }` | Claimed ticket |
| `release_ticket` | `{ "id": "PH-1" }` | Released ticket |
| `update_agent_phase` | `{ "id": "PH-1", "phase": "coding", "message": "..." }` | Updated ticket |
| `query_history` | `{ "id": "PH-1" }` | Reverse chronological activity |

## Create Ticket Example

```json
{
  "board_id": "PH",
  "type": "task",
  "title": "Add ticket search",
  "description": "Implement board-scoped ticket search.",
  "priority": "medium",
  "labels": ["mcp"]
}
```

## Update Ticket Example

```json
{
  "id": "PH-1",
  "fields": {
    "priority": "high",
    "acceptance_criteria": "- Search title and description",
    "technical_depth": "## Approach\n- Add `tsvector` index on tickets.title/description\n- Service: `services/tickets.search()` with board scoping\n- API: `/api/tickets?q=...`\n\n## Risks\n- Postgres-only (SQLite fallback uses ILIKE)\n\n## Out of scope\n- Comment search"
  }
}
```

## Error Shape

Application errors use a stable compact shape:

```json
{
  "error": "permission_denied",
  "message": "Permission denied",
  "required": "ticket.create",
  "have": ["comment.add"]
}
```
