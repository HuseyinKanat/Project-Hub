# System Design

> ProjectHub architecture, component diagram, and data model.

---

## Component Diagram

```mermaid
graph TB
    Client[React Frontend<br/>Vite + TanStack Query]
    
    subgraph "Backend (FastAPI)"
        API[REST API<br/>/api/*]
        WS[WebSocket Gateway<br/>/api/ws/*]
        MCP[MCP Server<br/>/api/mcp/*]
        Auth[JWT Auth<br/>Bearer Token]
        Events[Event Bus<br/>Redis Pub/Sub]
    end
    
    subgraph "Data Layer"
        PG[(PostgreSQL<br/>SQLAlchemy Async)]
        Redis[(Redis<br/>Pub/Sub + Cache)]
    end
    
    subgraph "External"
        Git[Git Webhook<br/>GitHub/GitLab]
    end
    
    Client -->|HTTP + WS| API
    Client -->|WebSocket| WS
    Client -.->|SSE Streaming| MCP
    
    API --> Auth
    API --> PG
    API --> Events
    
    WS --> Auth
    WS --> Events
    WS --> Redis
    
    MCP --> Auth
    MCP --> PG
    MCP --> Events
    
    Events --> Redis
    
    Git -->|Webhook| API
```

---

## ER Diagram

```mermaid
erDiagram
    BOARD ||--o{ TICKET : contains
    BOARD ||--o{ BOARD_MEMBERSHIP : has
    BOARD ||--o{ WORKFLOW_STATE : defines
    
    ACTOR ||--o{ BOARD_MEMBERSHIP : member_of
    ACTOR ||--o{ TICKET : reports
    ACTOR ||--o{ TICKET : assigned_to
    ACTOR ||--o{ TICKET_HISTORY : performs
    ACTOR ||--o{ COMMENT : writes
    
    TICKET ||--o{ TICKET_HISTORY : has
    TICKET ||--o{ COMMENT : has
    TICKET ||--o{ GIT_ACTIVITY : linked
    TICKET ||--o{ AGENT_PHASE : current
    
    TICKET ||--o| TICKET : parent_epic
    
    ROLE ||--o{ BOARD_MEMBERSHIP : assigned
    
    BOARD {
        uuid id PK
        string key UK
        string name
        string description
        json workflow
        timestamp created_at
    }
    
    ACTOR {
        uuid id PK
        string kind "human|agent"
        string display_name
        string email UK
        string agent_id UK
        string agent_role_hint
        timestamp created_at
    }
    
    BOARD_MEMBERSHIP {
        uuid id PK
        uuid board_id FK
        uuid actor_id FK
        uuid role_id FK
        timestamp joined_at
    }
    
    ROLE {
        uuid id PK
        string name "admin|manager|backend_dev|frontend_dev|qa"
        json permissions
    }
    
    TICKET {
        uuid id PK
        string key UK
        uuid board_id FK
        string type "epic|feature|task|bug"
        string title
        text description
        string priority
        string state
        uuid reporter_id FK
        uuid assignee_id FK
        uuid epic_id FK
        text technical_depth
        text impact_analysis
        text test_plan
        text acceptance_criteria
        timestamp created_at
        timestamp updated_at
        timestamp deleted_at "soft delete"
    }
    
    TICKET_HISTORY {
        uuid id PK
        uuid ticket_id FK
        uuid actor_id FK
        string event_type
        string field
        text old_value
        text new_value
        json event_metadata
        timestamp created_at
    }
    
    COMMENT {
        uuid id PK
        uuid ticket_id FK
        uuid actor_id FK
        text body
        timestamp created_at
    }
    
    GIT_ACTIVITY {
        uuid id PK
        uuid ticket_id FK
        string commit_hash
        string branch
        string message
        timestamp created_at
    }
    
    AGENT_PHASE {
        uuid id PK
        uuid ticket_id FK
        string agent_id
        string phase
        string message
        timestamp updated_at
    }
```

---

## API Architecture

### REST Endpoints

| Resource | Endpoints |
|---|---|
| Boards | `GET /api/boards`, `GET /api/boards/{key}` |
| Tickets | `GET /api/tickets`, `POST /api/tickets`, `GET /api/tickets/{key}` |
| | `PATCH /api/tickets/{key}`, `POST /api/tickets/{key}/transition/{state}` |
| | `POST /api/tickets/{key}/claim`, `POST /api/tickets/{key}/release` |
| | `POST /api/tickets/{key}/comments` |

### WebSocket Endpoints

| Endpoint | Purpose |
|---|---|
| `/api/ws/boards/{board_id}?token={jwt}` | Board-scoped event stream |
| `/api/ws/tickets/{ticket_id}?token={jwt}` | Ticket-scoped event stream |

### MCP Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/mcp/tools` | List available tools |
| `POST /api/mcp/call/{tool}` | Execute tool |
| `GET /api/mcp/stream/events?board_id={id}` | SSE streaming |

---

## Event Flow

```
User Action → Service Layer → EventBus.publish()
                                    ↓
                              Redis Channel
                           (board:{id}, ticket:{id})
                                    ↓
                           WebSocket Subscribers
                                    ↓
                           Frontend Update (React Query)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, TailwindCSS, TanStack Query |
| Backend | FastAPI, SQLAlchemy 2.0 (async), Pydantic |
| Database | PostgreSQL 15, Alembic migrations |
| Cache/Events | Redis 7 (pub/sub) |
| Auth | JWT Bearer tokens |

---

## References

- `docs/flows/README.md` — Flow diagrams
- `docs/permissions.md` — Permission system
- `docs/agent-workflow.md` — Ticket lifecycle
