# Project Plan — MCP-First Local Project Management System

> Jira-vari, lokal çalışan, MCP entegrasyonu birinci sınıf vatandaş olan bir proje/ticket yönetim sistemi. Sistem admin (insan) + role-based agent'lar tarafından kullanılacak; her ticket aksiyonu, agent fazı ve git aktivitesi tek bir time-based knowledge base'de birleştirilecek.

---

## 1. Vision & Scope

### 1.1 Ne yapıyoruz?
Lokal makinede çalışan, mobile (responsive web + tunnel) ile erişilebilir bir proje yönetim sistemi. Klasik insan kullanıcıların yanı sıra **MCP üzerinden agent'ların first-class operator** olduğu, board / ticket / state / git aktivitesinin **tek bir birleşik timeline**'da yaşadığı bir sistem.

### 1.2 Kilit hedefler
- **MCP-first**: agent'lar tüm ticket lifecycle'ını minimum context maliyetiyle yönetebilmeli.
- **Full audit trail**: her field değişikliği, kim/ne zaman/neden bilgisiyle kalıcı.
- **Live agent visibility**: hangi agent şu an hangi ticket üzerinde, hangi fazda — anlık.
- **Git-as-timeline**: branch / commit / PR aktivitesi ticket history ile interleaved akar; agent + insan birlikte tek bir knowledge base oluşturur.
- **Jira-like UX**: insan kullanıcı için tanıdık board + ticket detay arayüzü.

