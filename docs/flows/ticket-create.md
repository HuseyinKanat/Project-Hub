# Ticket Create Flow

**Status:** ✅ Implemented
**Code:** `backend/app/services/tickets.py::create_ticket`, `backend/app/api/tickets.py`, `backend/app/mcp/server.py`
**Tests:** `backend/tests/test_ticket_lifecycle.py::test_create_ticket_*`

## Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Caller as Agent / Human (HTTP)
    participant API as FastAPI route<br/>POST /api/tickets<br/>or /mcp/call/create_ticket
    participant Dep as deps.current_actor
    participant Svc as services.tickets.create_ticket
    participant Perm as core.permissions.require_permission
    participant DB as Postgres (asyncpg)
    participant Hist as services.history.write_history

    Caller->>API: Authorization: Bearer <token><br/>TicketCreate payload
    API->>Dep: resolve actor from token
    Dep->>DB: SELECT actors WHERE is_active
    DB-->>Dep: rows
    Dep->>Dep: bcrypt.checkpw(token, hash)
    Dep-->>API: Actor (with memberships)

    API->>Svc: create_ticket(actor, payload)
    Svc->>DB: SELECT board (by key/uuid) + workflow
    Svc->>Perm: require_permission(actor, board, "ticket.create")
    Perm-->>Svc: ok | PermissionDenied(403)

    Svc->>DB: SELECT board FOR UPDATE (lock for key seq)
    DB-->>Svc: locked_board (next_ticket_number=N)
    Svc->>Svc: key = "<BOARD>-<N>"; ++next_ticket_number
    Svc->>DB: INSERT ticket (state=initial_state, technical_depth=...)
    Svc->>Hist: write_history(event_type="created")
    Hist->>DB: INSERT ticket_history
    Svc->>DB: COMMIT
    Svc-->>API: Ticket (re-fetched with relations)
    API-->>Caller: 201 TicketResponse
```

## Notes

- **Sıra:** board key sequence için `SELECT ... FOR UPDATE` — aynı board'a paralel iki create race'ini önler.
- **Initial state:** `workflow.states` listesindeki `is_initial=true` olan; default workflow'da `backlog`.
- **technical_depth:** `TicketCreate.technical_depth` opsiyonel; create sırasında doldurulmazsa `to_do → in_progress` transition'ı `FieldGateNotMet` ile bloklanır (bkz. [state-transition](./state-transition.md)).
- **Error shape:** `permission_denied` (403, `required`/`have`), `not_found` (404), `validation_error` (Pydantic 422).
