# Agent Phase (Live Badge) Flow

**Status:** 🟡 Partial
- ✅ Backend: phase update + implicit claim + history event
- 📝 WebSocket broadcast (Redis pub-sub) — yok
- 📝 UI live badge (`TicketCard`'da phase render) — yok
- 📝 Stale heartbeat görseli — yok

**Code:** `backend/app/services/tickets.py::update_agent_phase`
**Tests:** `backend/tests/test_ticket_lifecycle.py::test_update_agent_phase_implicit_claim`

## Sequence (current)

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Agent (claude-backend-1)
    participant Svc as services.tickets.update_agent_phase
    participant DB as Postgres
    participant Hist as write_history

    Agent->>Svc: update_agent_phase(ticket_id, phase, message)
    Svc->>Svc: require_permission("ticket.claim")
    Svc->>DB: SELECT ticket
    alt claimed_by != agent
        Svc->>DB: UPDATE claimed_by=agent.id, claimed_at=now()<br/>(implicit claim)
        Svc->>Hist: write_history(claimed)
    end
    Svc->>DB: UPDATE agent_phase = {<br/>  agent_id, phase, message,<br/>  started_at, last_heartbeat_at<br/>}
    Svc->>Hist: write_history(phase_updated, new_value=phase)
    Svc-->>Agent: 200 TicketResponse
```

## Planned: real-time fan-out

```mermaid
sequenceDiagram
    autonumber
    actor Agent
    participant Svc
    participant Redis as Redis pub-sub
    participant WS as WebSocket gateway
    actor UI as Browser

    Agent->>Svc: update_agent_phase(...)
    Svc->>Svc: persist + history (current flow)
    Svc->>Redis: PUBLISH events:board:<id> phase_updated
    Redis-->>WS: subscriber receives
    WS->>UI: WS frame { event: phase_updated, ticket, phase }
    UI->>UI: optimistic re-render ticket card<br/>🟡 claude-backend-1 · planning
```

## Heartbeat / Stale Badge (planned)

- Agent her ≤60s `update_agent_phase` çağırır (idempotent, sadece `last_heartbeat_at` güncellenir).
- UI'da `last_heartbeat_at` ile `now` arasındaki fark > 5dk → badge soluk + "stale" tooltip.
- > 4h ise [claim-release](./claim-release.md) cron'u opsiyonel auto-release uygular.

## Phase enum

`planning | analyzing | coding | testing | reviewing | idle` — `AgentPhaseUpdate` Pydantic Literal'inde zorlanır.