### 1.3 In scope (v1 / MVP)
- Multi-board, role-based permissions
- Ticket types: `feature`, `bug`, `task`, `epic`
- Fixed schema fields (Jira-vari, custom field yok v1'de)
- Default workflow + per-board custom workflow tanımlama
- MCP server: query / mutate / event stream
- Per-agent token auth, role prefix'li agent_id
- GitHub entegrasyonu: branch create-from-ticket, commit/PR webhook ingestion, interleaved activity timeline
- Real-time UI (WebSocket)
- In-app notifications
- Responsive web UI (mobile via tunnel)

### 1.4 Out of scope (v1)
- Email/Slack notifications
- Custom fields (board-defined fields)
- Sprint planning (sprint kavramı yok; epic + label/milestone yeterli)
- Native iOS/Android app (responsive web yeterli)
- Multi-tenancy / SaaS deployment
- Time tracking / worklog
- Attachment upload (sadece URL referansı)
- Otomatik PR-merge → state transition (manuel, MCP üzerinden)

---

## 2. Core Concepts & Data Model

### 2.1 Hiyerarşi
```
Workspace (tek, implicit)
└── Board (= Project, 1:1)
    ├── Members (User + Agent + Role)
    ├── Workflow (states + transitions)
    ├── Epics
    └── Tickets
        ├── Comments
        ├── History (audit log)
        ├── Git Activity (commits, PRs, branches)
        └── Agent Phase (live)
```

### 2.2 Entity'ler

#### Board
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `key` | string | Ticket prefix, ör. `IB`, `IQB` |
| `name` | string | "IQBooster Mobile" |
| `description` | text | |
| `project_type` | enum | `mobile_app`, `web_app`, `backend`, `research`, `other` — sadece UI hint, izin etkisi yok |
| `workflow_id` | UUID | Bağlı workflow |
| `roles` | JSON | Board-specific role tanımları (aşağıda detay) |
| `created_at`, `updated_at`, `created_by` | | |

#### Ticket
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `key` | string | `IB-980`, board key + auto-increment |
| `board_id` | FK | |
| `type` | enum | `feature`, `bug`, `task`, `epic` |
| `title` | string | |
| `description` | markdown | |
| `state` | string | Workflow state name |
| `agent_phase` | JSON \| null | `{agent_id, phase, started_at, message}` — live badge |
| `assignee_id` | FK (User or Agent) | |
| `reporter_id` | FK | |
| `priority` | enum | `low`, `medium`, `high`, `urgent` |
| `epic_id` | FK | Nullable |
| `labels` | string[] | |
| `acceptance_criteria` | markdown | feature/task için |
| `technical_depth` | markdown | **Zorunlu (feature/task/bug):** mimari kararlar, etkilenen modüller, veri akışı, riskler, bağımlılıklar, performans/güvenlik notları. Architect/lead ya da assignee tarafından doldurulur; `to_do → in_progress` öncesi dolu olmalı. |
| `impact_analysis` | markdown | QA'in dolduracağı alan |
| `test_plan` | markdown | QA'in dolduracağı alan |
| `steps_to_reproduce` | markdown | bug için |
| `expected_behavior` | markdown | bug için |
| `actual_behavior` | markdown | bug için |
| `story_points` | int \| null | |
| `due_date` | date \| null | |
| `claimed_by` | FK \| null | Aktif claim (lock) |
| `claimed_at` | timestamp \| null | |
| `created_at`, `updated_at` | | |

> **Type-specific alanlar:** UI ve MCP'de yalnızca ilgili type'a göre form/response döner. Backend'de hepsi nullable kolon.

#### Epic
- Sadece bir `ticket` tipidir (`type='epic'`).
- Child ticket'lar `epic_id` ile ona bağlanır.
- Ayrı bir tablo yok; sadeleştirme için ticket tablosu içinde.

#### Comment
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `ticket_id` | FK | |
| `author_id` | FK | User veya Agent |
| `body` | markdown | |
| `created_at`, `edited_at` | | |

#### TicketHistory (audit log)
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `ticket_id` | FK | |
| `actor_id` | FK | User veya Agent |
| `event_type` | enum | `created`, `field_changed`, `state_changed`, `assigned`, `claimed`, `released`, `phase_updated`, `comment_added`, `git_branch_created`, `git_commit_linked`, `git_pr_opened`, `git_pr_merged`, `deleted` |
| `field` | string \| null | Hangi field değişti |
| `old_value` | JSON \| null | |
| `new_value` | JSON \| null | |
| `metadata` | JSON | Ekstra context (ör. git commit SHA) |
| `created_at` | timestamp | |

> **Her field değişikliği** burada satır olarak duruyor. Time-series query'leri için `(ticket_id, created_at)` üzerinde index.

#### Actor (User + Agent ortak tablo)
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `kind` | enum | `human`, `agent` |
| `display_name` | string | "Ahmet (Admin)" veya "claude-backend-1" |
| `agent_id` | string \| null | `claude-backend-1`, agent için unique slug |
| `agent_role_hint` | string \| null | `backend`, `qa`, `pm` — agent_id prefix'inden parse edilen, filtreleme için |
| `token_hash` | string | Auth |
| `is_active` | bool | |
| `created_at` | | |

#### BoardMembership (Actor × Board × Role)
| Field | Type | Notes |
|---|---|---|
| `board_id` | FK | |
| `actor_id` | FK | |
| `role` | string | Board'un `roles` JSON'unda tanımlı bir rol |

> Aynı actor, farklı board'larda farklı role'de olabilir.

#### Workflow
| Field | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `name` | string | |
| `states` | JSON | `[{name, category, color, is_initial, is_terminal}]` |
| `transitions` | JSON | `[{from, to, allowed_roles[]}]` |
| `is_default` | bool | Sistem default'u |

---

## 3. Roles & Permissions

### 3.1 Per-board role definition
Her board, oluşturulurken bir **role set** seçer veya tanımlar:

**Mobile App template:**
```json
{
  "roles": {
    "admin": { "permissions": ["*"] },
    "pm": { "permissions": ["ticket.create", "ticket.assign", "ticket.delete", "epic.manage", "comment.add"] },
    "architect": { "permissions": ["ticket.create", "ticket.update_field", "comment.add", "state.transition:to_review"] },
    "android_dev": { "permissions": ["ticket.update_field:if_assignee", "state.transition:if_assignee", "comment.add", "git.create_branch", "ticket.claim"] },
    "designer": { "permissions": ["ticket.update_field:if_assignee", "comment.add", "ticket.claim"] },
    "qa": { "permissions": ["ticket.update_field:impact_analysis,test_plan", "state.transition:to_test_done", "comment.add", "ticket.claim"] },
    "orchestrator": { "permissions": ["ticket.create", "ticket.assign", "comment.add"] }
  }
}
```

**Web App template:**
```json
{
  "roles": {
    "admin": { "permissions": ["*"] },
    "pm": { "...": "..." },
    "frontend_dev": { "...": "..." },
    "backend_dev": { "...": "..." },
    "qa": { "...": "..." }
  }
}
```

### 3.2 Permission grammar
Format: `<resource>.<action>[:<scope>]`

| Permission | Açıklama |
|---|---|
| `ticket.create` | Yeni ticket açma |
| `ticket.delete` | Silme |
| `ticket.assign` | Başkasına assign etme |
| `ticket.claim` | Kendine claim atma (lock) |
| `ticket.update_field` | Tüm field'ları değiştirme |
| `ticket.update_field:<f1>,<f2>` | Sadece belirli field'lar |
| `ticket.update_field:if_assignee` | Sadece assignee ise |
| `state.transition:*` | Tüm state geçişleri |
| `state.transition:to_<state>` | Belirli hedefe geçiş |
| `state.transition:if_assignee` | Sadece assignee ise |
| `comment.add` | Yorum |
| `epic.manage` | Epic CRUD |
| `git.create_branch` | MCP'den branch açma |
| `git.link_commit` | Manuel commit linkleme |
| `workflow.edit` | Workflow customization |
| `board.edit` | Board ayarları |
| `*` | Tümü (admin) |

### 3.3 Permission resolution order
1. Actor admin mi → her şey serbest.
2. Actor'un board membership'i var mı → yoksa erişim yok.
3. Role'ün permission listesine bak; `if_assignee` scope'u varsa actor == assignee kontrolü.
4. Hiçbir permission match etmezse → reddet, audit'e `permission_denied` event'i yaz.

> **Comment kuralı:** board'a üye herkes comment ekleyebilir (asgari permission), board members dışındakiler hiçbir şey yapamaz.

---

## 4. Default Workflow

### 4.1 States
| State | Category | Renk | Açıklama |
|---|---|---|---|
| `backlog` | new | gray | Yeni açıldı, henüz triage edilmedi |
| `to_do` | new | blue | Triage edildi, çalışılmaya hazır |
| `in_progress` | active | yellow | Aktif çalışılıyor |
| `blocked` | active | red | Dış bağımlılık nedeniyle duraklatıldı |
| `in_review` | active | purple | Code/design review aşamasında |
| `in_test` | active | orange | QA test ediyor |
| `done` | done | green | Tamamlandı (terminal) |

### 4.2 Transitions (default workflow)
```
backlog ──► to_do ──► in_progress ──► in_review ──► in_test ──► done
                          ▲    │            │           │
                          │    └─►blocked   │           │
                          │       │         │           │
                          └───────┘         │           │
                          (rejected from review/test) ◄─┘
```

| From | To | Allowed roles (default) |
|---|---|---|
| `backlog` | `to_do` | pm, architect |
| `to_do` | `in_progress` | assignee, pm |
| `in_progress` | `blocked` | assignee, pm |
| `blocked` | `in_progress` | assignee, pm |
| `in_progress` | `in_review` | assignee |
| `in_review` | `in_progress` | reviewer, pm |
| `in_review` | `in_test` | assignee, pm, qa |
| `in_test` | `in_progress` | qa, pm |
| `in_test` | `done` | qa, pm |
| any non-terminal | `done` | pm, admin (forced close) |

### 4.3 Custom workflow
- Her board kendi workflow'unu seçer / oluşturur.
- UI üzerinden state + transition CRUD.
- MCP üzerinden `get_workflow(board_id)` ile agent workflow'u keşfedebilir.

### 4.4 Field gate'leri (transition preconditions)
Bazı state geçişleri belirli field'ların dolu olmasını gerektirir. Backend `transition_state` çağrısında bu gate'leri zorlar; eksikse `ValidationError(missing_fields=[...])` döner.

| Transition | Required field(s) | Geçerli ticket type'ı |
|---|---|---|
| `to_do → in_progress` | `technical_depth` | feature, task, bug |
| `in_progress → in_review` | `technical_depth` (hâlâ dolu olmalı) | feature, task, bug |
| `in_review → in_test` | `test_plan` | feature, task, bug |
| `in_test → done` | `impact_analysis` | feature, task, bug |

> Epic ticket'ları için bu gate'ler uygulanmaz (epic = sadece gruplama).

---

## 5. Agent System

### 5.1 Agent identity
- **Format**: `<provider>-<role>-<n>` — ör. `claude-backend-1`, `claude-qa-2`, `gemini-pm-1`.
- `agent_role_hint` alanı prefix parse'ından gelir; filtreleme için kullanılır ama otoriter değil — gerçek role `BoardMembership.role` ile belirlenir.
- Her agent kendi `id` ve `token`'unu bilir, session başında MCP'ye attach olur.
- Aynı role'deki eski sayılar (claude-backend-1 → 2 → 3) geçmiş context taşır: yeni instance, eski instance'ın yaptığı işleri history'den okuyabilir.

### 5.2 Agent phase (live badge)
Ticket üzerinde aktif çalışan agent, fazını sürekli güncelleyebilir:

```python
update_agent_phase(
  ticket_id="IB-980",
  phase="planning",          # planning | analyzing | coding | testing | reviewing | idle
  message="Analyzing dependencies in module X"
)
```

- `ticket.agent_phase` JSON field'ı: `{agent_id, phase, message, started_at, last_heartbeat_at}`.
- UI: ticket kartı üzerinde küçük bir live badge (ör. 🟡 `claude-backend-1 · planning`).
- Heartbeat: agent her N saniyede `update_agent_phase` çağırmazsa badge "stale" görünür (`updated >5min ago`).
- Phase değişimleri `TicketHistory`'ye `phase_updated` event olarak yazılır.

### 5.3 Claim / lock mekanizması
İki agent aynı ticket üzerinde paralel çalışmasın diye:

```python
claim_ticket(ticket_id="IB-980")  # exclusive lock alır
# ... agent işini yapar ...
release_ticket(ticket_id="IB-980")  # bırakır
```

- `ticket.claimed_by` ve `claimed_at` set edilir.
- Başka bir actor `claim_ticket` çağırırsa: error `AlreadyClaimedError(claimed_by=..., since=...)`.
- Admin/PM `force_release(ticket_id)` ile lock'u kırabilir (audit'e geçer).
- State `done` veya `blocked` olduğunda claim otomatik release edilir.
- Stale claim (>4h heartbeat yok) için cron job uyarı verir veya auto-release (config).

