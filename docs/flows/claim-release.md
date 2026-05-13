# Claim / Release Flow

**Status:** ✅ Implemented (force_release: ✅; stale auto-release cron: 📝 Planned)
**Code:** `backend/app/services/tickets.py::claim_ticket`, `release_ticket`, `force_release_ticket`
**Tests:** `backend/tests/test_ticket_lifecycle.py::test_claim_conflict_*`, `test_release_clears_*`

## Sequence

```mermaid
sequenceDiagram
    autonumber
    actor A as Agent A
    actor B as Agent B
    participant Svc as services.tickets
    participant DB as Postgres
    participant Hist as write_history

    A->>Svc: claim_ticket(ticket_id)
    Svc->>Svc: require_permission("ticket.claim")
    Svc->>DB: SELECT ticket
    alt already claimed by another actor
        DB-->>Svc: claimed_by=X, claimed_at=T
        Svc-->>A: 409 already_claimed { claimed_by, since }
    else free or self
        Svc->>DB: UPDATE claimed_by=A.id, claimed_at=now()
        Svc->>Hist: write_history(claimed)
        Svc-->>A: 200 TicketResponse
    end

    B->>Svc: claim_ticket(same ticket)
    Svc->>DB: SELECT ticket
    DB-->>Svc: claimed_by=A
    Svc-->>B: 409 already_claimed

    A->>Svc: release_ticket(ticket_id)
    Svc->>DB: UPDATE claimed_by=NULL, claimed_at=NULL, agent_phase=NULL
    Svc->>Hist: write_history(released)
    Svc-->>A: 200 TicketResponse
```

## Force Release (admin/PM)

```mermaid
sequenceDiagram
    actor PM as PM or Admin
    participant Svc as services.tickets.force_release_ticket
    participant Hist
    participant DB

    PM->>Svc: force_release(ticket_id)
    Svc->>Svc: require_permission("ticket.force_release")<br/>(admin '*' or pm role)
    Svc->>DB: UPDATE claimed_by=NULL, claimed_at=NULL, agent_phase=NULL
    Svc->>Hist: write_history(force_released, metadata={by: PM.id})
    Svc-->>PM: 200 TicketResponse
```

## Auto-release on state change

- `transition_ticket_state` → `done` veya `blocked` ise `claimed_by` ve `claimed_at` `NULL` yapılır.
- Bu DB transaction içinde state change ile aynı commit'te olur.

## Planned (v1.1)

- 📝 **Stale claim cron:** `claimed_at < now() - 4h` ve `agent_phase.last_heartbeat_at` eski → uyarı veya auto-release (config flag).
