# Agent Skills — ProjectHub

> Bu dosya, ProjectHub üzerinde çalışan agent'lar için **tekrarlayan pattern'leri ve how-to recipe'lerini** içerir. `rules.md` "ne yapamazsın" / "ne yapmak zorundasın"ı söyler; bu dosya **"X'i nasıl yapacaksın"ı** söyler.
> Bir görev başlangıcında ilgili skill bölümünü oku, kod yazmadan önce pattern'i kavra.

---

## Skill Index

1. [Yeni MCP tool eklemek](#1-yeni-mcp-tool-eklemek)
2. [Ticket field eklemek / değiştirmek](#2-ticket-field-eklemek--değiştirmek)
3. [Workflow state veya transition eklemek](#3-workflow-state-veya-transition-eklemek)
4. [Permission eklemek](#4-permission-eklemek)
5. [Real-time event yayınlamak](#5-real-time-event-yayınlamak)
6. [GitHub webhook event'i ingest etmek](#6-github-webhook-eventi-ingest-etmek)
7. [Frontend'de yeni real-time view eklemek](#7-frontendde-yeni-real-time-view-eklemek)
8. [Permission-aware bir endpoint yazmak](#8-permission-aware-bir-endpoint-yazmak)
9. [Agent claim/release lifecycle'ı](#9-agent-claimrelease-lifecycle)
10. [Migration yazmak](#10-migration-yazmak)
11. [MCP context'i koruyarak veri çekmek](#11-mcp-contexti-koruyarak-veri-çekmek)
12. [Audit history yazımı](#12-audit-history-yazımı)
13. [Test fixture'ları](#13-test-fixtureları)
14. [Custom exception ve error response](#14-custom-exception-ve-error-response)
15. [Project plan'dan implementation scope çıkarmak](#15-project-plandan-implementation-scope-çıkarmak)
16. [Planı domain model ve migration'a çevirmek](#16-planı-domain-model-ve-migrationa-çevirmek)
17. [Planı MCP tool spec'ine çevirmek](#17-planı-mcp-tool-specine-çevirmek)
18. [Planı frontend iş paketine çevirmek](#18-planı-frontend-iş-paketine-çevirmek)
19. [Plan değişikliğini rules ve docs'a yansıtmak](#19-plan-değişikliğini-rules-ve-docsa-yansıtmak)
20. [Ticket alanlarını doğru doldurmak](#20-ticket-alanlarını-doğru-doldurmak)

---

## 1. Yeni MCP tool eklemek

**Adımlar:**

1. **Sorumluluğu netleştir.** Tek cümlede tool'un ne yaptığını yaz. Eğer "ve" / "veya" kullanıyorsan büyük ihtimalle iki tool'a bölünmeli.

2. **Schema yaz.** `backend/app/mcp/tools/<name>.py`:
   ```python
   from pydantic import BaseModel, Field
   from app.mcp.registry import register_tool
   from app.core.permissions import require_permission
   from app.services.tickets import TicketService

   class CreateTicketInput(BaseModel):
       board_id: str = Field(..., description="Board UUID veya key")
       type: Literal["feature", "bug", "task", "epic"]
       title: str = Field(..., max_length=200)
       description: str = ""
       priority: Literal["low", "medium", "high", "urgent"] = "medium"
       epic_id: str | None = None
       labels: list[str] = []

   class CreateTicketOutput(BaseModel):
       key: str
       id: str
       _links: dict[str, str]

   @register_tool(
       name="create_ticket",
       description="Bir board'da yeni ticket açar. Açıldığı anda 'backlog' state'inde başlar.",
       permission="ticket.create",
   )
   async def create_ticket(
       input: CreateTicketInput,
       actor: Actor,
       svc: TicketService,
   ) -> CreateTicketOutput:
       require_permission(actor, "ticket.create", resource=input.board_id)
       ticket = await svc.create(actor=actor, **input.model_dump())
       return CreateTicketOutput(
           key=ticket.key,
           id=str(ticket.id),
           _links={
               "self": f"get_ticket?id={ticket.key}",
               "claim": f"claim_ticket?id={ticket.key}",
           },
       )
   ```

3. **Test yaz.** `backend/tests/mcp/test_create_ticket.py`:
   - Happy path
   - Permission denied
   - Invalid input (Pydantic validation)
   - Out-of-scope value (ör. unknown type)

4. **Docs güncelle.** `docs/mcp-tools.md` — alfabetik sırayla input/output schema + örnek.

5. **`_links` ekle.** Agent'ın bir sonraki olası adımını keşfedebilmesi için response'a `_links` koy.

**Anti-pattern:**
- ❌ Bir tool ile hem read hem write yapmak.
- ❌ Optional flag'lerle aşırı esnek tool. Ayrı tool'lara böl.

---

## 2. Ticket field eklemek / değiştirmek

**Adım sırası kritik. Atlama:**

1. **Alembic migration.** (Container içinde çalıştırılır — bkz. `rules.md` § 1)
   ```bash
   docker compose exec backend alembic revision --autogenerate -m "add ticket.severity field"
   ```
   Generated migration'ı **manuel review et** (host'taki `backend/app/db/migrations/versions/`'a yazılır). `downgrade()`'i kontrol et.

2. **SQLAlchemy model.** `backend/app/db/models/ticket.py`:
   ```python
   class Ticket(Base):
       ...
       severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
   ```

3. **Pydantic schemas.** Input ve output ayrı:
   - `TicketCreateInput`, `TicketUpdateInput` (partial), `TicketResponse`
   - Field hangi type'larda görünür? `field_visibility` config'ine ekle (ör. severity sadece `bug` için).

4. **Servis update logic.** `update_ticket` servis fonksiyonu:
   - Eski değer ile karşılaştır
   - Farklıysa `TicketHistory` `field_changed` event'i yaz
   - Redis pub-sub'a event yayınla

5. **MCP tool exposure.** `update_ticket` zaten varsa schema'sına ekle. Tek bir field için ayrı tool yapma.

6. **Frontend.**
   - TypeScript type'ını OpenAPI'den regenerate et.
   - Form component'ında ilgili `TicketType` için input render et.
   - Activity timeline'da field name'in human-readable label'ını ekle.

7. **Test.**
   - Migration upgrade + downgrade.
   - Field değişiminin history'ye yazıldığını assert et.
   - Permission kontrolünün doğru çalıştığını assert et.

---

## 3. Workflow state veya transition eklemek

**Default workflow değişikliği:**

1. `backend/app/services/workflow_seed.py` — default workflow JSON'unu güncelle.
2. Migration: mevcut board'ların workflow'unu **otomatik değiştirme**; sadece default seed etkilenir. Aksi takdirde data migration ile dikkatli ilerle.

**Bir board için custom transition eklemek (runtime):**

```python
await workflow_svc.add_transition(
    workflow_id=...,
    from_state="in_test",
    to_state="in_progress",
    allowed_roles=["qa", "pm"],
)
```

**Doğrulama:**
- Cycle detection: yeni transition graph'ı invalid hale getirmesin (cycle'lar normal değil; sadece blocked↔in_progress gibi side state geçişleri kabul).
- Unreachable state olmasın.
- En az bir terminal state olmalı.

**Test:**
- Allowed role transition'ı yapabiliyor.
- Disallowed role `PermissionDenied` alıyor.
- Tanımsız transition `InvalidTransitionError` fırlatıyor.

---

## 4. Permission eklemek

1. **Grammar'a ekle.** `docs/permissions.md`:
   ```
   git.create_branch — MCP üzerinden GitHub'da branch açma izni
   ```

2. **Permission engine.** `backend/app/core/permissions.py`:
   ```python
   KNOWN_PERMISSIONS = {
       ...
       "git.create_branch",
   }
   ```

3. **Default role template'leri güncelle.** Hangi role'lere verilecek? `backend/app/services/board_templates.py`:
   ```python
   MOBILE_TEMPLATE = {
       "android_dev": {
           "permissions": [..., "git.create_branch"],
       },
       ...
   }
   ```

4. **Tool / endpoint'te kullan.**
   ```python
   require_permission(actor, "git.create_branch", resource=board)
   ```

5. **Test:**
   - Permission verilen actor → success
   - Permission verilmeyen actor → `PermissionDenied` + `permission_denied` event history'de

---

## 5. Real-time event yayınlamak

**Pattern (servis katmanında):**

```python
async def transition_state(ticket_id, to_state, actor):
    async with db.transaction():
        ticket = await get_ticket_for_update(ticket_id)  # SELECT FOR UPDATE
        old_state = ticket.state
        validate_transition(ticket.workflow, old_state, to_state, actor)
        ticket.state = to_state
        await write_history(
            ticket_id=ticket.id,
            actor_id=actor.id,
            event_type="state_changed",
            field="state",
            old_value=old_state,
            new_value=to_state,
        )
        await db.commit()

    # Transaction commit'inden SONRA event yayınla:
    await event_bus.publish(
        event_type="state_changed",
        board_id=ticket.board_id,
        ticket_id=ticket.id,
        actor_id=actor.id,
        data={"from": old_state, "to": to_state},
    )
```

**Kritik noktalar:**
- ❌ Transaction içinde event yayınlama. Commit fail olursa event yayılır, state tutarsız olur.
- ✅ Event payload **idempotent re-fetch için yeterli** olmalı: ticket key, board, değişen field'lar.
- ✅ Event ID monoton artar (database sequence veya ULID).

---

## 6. GitHub webhook event'i ingest etmek

**Yeni event type eklemek (ör. PR review comment):**

1. `backend/app/git/webhook.py` içinde event dispatcher'a case ekle:
   ```python
   elif event_type == "pull_request_review":
       await handle_pr_review(payload, board_id)
   ```

2. Handler:
   ```python
   async def handle_pr_review(payload, board_id):
       body = payload["review"]["body"] or ""
       ticket_keys = parse_ticket_keys(body) | parse_ticket_keys(payload["pull_request"]["title"])
       for key in ticket_keys:
           ticket = await get_ticket_by_key(key, board_id=board_id)
           if not ticket:
               continue
           await write_history(
               ticket_id=ticket.id,
               actor_id=GITHUB_BOT_ACTOR_ID,  # sistem actor'u
               event_type="git_pr_reviewed",
               metadata={
                   "pr_url": payload["pull_request"]["html_url"],
                   "reviewer": payload["review"]["user"]["login"],
                   "state": payload["review"]["state"],
               },
           )
           await event_bus.publish(...)
   ```

3. HMAC signature verification **mutlaka** dispatcher'dan önce çağrılır:
   ```python
   verify_github_signature(request.headers["X-Hub-Signature-256"], body, board.webhook_secret)
   ```

4. Test: payload fixture'ı ile webhook endpoint'i çağır, history'de event'in yazıldığını doğrula.

---

## 7. Frontend'de yeni real-time view eklemek

**Pattern:**

```tsx
// frontend/src/pages/BoardView.tsx
function BoardView({ boardId }: { boardId: string }) {
  const queryClient = useQueryClient();
  const { data: tickets } = useTickets({ boardId });

  // WebSocket subscribe
  useWebSocketSubscription({
    filters: { boardId, eventTypes: ["state_changed", "phase_updated"] },
    onEvent: (event) => {
      if (event.event_type === "state_changed") {
        // Sadece bu ticket'ın cache'ini invalidate et
        queryClient.invalidateQueries({ queryKey: ["ticket", event.ticket_id] });
        queryClient.invalidateQueries({ queryKey: ["tickets", boardId] });
      }
      if (event.event_type === "phase_updated") {
        // Optimistic: cache'i doğrudan güncelle, refetch'e gerek yok
        queryClient.setQueryData(["ticket", event.ticket_id], (old) =>
          old ? { ...old, agent_phase: event.data } : old
        );
      }
    },
  });

  return <KanbanBoard tickets={tickets} />;
}
```

**Kurallar:**
- ✅ Yüksek frekanslı event'ler (`phase_updated`) → optimistic cache update.
- ✅ Düşük frekans (`state_changed`) → invalidate + refetch.
- ❌ Her event'te tüm board'u refetch etme.

---

## 8. Permission-aware bir endpoint yazmak

**FastAPI handler:**

```python
@router.post("/tickets/{ticket_id}/comments")
async def add_comment(
    ticket_id: str,
    body: AddCommentInput,
    actor: Actor = Depends(current_actor),
    svc: CommentService = Depends(),
):
    return await svc.add_comment(ticket_id=ticket_id, body=body.body, actor=actor)
```

**Service (permission check burada):**

```python
class CommentService:
    async def add_comment(self, ticket_id, body, actor):
        ticket = await self.repo.get_or_404(ticket_id)
        require_permission(actor, "comment.add", resource=ticket)
        comment = await self.repo.create(ticket_id, actor.id, body)
        await self._write_history(ticket.id, actor.id, "comment_added", new_value={"comment_id": str(comment.id)})
        await event_bus.publish("comment_added", board_id=ticket.board_id, ticket_id=ticket.id, actor_id=actor.id, data={"comment_id": str(comment.id)})
        return comment
```

**Kurallar:**
- Permission check **service'te**, endpoint'te değil. Internal call'lar da geçer.
- Endpoint sadece transport adapter'ı; iş mantığı yok.

---

## 9. Agent claim/release lifecycle

**Doğru kullanım:**

```python
# Agent oturumu başında:
ticket = await mcp.claim_ticket(id="IB-980")  # AlreadyClaimedError olabilir

try:
    # Heartbeat coroutine (her 60 saniyede)
    async def heartbeat():
        while not done.is_set():
            await mcp.update_agent_phase(
                ticket_id="IB-980",
                phase=current_phase,
                message=current_message,
            )
            await asyncio.sleep(60)

    asyncio.create_task(heartbeat())

    # Faz geçişleri:
    current_phase = "planning"; current_message = "Analyzing dependencies"
    # ... analiz ...

    current_phase = "coding"; current_message = "Implementing JWT middleware"
    branch = await mcp.create_branch_for_ticket(id="IB-980")
    # ... commit'ler dış git ile yapılır ...

    current_phase = "reviewing"; current_message = "Self-review before PR"
    await mcp.transition_state(id="IB-980", to_state="in_review")
finally:
    done.set()
    await mcp.release_ticket(id="IB-980")
```

**Edge case'ler:**
- Exception olursa: `finally` bloğunda mutlaka release.
- Workflow `done` veya `blocked` state'ine geçtiğinde server zaten auto-release yapar; agent ek release çağırırsa idempotent (no-op).
- Eski instance'ın claim'i hâlâ duruyorsa (crash sonrası): admin'e bildir; `force_release` admin'e ait.

---

## 10. Migration yazmak

**Reversible migration pattern:**

```python
# backend/app/db/migrations/versions/2026_05_13_1430_add_severity.py
def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("severity", sa.String(20), nullable=True),
    )
    op.create_index("ix_tickets_severity", "tickets", ["severity"])

def downgrade() -> None:
    op.drop_index("ix_tickets_severity", "tickets")
    op.drop_column("tickets", "severity")
```

**Data migration örneği:**

```python
def upgrade() -> None:
    op.add_column("tickets", sa.Column("severity", sa.String(20), nullable=True))
    # Eski bug ticket'larına default "medium" ata:
    op.execute("UPDATE tickets SET severity = 'medium' WHERE type = 'bug' AND severity IS NULL")
```

**Çoklu adım migration:** Backward-incompatible değişikliklerde 3 PR/migration ile expand-contract:
1. **Expand**: yeni kolonu nullable ekle, kod hem eski hem yeniyi okur.
2. **Backfill**: data migration, eski satırları doldur.
3. **Contract**: kolonu NOT NULL yap, kod yalnızca yeniyi okur.

**Kurallar:**
- ❌ Production'da `op.drop_column` direkt yapma; önce kullanılmadığından emin ol.
- ❌ Migration içinde ORM model import etme (model değişirse eski migration kırılır). `op` API'sini veya `sa.Table` literal'ini kullan.

---

## 11. MCP context'i koruyarak veri çekmek

**Anti-pattern:**
```python
# ❌ Tüm field'larla, tüm ticket'ları çek:
tickets = await mcp.query_tickets(filters={"board_id": "iqb"})
# Sonuç: context'i şişiren büyük JSON.
```

**Pattern:**
```python
# ✅ Önce özet projeksiyonla listele:
tickets = await mcp.query_tickets(
    filters={"board_id": "iqb", "state": ["to_do", "in_progress"]},
    fields=["key", "title", "state", "assignee"],
    limit=20,
    sort="-priority,created_at",
)

# ✅ Sadece ilgilenilen ticket'ı derinleştir:
ticket = await mcp.get_ticket(id="IB-980", include=["history"])
```

**Text search kullanımı:**
```python
# Filtre + text search aynı çağrıda. Filtre önce dar tutuyor, text search içeride.
tickets = await mcp.query_tickets(
    filters={"board_id": "iqb", "type": "bug"},
    text_search="auth flow",
    fields=["key", "title"],
    limit=10,
)
```

**Bilgi hiyerarşisi (sırayla iste):**
1. `list_boards()` — bir kez session başında
2. `get_board(board_id)` — workflow + roles
3. `query_tickets(...)` — özet liste
4. `get_ticket(id, include=...)` — sadece gerekli detay

**Çok dallanma olduğunda:**
- Ticket'lar arası link'leri (epic, blocks) takip etmek için ayrı `query_tickets` çağrısı yap; bir ticket'ın detayını çekerken transitively tüm bağlıları yükleme.

---

## 12. Audit history yazımı

**Helper:**

```python
async def write_history(
    *,
    ticket_id: UUID,
    actor_id: UUID,
    event_type: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    metadata: dict | None = None,
) -> None:
    history = TicketHistory(
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=event_type,
        field=field,
        old_value=json.dumps(old_value) if old_value is not None else None,
        new_value=json.dumps(new_value) if new_value is not None else None,
        metadata=metadata or {},
    )
    db.add(history)
    # Caller commit eder.
```

**Birden fazla field değişimi:**
```python
# Tek transaction içinde, her field için ayrı history satırı:
for field, (old, new) in changes.items():
    await write_history(ticket_id=ticket.id, actor_id=actor.id, event_type="field_changed", field=field, old_value=old, new_value=new)
```

**Git event'leri için actor:**
- Sistem'in `github_bot_actor` adlı sentinel actor'u var. Webhook'tan gelen event'lerde `actor_id=github_bot_actor.id`, `metadata={"github_user": "<login>"}`.

---

## 13. Test fixture'ları

**Temel fixture seti (`backend/tests/conftest.py`):**

```python
@pytest.fixture
async def db_session():
    """Her test için izolasyonlu transaction."""
    async with engine.begin() as conn:
        async with AsyncSession(conn) as session:
            yield session
            await conn.rollback()

@pytest.fixture
async def admin_actor(db_session):
    actor = Actor(kind="human", display_name="Test Admin", token_hash=hash_token("secret"))
    db_session.add(actor); await db_session.commit()
    return actor

@pytest.fixture
async def board(db_session, admin_actor):
    board = Board(key="TST", name="Test Board", roles=DEFAULT_WEB_TEMPLATE, ...)
    db_session.add(board); await db_session.commit()
    await add_membership(db_session, board, admin_actor, role="admin")
    return board

@pytest.fixture
async def backend_agent(db_session, board):
    actor = Actor(kind="agent", agent_id="claude-backend-1", display_name="Backend Agent", token_hash=hash_token("agent-token"))
    db_session.add(actor); await db_session.commit()
    await add_membership(db_session, board, actor, role="backend_dev")
    return actor

@pytest.fixture
def mcp_client(test_app):
    return TestClient(test_app)
```

**Permission test pattern:**

```python
async def test_qa_can_transition_to_test_done(qa_agent, ticket_in_test, mcp_client):
    res = mcp_client.post("/mcp/transition_state", json={"id": ticket_in_test.key, "to_state": "done"}, headers=auth(qa_agent))
    assert res.status_code == 200

async def test_backend_cannot_transition_to_test_done(backend_agent, ticket_in_test, mcp_client):
    res = mcp_client.post("/mcp/transition_state", json={"id": ticket_in_test.key, "to_state": "done"}, headers=auth(backend_agent))
    assert res.status_code == 403
    assert res.json()["error"] == "permission_denied"
```

---

## 14. Custom exception ve error response

**Exception hiyerarşisi (`backend/app/core/exceptions.py`):**

```python
class ProjectHubError(Exception):
    """Base."""
    code: str = "internal_error"
    status: int = 500

class PermissionDenied(ProjectHubError):
    code = "permission_denied"
    status = 403
    def __init__(self, required: str, have: list[str]):
        self.required = required
        self.have = have

class NotFound(ProjectHubError):
    code = "not_found"
    status = 404

class InvalidTransition(ProjectHubError):
    code = "invalid_transition"
    status = 422
    def __init__(self, from_state, to_state, allowed):
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed

class AlreadyClaimed(ProjectHubError):
    code = "already_claimed"
    status = 409
    def __init__(self, claimed_by, since):
        self.claimed_by = claimed_by
        self.since = since
```

**FastAPI exception handler:**

```python
@app.exception_handler(ProjectHubError)
async def projecthub_error_handler(request, exc: ProjectHubError):
    payload = {"error": exc.code, "message": str(exc)}
    for attr in ("required", "have", "allowed", "from_state", "to_state", "claimed_by", "since"):
        if hasattr(exc, attr):
            payload[attr] = getattr(exc, attr)
    return JSONResponse(status_code=exc.status, content=payload)
```

**Kullanım:**

```python
if ticket.claimed_by and ticket.claimed_by != actor.id:
    raise AlreadyClaimed(claimed_by=str(ticket.claimed_by), since=ticket.claimed_at.isoformat())
```

---

## Genel Çalışma Akışı (özet)

Bir ticket üzerinde çalışmaya başladığında:

```
1.  get_board(board_id)                      ← workflow ve roles'u bil
2.  get_ticket(id, include=["history"])      ← ne yapıldığını gör
3.  claim_ticket(id)                         ← lock al
4.  update_agent_phase(id, "planning", ...)  ← live badge başlat
5.  create_branch_for_ticket(id)             ← branch adı hesapla, ticket'a kaydet — ZORUNLU
6.  (heartbeat coroutine başlat)
7.  update_agent_phase(id, "coding", ...)    ← faz değişimi
8.  (kod yaz; commit: feat(PH-XX): ... — ZORUNLU format)
9.  link_pr(id, pr_url)                      ← PR açıldığında bağla
10. update_agent_phase(id, "reviewing", ...)
11. transition_state(id, "in_review")        ← gate: technical_depth + acceptance_criteria
12. release_ticket(id)
```

Tıkandığında:
- `transition_state(id, "blocked")` + `add_comment(id, "blocked by: ...")` + `release_ticket(id)`.

Belirsizlik:
- `add_comment(id, "@admin question: ...")` — admin'e sor, claim'i bırak, başka iş yap.

---

## 15. Project plan'dan implementation scope çıkarmak

`docs/project_plan.md` ürün ve mimari kaynak dokümandır. Yeni bir görev geldiğinde önce görevin planın hangi bölümüne denk geldiğini saptayın.

**Adımlar:**

1. **Scope kontrolü yap.**
   - `docs/project_plan.md` §1.3 `In scope` içinde mi?
   - §1.4 `Out of scope` içinde mi?
   - Out-of-scope ise kod yazmadan admin/PM kararı iste.

2. **MVP fazını belirle.**
   - Foundation: modeller, migration, Docker, temel CRUD.
   - MCP Core: tool, permission, workflow engine.
   - Agent UX: claim/release, phase heartbeat, event stream.
   - Git Integration: branch, webhook, git timeline.
   - Polish & Mobile: board UX, ticket detail, responsive, notification.

3. **Deliverable'ı küçük parçalara böl.**
   Her parça tek bir ticket'a sığmalı:
   - DB/migration
   - service logic
   - MCP/REST surface
   - frontend view/component
   - tests/docs

4. **Kabul kriterlerini plan dilinden çıkar.**
   Örnek:
   - "Full audit trail" → her field update için `TicketHistory` assert'i.
   - "MCP-first" → tool response'unda `_links`, `fields/include` projection ve permission testi.
   - "Live agent visibility" → `agent_phase` update + WebSocket event + UI badge.

**Anti-pattern:**
- ❌ Planı sadece açıklama metni gibi okuyup acceptance criteria'ya çevirmemek.
- ❌ Aynı ticket'ta Foundation + UI polish + Git integration karıştırmak.

---

## 16. Planı domain model ve migration'a çevirmek

Planın §2 `Core Concepts & Data Model` bölümü DB ve model tasarımında esas alınır.

**Entity mapping:**

| Plan entity | Backend karşılığı | Not |
|---|---|---|
| Board | `Board` SQLAlchemy model | `key`, `roles`, `workflow_id` zorunlu |
| Ticket | `Ticket` model | Fixed schema, type-specific alanlar nullable |
| Epic | `Ticket(type="epic")` | Ayrı tablo oluşturma |
| Comment | `Comment` model | Markdown body |
| TicketHistory | `TicketHistory` model | Append-only audit log |
| Actor | `Actor` model | Human + agent ortak |
| BoardMembership | join model | Board-specific role |
| Workflow | `Workflow` model | `states` + `transitions` JSON |

**Model yazarken:**

1. Plan tablosundaki field'ları birebir işaretle.
2. Primary key ve foreign key ilişkilerini kur.
3. JSON alanları için shape'i Pydantic schema'da doğrula:
   - `roles`
   - `states`
   - `transitions`
   - `agent_phase`
   - history `metadata`
4. Index'leri planın query ihtiyacına göre ekle:
   - `TicketHistory(ticket_id, created_at)`
   - `Ticket(board_id, state)`
   - `Ticket(board_id, key)`
   - `Actor(agent_id)` unique nullable
5. Migration reversible olmalı.

**Dikkat:**
- Epic için tablo açma.
- Custom fields ekleme.
- History update/delete yazma; sadece insert.
- State'i doğrudan model assignment ile değiştiren public path bırakma.

---

## 17. Planı MCP tool spec'ine çevirmek

Planın §6 `MCP Server Interface` bölümü tool katalog ve response davranışı için kaynaktır.

**Tool yazmadan önce karar ağacı:**

1. Tool read mi mutate mi?
   - Read ise context-efficient projection (`fields`, `include`, `limit`) tasarla.
   - Mutate ise service-layer permission ve history event gerekir.

2. Plan katalogunda tool var mı?
   - Varsa aynı ismi kullan.
   - Yoksa `<verb>_<resource>[_<qualifier>]` kuralıyla isimlendir ve neden yeni tool gerektiğini dokümante et.

3. Response içinde `_links` var mı?
   - `get_ticket` → `claim`, `transition_state`, `add_comment`, `list_ticket_git_activity`
   - `create_ticket` → `self`, `claim`
   - `claim_ticket` → `update_agent_phase`, `release_ticket`

4. Error kısa ve actionable mı?
   ```json
   {
     "error": "permission_denied",
     "required": "state.transition:to_done",
     "have": ["comment.add"]
   }
   ```

**Spec checklist:**

- Input Pydantic model
- Output Pydantic model
- Permission requirement
- History event
- Event bus publish
- `_links`
- Happy path test
- Permission denied test
- Invalid input test
- `docs/mcp-tools.md` güncellemesi

---

## 18. Planı frontend iş paketine çevirmek

Planın §9 `UI / UX` bölümü frontend için kabul kriteridir. İlk ekran marketing sayfası değil, kullanılabilir ProjectHub arayüzü olmalıdır.

**Board view:**

1. Board selector ve sidebar bulunur.
2. Kanban kolonları workflow state'lerinden gelir.
3. Ticket card şu minimum bilgileri taşır:
   - type icon
   - ticket key
   - title
   - assignee
   - priority
   - labels
   - live agent badge
4. Drag-drop transition yapıyorsa `transition_state` çağırır; local state'i DB bypass ile değiştirme.

**Ticket detail:**

1. Sol: title, description, type-specific fields, comments.
2. Sağ: state, assignee, priority, labels, epic, dates.
3. Alt: interleaved activity timeline; filtreler: All / Comments / History / Git.
4. Üst: claim status banner; release action sadece yetkili role için görünür.

**Real-time pattern:**

- `phase_updated` gibi yüksek frekanslı event'lerde cache'i optimistic güncelle.
- `state_changed`, `comment_added`, `git_*` gibi event'lerde ilgili query'leri invalidate et.
- Her event'te tüm board'u refetch etme.

**Mobile:**

- Kanban yatay scroll destekler.
- Ticket detail tek kolon akışa düşer.
- Tailscale/tunnel erişimi için port varsayımları `5173` ve `8000` ile uyumlu kalır.

---

## 19. Plan değişikliğini rules ve docs'a yansıtmak

Plan değişikliği yalnız başına bırakılmaz. Plan, rules, skills ve uygulama dokümanları birlikte tutarlı kalır.

**Değişiklik türüne göre güncelleme:**

| Değişiklik | Güncellenecek yerler |
|---|---|
| Yeni v1 scope | `docs/project_plan.md`, `rules.md` §0.1, ilgili skill |
| Yeni out-of-scope karar | `docs/project_plan.md`, `rules.md` §10 |
| Yeni entity/field | `docs/project_plan.md` §2, migration, model, schema, tests |
| Yeni permission | `docs/permissions.md`, `rules.md` §5, role templates, tests |
| Yeni MCP tool | `docs/project_plan.md` §6, `docs/mcp-tools.md`, `skills.md` ilgili recipe |
| Yeni workflow state/transition | `docs/project_plan.md` §4, workflow seed, tests |
| Yeni UI kabul kriteri | `docs/project_plan.md` §9, frontend component/test |

**Review soruları:**

- Bu değişiklik planın v1/MVP sınırını büyütüyor mu?
- Agent'ların context maliyetini artırıyor mu?
- Audit trail hâlâ eksiksiz mi?
- Permission ve workflow bypass edilebilir hale geliyor mu?
- Docker-first kurala aykırı yeni bir setup adımı var mı?

---

## 20. Ticket alanlarını doğru doldurmak

Ticket'ın 4 kritik alanı vardır. Bunlar state transition gate'lerinde kontrol edilir ve ticket'ın kalitesini belirler.

### 20.1 technical_depth — Technical Debt / Borç Notları

**Amaç:** Yapılan geliştirme sırasında ertelenen, sonradan yapılması gereken işlerin not düşülmesi.

**İçerik:**
```markdown
## Technical Debt / Ertelemeler
- [ ] Retry mekanizması: Redis publish fail olursa exponential backoff yok
- [ ] Event persistence: Sadece Redis, DB event_log tablosuna yazılmıyor
- [ ] WebSocket heartbeat: 30sn ping, 2x miss = disconnect yok
- [ ] Connection pooling: Her WS bağlantısı ayrı Redis sub açıyor

## FIXME
- EventBus._redis singleton thread-safety test edilmedi
- WebSocket close kodları daha granular olabilir
```

**Anti-pattern:**
- ❌ Buraya test planı yazmak (test_plan alanı var)
- ❌ Buraya implementasyon detayı yazmak (description'a yaz)
- ❌ Buraya acceptance criteria yazmak (acceptance_criteria alanı var)

---

### 20.2 impact_analysis — Etki Analizi

**Amaç:** Bu değişikliğin etkilediği flow'ları, dosyaları ve dikkat edilmesi gerekenleri belirtmek.

**İçerik:**
```markdown
## Etkilenen Flowlar
- Ticket lifecycle: create, update, transition, claim, release
- Git webhook flow (gelecekte): push eventlerinin publish edilmesi

## Etkilenen Dosyalar
- `app/events/bus.py` — yeni
- `app/services/tickets.py` — publish çağrıları eklendi
- `app/api/websocket.py` — WebSocket endpoint

## Dikkat Edilecekler
- EventBus.publish exception swallow ediyor, logları kontrol et
- Redis connection failure durumunda events kayboluyor
- Her WS bağlantısı Redis sub açıyor, scale test gerekli
```

---

### 20.3 test_plan — QA Test Senaryoları

**Amaç:** QA ekibinin (veya test yazan agent'ın) hangi senaryoları test etmesi gerektiği.

**İçerik:**
```markdown
## QA Test Senaryoları
1. EventEnvelope serialization: JSON roundtrip test
2. Publish fail-soft: Redis kapalıyken exception fırlatmamalı
3. WS auth valid token: connection kabul edilmeli
4. WS auth invalid token: 1008 policy violation ile kapanmalı
5. Board subscribe: PH boarda ticket create olunca frame almalı
6. Cross-board isolation: PH subscriber PH-2 events almamalı
```

**Anti-pattern:**
- ❌ Teknik mimari detayları (technical_depth içine yaz)
- ❌ "Her şeyi test et" gibi vazgeçişler

---

### 20.4 acceptance_criteria — Definition of Done

**Amaç:** Bu ticket'ın "tamamlandı" sayılması için hangi maddelerin checked olması gerektiği.

**İçerik:**
```markdown
## Definition of Done
- [x] EventBus.publish tüm ticket operasyonlarına entegre
- [x] EventEnvelope JSON serialization/deserialization çalışıyor
- [x] /ws/boards/{board_id} endpoint çalışıyor
- [x] Token auth (query param ve Sec-WebSocket-Protocol) çalışıyor
- [ ] Integration test: Docker Compose Redis ile event akışı
- [ ] Load test: 1000 events/sec publish latency < 10ms
```

**Kural:**
- `[x]` — Tamamlandı, test edildi
- `[ ]` — Henüz yapılmadı veya test edilmedi
- Transition yapmadan önce tüm `[ ]`ler `[x]` olmalı

---

### 20.5 State Transition Gate'leri

Ticket state'leri arası geçişlerde (özellikle `in_review` → `in_test` → `done`) bu alanlar kontrol edilir:

| Transition | Gerekli Alan | Açıklama |
|---|---|---|
| `in_progress` → `in_review` | `technical_depth` + `acceptance_criteria` | İmplementasyon borç notları + DoD maddeleri |
| `in_review` → `in_test` | `test_plan` | QA test senaryoları var mı |
| `in_test` → `done` | `impact_analysis` | Etki analizi yapılmış mı |

> `to_do` → `in_progress` artık gate yok. `technical_depth` henüz başlanmamış bir işin borcu tahmin edilemez; implementasyon sırasında doldurulan alan in_review'dan önce zorunlu hale gelir.

**Epic tipi ticket'lar** bu gate'lerden muaf (type exemption).

---

### 20.6 Çalışma Akışı

Ticket üzerinde çalışırken:

1. **Başlangıç (to_do → in_progress — gate yok):**
   - `impact_analysis` — etkilenecek dosya ve flow ön tahmini yaz
   - `acceptance_criteria` — DoD maddelerini tanımla (`[ ]` liste)

2. **Geliştirme sırasında (in_progress):**
   - `technical_depth` — keşfedilen borçları ve FIXME'leri not et
   - `acceptance_criteria` — tamamlanan maddeleri `[x]` işaretle

3. **Review öncesi (in_progress → in_review — GATE: technical_depth + acceptance_criteria):**
   - `technical_depth` dolu: gerçek borç notları yazıldı
   - `acceptance_criteria` dolu: maddeler tanımlı

4. **Test aşaması (in_review → in_test — GATE: test_plan):**
   - `test_plan` — QA senaryolarını detaylandır
   - `acceptance_criteria` — test edilen maddeleri `[x]` işaretle

5. **Bitiş (in_test → done — GATE: impact_analysis):**
   - `acceptance_criteria` tüm maddeler `[x]` olmalı
   - `impact_analysis` — gerçek etki, etkilenen dosyalar ve akışlar net
   - Sonradan yapılacaklar `technical_depth` içinde checkbox olarak kalmalı