### 5.4 Auth (lightweight)
- Her actor için bir bearer token (`token_hash` tabloda).
- MCP request header: `Authorization: Bearer <token>`.
- Token'ı admin UI'dan generate eder; agent config'ine konur.
- Token rotation manuel.
- Rate limit yok v1'de (lokal sistem).

---

## 6. MCP Server Interface

### 6.1 Tool katalog

#### Discovery / read
| Tool | Açıklama | Tipik kullanım |
|---|---|---|
| `list_boards()` | Tüm board'lar | "Hangi projeler var?" |
| `get_board(board_id)` | Board detayı + role tanımları + workflow | İlk attach |
| `get_workflow(board_id)` | State ve transition listesi | Geçişten önce |
| `list_epics(board_id)` | Aktif epic'ler | Ticket gruplama |

#### Ticket query (context-efficient)
| Tool | Açıklama |
|---|---|
| `query_tickets(filters, text_search, fields, limit, sort)` | Yapısal filtre + opsiyonel text search; sadece istenen field'ları döner |
| `get_ticket(id, include)` | Tek ticket detayı; `include=[history, comments, git_activity, agent_phase]` ile genişlik kontrolü |

**Query example:**
```python
query_tickets(
  filters={
    "board_id": "iqb",
    "state": ["to_do", "in_progress"],
    "type": "bug",
    "assignee_role": "backend",
    "labels_any": ["urgent"]
  },
  text_search="auth flow",       # title + description içinde fuzzy
  fields=["key", "title", "state", "assignee", "priority"],
  limit=20,
  sort="-priority,created_at"
)
```

