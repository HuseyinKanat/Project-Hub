# State Transition Flow (with Field Gates)

**Status:** ✅ Implemented
**Code:** `backend/app/services/tickets.py::transition_ticket_state`, `TRANSITION_FIELD_GATES`
**Tests:** `backend/tests/test_ticket_lifecycle.py::test_transition_*`, `test_invalid_transition_*`, `test_*_field_gate_*`

## State Machine (default workflow)

```mermaid
stateDiagram-v2
    [*] --> backlog
    backlog --> to_do: pm / architect
    backlog --> done: pm / admin (forced)
    to_do --> in_progress: assignee / pm
    in_progress --> blocked: assignee / pm
    blocked --> in_progress: assignee / pm
    in_progress --> in_review: assignee<br/><b>requires technical_depth + acceptance_criteria</b>
    in_review --> in_progress: reviewer / pm
    in_review --> in_test: assignee / pm / qa<br/><b>requires test_plan</b>
    in_test --> in_progress: qa / pm
    in_test --> done: qa / pm<br/><b>requires impact_analysis</b>
    in_progress --> done: pm / admin (forced)
    in_review --> done: pm / admin (forced)
    in_test --> done: pm / admin (forced)
    done --> [*]
```

> **Forced-close** (`* → done` by pm/admin) field gate'lerini **bypass etmez**; `in_test → done` için `impact_analysis` halen zorunludur. Epic ticket'lar (`type='epic'`) tüm gate'lerden muaf.

## Transition Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Caller
    participant API as POST /api/tickets/{key}/transition<br/>or /mcp/call/transition_state
    participant Svc as services.tickets.transition_ticket_state
    participant WF as _transition_allowed_by_workflow
    participant Perm as require_permission
    participant Gate as _missing_gate_fields
    participant Hist as write_history
    participant DB as Postgres

    Caller->>API: { to_state, comment? }
    API->>Svc: transition(actor, ticket_id, to_state)
    Svc->>DB: SELECT ticket + board + workflow
    Svc->>WF: check (from_state → to_state, actor_roles)
    alt no matching transition or role
        WF-->>Svc: false
        Svc-->>Caller: 422 invalid_transition<br/>{ from, to, allowed[] }
    end
    Svc->>Perm: require_permission("state.transition:to_<to_state>")
    Perm-->>Svc: ok | 403 permission_denied

    Svc->>Gate: _missing_gate_fields(ticket, to_state)
    Note over Gate: TRANSITION_FIELD_GATES lookup<br/>(in_progress→in_review: technical_depth + acceptance_criteria)<br/>(in_review→in_test: test_plan)<br/>(in_test→done: impact_analysis)<br/>Epic tipinde muafiyet.
    alt missing fields
        Gate-->>Svc: ["technical_depth", ...]
        Svc-->>Caller: 422 field_gate_not_met<br/>{ transition, missing_fields }
    end

    Svc->>DB: UPDATE ticket SET state=to_state<br/>(claimed_by/at NULL if done/blocked)
    Svc->>Hist: write_history(state_changed, old, new)
    Svc->>DB: COMMIT
    Svc-->>Caller: 200 TicketResponse
```

## Field Gate Matrix

| From → To | Required field(s) | Exempt types |
|---|---|---|
| `in_progress → in_review` | `technical_depth`, `acceptance_criteria` | epic |
| `in_review → in_test` | `test_plan` | epic |
| `in_test → done` | `impact_analysis` | epic |

## Error Payloads

```json
// invalid_transition
{ "error": "invalid_transition", "from_state": "backlog", "to_state": "in_review",
  "allowed": ["to_do", "done"] }

// field_gate_not_met
{ "error": "field_gate_not_met", "transition": "in_progress->in_review",
  "missing_fields": ["technical_depth", "acceptance_criteria"] }
```