**Get example:**
```python
get_ticket(
  id="IB-980",
  include=["history", "git_activity"]  # comments hariç → context tasarrufu
)
```

> **2-call principle:** agent çoğu zaman `query_tickets` ile listeyi alır, sonra ilgilendiğini `get_ticket` ile derinleştirir. Daha fazlasına nadiren gerek var.

#### Ticket mutate
| Tool | Açıklama |
|---|---|
| `create_ticket(board_id, type, title, description, fields...)` | Yeni ticket aç |
| `update_ticket(id, fields)` | Birden fazla field'ı toplu güncelle |
| `transition_state(id, to_state, comment?)` | State geçişi (permission + workflow rule check) |
| `assign_ticket(id, assignee_id)` | Assign |
| `add_comment(id, body)` | Yorum |
| `delete_ticket(id, reason)` | Silme (soft delete, history'de kalır) |

#### Agent operations
| Tool | Açıklama |
|---|---|
| `claim_ticket(id)` | Lock al |
| `release_ticket(id)` | Lock bırak |
| `update_agent_phase(id, phase, message)` | Live badge update + heartbeat |
| `force_release(id)` | Admin/PM only |

#### Git operations
| Tool | Açıklama |
|---|---|
| `create_branch_for_ticket(id, base_branch?)` | GitHub'da `IB-980-<slugified-title>` branch'i açar |
| `list_ticket_git_activity(id)` | Bu ticket'a bağlı branch / commit / PR'lar |
| `link_pr(id, pr_url)` | Manuel PR link (otomatik webhook'a ek olarak) |

#### Subscription
| Tool | Açıklama |
|---|---|
| `subscribe_events(filters)` | Server-pushed event stream — aşağıda detay |
| `get_recent_events(board_id, since, limit)` | Polling fallback / reconnect catch-up |

### 6.2 Event stream
MCP server agent'a event push'lar (server-sent stream, MCP'nin streaming response capability'si üzerinden).

**Subscribe örneği:**
```python
subscribe_events(filters={
  "board_id": "iqb",
  "event_types": ["state_changed", "assigned", "comment_added"],
  "actor_id_not": "self"  # kendi yaptıklarımı duymayayım
})
```

**Event payload:**
```json
{
  "event_id": "evt_...",
  "event_type": "state_changed",
  "ticket_id": "IB-980",
  "ticket_key": "IB-980",
  "actor_id": "claude-qa-1",
  "timestamp": "2026-05-13T12:34:56Z",
  "data": {
    "from": "in_review",
    "to": "in_test"
  }
}
```

**Reconnect / replay:**
- Her event monoton artan `event_id`.
- Agent disconnect olursa `subscribe_events(..., since_event_id=<last>)` ile kaldığı yerden devam eder.
- Server son N event'i (varsayılan 1000 veya 24h, hangisi büyükse) memory + DB'de tutar.

### 6.3 Context efficiency design notes
- `query_tickets` default olarak **özet projeksiyon** döner (key, title, state, assignee, priority). Diğer field'lar opt-in.
- `get_ticket` default'unda `include=[]` — sadece temel ticket gövdesi. History/comments/git ayrı flag.
- Error response'ları kısa ve actionable: `{ "error": "permission_denied", "required": "state.transition:to_done", "have": ["comment.add"] }`.
- Tüm tool'lar response'larda `_links: { related: [...] }` döner — agent ek bilgi gerekirse hangi tool'u çağıracağını bilir.

---

## 7. Audit & History

### 7.1 Logged events
Her şey `TicketHistory` tablosuna düşer. Aşağıdaki event'ler tutulur:

- `created`, `deleted` (soft delete)
- `field_changed` (her bir field için ayrı satır)
- `state_changed`
- `assigned`, `unassigned`
- `claimed`, `released`, `force_released`
- `phase_updated`
- `comment_added`, `comment_edited`, `comment_deleted`
- `git_branch_created`, `git_commit_linked`, `git_pr_opened`, `git_pr_merged`, `git_pr_closed`
- `permission_denied` (security audit için)

### 7.2 Retrieval
- UI: ticket detay > "Activity" sekmesinde reverse-chronological feed (filtrelenebilir).
- MCP: `get_ticket(id, include=["history"])` veya `query_history(filters)` (advanced).
- Time-in-state metric'i için `state_changed` event'leri yeterli; cycle time / lead time UI'da hesaplanır.

### 7.3 Interleaved activity timeline
Git aktiviteleri (commit, PR) `git_*` event tipleriyle aynı `TicketHistory`'ye yazılır → UI tek bir kronolojik feed gösterir:

```
2026-05-13 14:22  claude-backend-1  created branch IB-980-add-auth-flow
2026-05-13 14:25  claude-backend-1  phase → planning
2026-05-13 14:31  claude-backend-1  phase → coding
2026-05-13 14:45  github            commit b3f29a1 "feat(IB-980): scaffold auth module"
2026-05-13 15:10  github            commit 7d8e23c "feat(IB-980): impl JWT middleware"
2026-05-13 15:30  claude-backend-1  state: in_progress → in_review
2026-05-13 15:31  claude-backend-1  released claim
2026-05-13 15:32  github            PR #142 opened "feat(IB-980): auth flow"
2026-05-13 16:00  ahmet (admin)     comment: "looks good, merging"
2026-05-13 16:01  github            PR #142 merged
2026-05-13 16:05  ahmet (admin)     state: in_review → in_test
```

---

## 8. Git Integration (GitHub)

### 8.1 Repo bağlama
- Her board'a bir veya birden fazla GitHub repo bağlanabilir.
- Auth: GitHub Personal Access Token (admin tarafından girilen, sistem genelinde tutulan).
- Webhook secret per board.

### 8.2 Branch naming convention
- Format: `<TICKET_KEY>-<slugified-title>` — ör. `IB-980-add-auth-flow`.
- MCP tool: `create_branch_for_ticket(ticket_id, base_branch="main")` → GitHub API ile branch oluşturur, history'ye `git_branch_created` yazar.

### 8.3 Commit message convention
- Format: `<type>(<TICKET_KEY>): <message>` — ör. `feat(IB-980): scaffold auth module`.
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
- **Ticket linking**: regex `\b([A-Z]+-\d+)\b` ile commit message'tan ticket key parse edilir.

### 8.4 Webhook ingestion
GitHub webhook'larından dinlenecek event'ler:
- `push` → commit'lerden ticket key parse, her commit için `git_commit_linked` event.
- `pull_request.opened` → `git_pr_opened`.
- `pull_request.closed` (merged=true) → `git_pr_merged`.
- `pull_request.closed` (merged=false) → `git_pr_closed`.

**Webhook handler:** `/api/webhooks/github/<board_id>?secret=...`

### 8.5 PR → state transition
v1'de **manuel**: PR merge olduğunda sistem otomatik `in_test`'e çekmez. Agent veya admin `transition_state` ile manuel geçirir. (v2'de opsiyonel auto-transition rule).

---

## 9. UI / UX

### 9.1 Layout (Jira-vari)
```
┌─────────────────────────────────────────────────────────┐
│  ProjectHub        [Board selector ▼]   [🔔]  [👤 Admin] │
├──────────────┬──────────────────────────────────────────┤
│ Sidebar      │  Board view: Kanban columns              │
│ - Backlog    │  ┌─────┬─────┬─────┬─────┬─────┬─────┐  │
│ - Board      │  │ToDo │InPrg│Blkd │Rvw  │Test │Done │  │
│ - Epics      │  ├─────┼─────┼─────┼─────┼─────┼─────┤  │
│ - History    │  │ □   │ □ 🟡│     │ □   │ □   │ □   │  │
│ - Settings   │  │ □   │ □   │     │     │     │ □   │  │
│              │  └─────┴─────┴─────┴─────┴─────┴─────┘  │
└──────────────┴──────────────────────────────────────────┘
```

### 9.2 Ticket card
Kanban'daki kart:
- Type icon (feat/bug/task)
- Ticket key: `IB-980`
- Title
- Assignee avatar
- Priority dot
- Labels chips
- **Live badge** (varsa): `🟡 claude-backend-1 · planning` — animasyonlu nokta + agent + phase

### 9.3 Ticket detail page
- Sol: title, description, all fields, comments
- Sağ: state selector, assignee, priority, labels, epic, dates
- Alt: **Activity timeline** (interleaved git + history) — filtrelenebilir (All / Comments / History / Git)
- Üst: claim status banner — "🔒 Claimed by claude-backend-1 for 23 min · [Release] (admin only)"

### 9.4 Real-time
- WebSocket connection per browser session.
- Server push: state change, agent phase update, new comment, new git activity.
- UI optimistic update + reconcile.

### 9.5 Mobile
- Responsive web; Kanban yatayda kaydırılabilir.
- Erişim: Tailscale veya Cloudflare Tunnel — laptop'taki sistem mobile'a erişilebilir kılınır.
- v1'de native app yok.

### 9.6 Notifications (in-app)
- Çan ikonu, unread badge.
- Trigger'lar (admin için):
  - Bana atanan ticket
  - Bende olan ticket'a comment
  - Bende olan ticket'a state change
  - Bende olan ticket'ta agent phase change (opsiyonel)
- Tıklayınca ticket detail'a gider.

---

## 10. Technology Stack

| Layer | Tech | Gerekçe |
|---|---|---|
| Backend | **FastAPI** (Python 3.12) | Tercih; agent ile aynı dilde, hızlı geliştirme |
| ORM | **SQLAlchemy 2** + Alembic | Migration + tip güvenliği |
| DB | **PostgreSQL 16** | JSON field, transaction, full-text search (`tsvector`) |
| Cache / pub-sub | **Redis** | WebSocket fanout + event stream |
| MCP server | FastAPI içinde tek route grubu | Tek deployment, ortak auth |
| Real-time | **WebSocket** (FastAPI native) + Redis pub-sub | UI live updates |
| Frontend | **React 18** + Vite | |
| UI kit | **shadcn/ui** + **Tailwind** | Jira-vari UI hızlı |
| Drag-drop | `@dnd-kit` | Kanban kartı sürükle-bırak |
| State mgmt | **Zustand** + TanStack Query | Hafif, sade |
| Git integration | `PyGithub` + `httpx` | GitHub API + webhook |
| Auth (admin) | Session cookie + bcrypt | Tek admin, lokal — yeterli |
| Auth (agent) | Bearer token, hash'lenmiş (`token_hash` kolon) | Lightweight |
| Containerization | Docker Compose | Tek `up` ile çalışsın |
| Tunnel (mobile) | Tailscale (önerilen) | Güvenli, ücretsiz, hızlı |

### 10.1 Repo yapısı (önerilen)
```
project-hub/
├── backend/
│   ├── app/
│   │   ├── api/          # REST + MCP routes
│   │   ├── core/         # config, auth, permissions
│   │   ├── db/           # models, migrations
│   │   ├── mcp/          # MCP tool implementations
│   │   ├── services/     # business logic
│   │   ├── git/          # GitHub integration
│   │   └── events/       # event bus, websocket
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/        # Board, TicketDetail, Settings
│   │   ├── components/   # Kanban, TicketCard, ActivityTimeline
│   │   ├── stores/       # Zustand
│   │   ├── api/          # TanStack Query hooks
│   │   └── ws/           # WebSocket client
│   └── package.json
├── docker-compose.yml
└── README.md
```

---

## 11. Naming Conventions

| Item | Format | Örnek |
|---|---|---|
| Board key | 2-5 uppercase letters | `IB`, `IQB`, `HBX` |
| Ticket key | `<BOARD>-<n>` | `IB-980` |
| Git branch | `<TICKET>-<slug>` | `IB-980-add-auth-flow` |
| Commit msg | `<type>(<TICKET>): <message>` | `feat(IB-980): scaffold auth` |
| Agent id | `<provider>-<role>-<n>` | `claude-backend-1` |
| PR title | `<type>(<TICKET>): <description>` | `feat(IB-980): auth flow` |

---

## 12. MVP Roadmap (önerilen faz sırası)

### Phase 1 — Foundation (1-2 hafta)
- Repo iskeleti, Docker Compose, Postgres + Redis up.
- Actor / Board / Ticket / TicketHistory modelleri + migration.
- Admin auth + minimal REST API (CRUD).
- Basit React UI: board list, ticket list.

### Phase 2 — MCP Core (1-2 hafta)
- MCP server endpoint + bearer auth.
- `list_boards`, `get_board`, `query_tickets`, `get_ticket`, `create_ticket`, `update_ticket`, `transition_state`, `add_comment`.
- Workflow engine + default workflow seed.
- Permission engine + role tanımları.

### Phase 3 — Agent UX (1 hafta)
- `claim_ticket`, `release_ticket`, `update_agent_phase`.
- WebSocket + Redis pub-sub.
- UI live badge ve real-time board updates.
- `subscribe_events` MCP tool.

### Phase 4 — Git Integration (1 hafta)
- GitHub PAT + webhook config.
- `create_branch_for_ticket`.
- Webhook handler → commit/PR parsing → TicketHistory.
- Interleaved activity timeline UI.

### Phase 5 — Polish & Mobile (3-5 gün)
- Jira-vari ticket detail UX.
- Drag-drop Kanban.
- Responsive mobile.
- Tailscale setup dokümanı.
- Notifications.

### Phase 6 — Stretch
- Custom workflow editor UI.
- Custom fields.
- Sprint / milestone (eğer ihtiyaç doğarsa).
- Native iOS app.

---

## 13. Open Questions / Sonraki Adımlar

Plan netleşti, ama design doc / implementation öncesi şu noktaları konuşmamız faydalı olur:

1. **MCP transport**: stdio mu, HTTP/SSE mi? Local agent'lar (laptop'ta Claude Code gibi) için stdio kolay; remote agent için HTTP gerek. **İkisini de destekleyelim mi?**
2. **Schema migration policy**: ticket schema değiştiğinde (örn. yeni field eklendiğinde) eski ticket'ların behavior'u? Default value mı, nullable mı?
3. **Soft delete**: ticket silindiğinde history'sine ne olur — UI'dan gizlenir ama DB'de kalır mı? Tercih: kalır, sadece `deleted_at` set edilir.
4. **Backup**: Postgres dump cron'u? Lokal disk yeterli mi?
5. **Workflow validation**: custom workflow tanımlanırken cycle, unreachable state gibi durumlar valide edilsin mi (yes)?
6. **Concurrent state transitions**: aynı anda iki actor `transition_state` çağırırsa (insan + agent)? DB-level row lock + optimistic versioning.
7. **MCP tool versioning**: tool şeması değiştiğinde agent'lar nasıl haberdar olur? Versioned tool descriptions?
8. **System bootstrap**: ilk kez ayağa kalkarken default board, default workflow, admin user nasıl seed edilir? CLI komutu mu, ilk-açılış sihirbazı mı?

---

## 14. Next Deliverables

Plan onayından sonra üreteceklerimiz (önerilen sıra):

1. **System Design Document**: Component diagram, sequence diagram'lar (ticket create flow, state transition flow, agent phase flow, git webhook flow), DB schema ER diagram.
2. **MCP Tool Specification**: Her tool için input/output JSON schema, error code listesi, örnek request/response.
3. **API Specification**: OpenAPI spec'i (FastAPI'den auto-generate edilebilir, ama manuel olarak da yazılması faydalı).
4. **DB Schema & Migration Plan**: SQLAlchemy modelleri, Alembic migration sırası.
5. **Permission Matrix**: Tam role × action tablosu (default templates için).
6. **Frontend Component Tree**: React component hiyerarşisi, routing, state shape.
7. **Bootstrap & Operations Guide**: docker-compose up'tan ilk ticket'a kadar her adım; Tailscale ile mobile erişim.
