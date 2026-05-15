# Agent Rules — ProjectHub

> Bu dosya, ProjectHub kod tabanı üzerinde çalışan **her agent'ın uyması zorunlu** kurallarını içerir. Kurallar `MUST` (zorunlu), `MUST NOT` (yasak), `SHOULD` (kuvvetle önerilen) seviyelerinde işaretlenmiştir. Bir kuralı çiğnemeden önce **mutlaka** açık bir gerekçeyle PR açıklamasında belirt ve admin onayı al.

---

## 0. Önce oku

Her oturum başında, herhangi bir kod yazmadan önce:

1. **MUST** — Bağlandığın board'un `get_board(board_id)` çıktısını al; workflow, roles, members listesini bil.
2. **MUST** — Üstünde çalışacağın ticket'ı `get_ticket(id, include=["history", "git_activity"])` ile oku. Geçmiş aksiyonları, eski agent instance'larının yaptıklarını ve git aktivitesini gör.
3. **MUST** — Bu repo kökündeki `skills.md` dosyasını oku; tekrarlayan pattern'ler orada.
4. **MUST** — Çalışmaya başlamadan önce `claim_ticket(id)` çağır. Claim alınmamış ticket'a kod yazma.
5. **MUST** — Ürün davranışı, veri modeli, scope veya roadmap ile ilgili karar verirken `docs/project_plan.md` dosyasını kaynak kabul et.

---

## 0.1 Project Plan Kaynak Kuralı

0.1.1. **MUST** — `docs/project_plan.md`, ProjectHub için ürün ve mimari kontratın kaynak dokümanıdır. Kod, API, MCP tool, DB modeli ve UI kararı bu planla çelişemez.

0.1.2. **MUST** — Planla çelişen bir değişiklik gerekiyorsa önce `docs/project_plan.md`, ardından bu `rules.md` ve gerekiyorsa `skills.md` aynı değişiklikle güncellenir.

0.1.3. **MUST** — v1/MVP kapsamı planın `In scope` listesidir: multi-board, fixed ticket schema, workflow, MCP query/mutate/event stream, per-agent token auth, GitHub timeline ingestion, WebSocket UI, in-app notifications ve responsive web.

0.1.4. **MUST NOT** — Planın `Out of scope` listesindeki özellikleri admin onayı olmadan ekleme: email/Slack bildirimleri, custom fields, sprint planning, native mobile app, multi-tenancy/SaaS, time tracking, attachment upload, otomatik PR merge sonrası state transition.

0.1.5. **SHOULD** — Yeni kararlar planın açık bıraktığı `Open Questions` başlıklarından birine denk geliyorsa karar, gerekçesiyle birlikte `docs/project_plan.md` içinde netleştirilir.

---

## 0.2 Product & Domain Invariants

0.2.1. **MUST** — Workspace tek ve implicit kabul edilir. Birincil hiyerarşi: `Board -> Workflow/Epics/Tickets -> Comments/History/Git Activity/Agent Phase`.

0.2.2. **MUST** — Board, project ile 1:1 eşleşir. Board key 2-5 büyük harf olmalı; ticket key her zaman `<BOARD>-<n>` formatında üretilir.

0.2.3. **MUST** — Ticket type set'i v1'de sabittir: `feature`, `bug`, `task`, `epic`. Epic ayrı tablo değil, `type='epic'` olan ticket'tır; child ticket'lar `epic_id` ile bağlanır.

0.2.4. **MUST** — Ticket schema fixed kalır. Type-specific alanlar backend'de nullable kolon olarak durur; UI ve MCP response'ları yalnızca ilgili ticket type için anlamlı alanları gösterir.

0.2.5. **MUST** — `agent_phase` canlı durumdur ama tarihçe değildir. Her phase değişimi ayrıca `TicketHistory` içinde `phase_updated` olarak kalıcı yazılır.

0.2.6. **MUST** — Ticket history append-only time-based knowledge base'dir. Ticket field değişikliği, state geçişi, claim/release, comment ve git aktiviteleri aynı timeline'a interleaved yazılır.

0.2.7. **MUST** — Actor modeli human ve agent'ı ortak taşır. Agent kimliği `<provider>-<role>-<n>` formatındadır; `agent_role_hint` sadece filtreleme ipucudur, yetki kaynağı `BoardMembership.role` alanıdır.

0.2.8. **MUST** — Her board kendi role template'ini ve workflow'unu taşıyabilir. Permission değerlendirmesi board membership üzerinden yapılır.

---

## 0.3 Canonical Defaults

0.3.1. **MUST** — Default workflow state set'i planla uyumlu kalır: `backlog`, `to_do`, `in_progress`, `blocked`, `in_review`, `in_test`, `done`.

0.3.2. **MUST** — Default transition graph'ı planın §4.2 tablosuyla uyumlu olmalıdır. Yeni default transition eklenirse workflow seed, permission testleri ve dokümantasyon birlikte güncellenir.

0.3.3. **MUST** — MCP tool katalog isimleri planla uyumlu kalır: `list_boards`, `get_board`, `get_workflow`, `query_tickets`, `get_ticket`, `create_ticket`, `update_ticket`, `transition_state`, `assign_ticket`, `add_comment`, `delete_ticket`, `claim_ticket`, `release_ticket`, `update_agent_phase`, `force_release`, `create_branch_for_ticket`, `list_ticket_git_activity`, `link_pr`, `subscribe_events`, `get_recent_events`.

0.3.4. **MUST** — Agent veri çekme akışı 2-call principle'a uyar: önce `query_tickets` ile dar özet projeksiyon, sonra sadece seçilen ticket için `get_ticket(..., include=[...])`.

0.3.5. **MUST** — Event stream payload'ları reconnect/replay için monoton `event_id`, `event_type`, `ticket_id`, `ticket_key`, `actor_id`, `timestamp` ve yeterli `data` taşır.

0.3.6. **MUST** — GitHub entegrasyonu yalnızca planlanan v1 event'lerini işler: `push`, `pull_request.opened`, `pull_request.closed` merged true/false. Yeni webhook event'i eklenirse history event type, handler, HMAC doğrulama testi ve MCP/UI timeline etkisi birlikte ele alınır.

---

## 0.4 Stack & Delivery Guardrails

0.4.1. **MUST** — Backend FastAPI + Python 3.12 + SQLAlchemy 2 + Alembic + PostgreSQL 16 + Redis stack'inden sapmaz.

0.4.2. **MUST** — Frontend React 18 + Vite + TypeScript strict + shadcn/ui + Tailwind + TanStack Query + Zustand + `@dnd-kit` stack'inden sapmaz.

0.4.3. **MUST** — MCP server FastAPI içinde ortak auth ve servis katmanını kullanan route/tool grubu olarak uygulanır; ayrı deployment varsayılmaz.

0.4.4. **MUST** — UI Jira-vari board, ticket detail, activity timeline, live agent badge, claim banner, in-app notification ve responsive mobile davranışını v1 kabul kriteri olarak taşır.

0.4.5. **SHOULD** — MVP geliştirme sırası planın roadmap'iyle uyumlu ilerler: Foundation, MCP Core, Agent UX, Git Integration, Polish & Mobile.

---

## 1. Geliştirme Ortamı (Docker-first) — TEMEL KURAL

> **Bu projedeki hiçbir komut bare-metal çalıştırılmaz. HER ŞEY Docker container'ları içindedir.** Bu kural diğer tüm kuralları override eder ve istisnası yoktur.

1.1. **MUST** — Sistemin tüm servisleri (postgres, redis, backend, frontend) yalnızca `docker-compose.yml` üzerinden ayağa kalkar:
```bash
docker compose up -d        # tümünü başlat
docker compose ps           # durum
docker compose logs -f backend   # log takibi
docker compose down         # durdur
```

1.2. **MUST NOT** — Lokal Python virtualenv, pyenv, `pip install`, lokal `node_modules`, lokal Postgres, lokal Redis kurma veya kullanma. Geliştirici makinesinde yalnızca **Docker** + **git** + (opsiyonel) **bir IDE** kurulu olmalı.

1.3. **MUST** — Backend içinde komut çalıştırma:
```bash
docker compose exec backend <command>
# Örnekler:
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec backend mypy --strict app
docker compose exec backend alembic revision --autogenerate -m "add x"
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.cli bootstrap
```

1.4. **MUST** — Frontend içinde komut çalıştırma:
```bash
docker compose exec frontend <command>
# Örnekler:
docker compose exec frontend npm install <pkg>
docker compose exec frontend npm run generate:types
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run lint
```

1.5. **MUST** — `pyproject.toml` veya `package.json` değiştirildiğinde image **yeniden build** edilir:
```bash
docker compose build backend     # veya frontend
docker compose up -d backend     # yeniden başlat
```

1.6. **MUST** — Kaynak kod **volume mount** üzerinden container'a bağlıdır. Editör'de yapılan değişiklik anında container içinde görünür; `--reload` (backend) ve Vite HMR (frontend) hot-reload yapar. Container içine **manuel kod kopyalamak yasak**.

1.7. **MUST** — DB ve Redis için CLI erişimi:
```bash
docker compose exec postgres psql -U projecthub -d projecthub
docker compose exec redis redis-cli
```

1.8. **MUST NOT** — Container içinde root user ile gereksiz dosya oluşturma; host'taki dosya izinleri bozulur. Backend Dockerfile'ı non-root user ile çalışır (bunu image build setup'ında yapılandır).

1.9. **MUST** — Migration'lar container içinde çalıştırılır ve container restart sonrası persist olur (volume mount sayesinde host'taki `backend/app/db/migrations/versions/`'a yazılır).

1.10. **MUST** — Yeni bir dependency ekleme süreci:
   - `pyproject.toml` veya `package.json`'a manuel ekle (host'tan edit).
   - `docker compose build <service>` ile image'ı tazele.
   - `docker compose up -d <service>` ile container'ı yenile.
   - Yapılan değişikliği commit et (lock file dahil).

1.11. **MUST NOT** — Test'leri host'tan çalıştırmaya çalışma. `docker compose exec backend pytest` ile çalışır; PostgreSQL fixture'ı aynı network içindeki `postgres` service'e bağlanır.

1.12. **MUST** — CI/CD (ileride eklenirse) de aynı Docker setup'ını kullanır. Local dev ile CI arasında ortam farkı sıfır olmalı.

1.13. **MUST** — Mobil erişim için Tailscale **host makinede** çalışır (container içinde değil). Tunnel host'un Docker port'larına (`5173`, `8000`) bakar.

1.14. **SHOULD** — IDE içinden komut çalıştırırken `docker compose exec` prefix'ini terminal'inde otomatik alias ile kolaylaştır (örn. `dcx="docker compose exec"`). Ama dökümanda her zaman tam form yazılır.

**Bu kuralın gerekçesi:** dev ortamı, prod ortamı, CI ortamı arasında "benim makinemde çalışıyor" durumunu sıfırlamak; agent'ların farklı makine/OS konfigürasyonlarına bağımlı olmadan tutarlı sonuç üretmesi; setup süresini saniyelere indirmek.

---

## 2. Ticket & Workflow Discipline

2.1. **MUST** — Hiçbir kod değişikliği bir ticket'a bağlı olmadan yapılmamalı. "Drive-by" düzeltmeler bile bir `task` tipinde ticket gerektirir.

2.2. **MUST** — Her branch ismi `<TICKET_KEY>-<slugified-title>` formatında. Örn: `IB-980-add-auth-flow`. Manuel branch açma; `create_branch_for_ticket(id)` kullan.

2.3. **MUST** — Her commit mesajı `<type>(<TICKET_KEY>): <message>` formatında. Geçerli type'lar: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`. Örn: `feat(IB-980): scaffold auth module`.

2.4. **MUST** — State değişikliği **yalnızca** `transition_state(id, to_state)` üzerinden yapılır. DB'ye doğrudan state yazmak yasaktır; workflow engine permission ve transition validation yapar.

2.5. **MUST** — Aktif çalışırken `update_agent_phase(id, phase, message)` ile heartbeat at. En az 2 dakikada bir. Aksi halde live badge "stale" gösterilir.

2.6. **MUST** — İş bittiğinde veya bloke olduğunda `release_ticket(id)` çağır. Asla claim'i open bırakma.

2.7. **MUST NOT** — Başkasının claim'lediği ticket'a `force_release` yetkin olmadıkça dokunma. `AlreadyClaimedError` aldığında durup admin/PM'e durumu bildir.

2.8. **MUST NOT** — Workflow'un izin vermediği bir transition'ı zorlamaya çalışma. `InvalidTransitionError` aldığında doğru ara state'i bul (ör. review'dan progress'e geri dönüş gerekiyorsa önce o adımı yap).

---

## 3. Code Style & Quality

### 3.1 Python (backend)
3.1.1. **MUST** — Python 3.12, type hints **her** fonksiyon imzasında ve modül-seviyesi değişkende zorunlu.

3.1.2. **MUST** — `docker compose exec backend ruff check .` ve `docker compose exec backend mypy --strict app` temiz olmadan commit etme.

3.1.3. **MUST** — Async-by-default. Sync I/O fonksiyonları sadece `asyncio.to_thread` içinde sarmalanabilir.

3.1.4. **MUST** — SQLAlchemy 2.0 syntax (`select(...)`). Legacy `Query` API yasak.

3.1.5. **MUST NOT** — N+1 query. Relation'ları `selectinload` / `joinedload` ile eager yükle.

3.1.6. **MUST NOT** — Test'lerde gerçek DB veya gerçek GitHub API'ye git. Fixture'lar ve mock'lar kullan.

3.1.7. **SHOULD** — Public fonksiyonlara docstring; private/internal'a yorum yeterli.

### 3.2 TypeScript (frontend)
3.2.1. **MUST** — `strict: true` tsconfig. `any` yasak (`unknown` veya generic kullan).

3.2.2. **MUST** — Backend response tipleri **manuel olarak yazılmamalı**; OpenAPI/JSON schema'dan üret (`docker compose exec frontend npm run generate:types`).

3.2.3. **MUST** — TanStack Query her sunucu state'i için. `useEffect + fetch` pattern'i yasak.

3.2.4. **MUST** — Tailwind utility-first; ad-hoc CSS sadece animation/edge-case için.

3.2.5. **SHOULD** — Component dosyası ≤ 300 satır. Daha büyükse parçala.

### 3.3 Genel
3.3.1. **MUST** — Her PR'da en az bir test eklenir veya değiştirilir (kapsanan alana göre).

3.3.2. **MUST** — Migration olmadan DB schema değişikliği yasak. `docker compose exec backend alembic revision --autogenerate -m "..."` + manuel review.

3.3.3. **MUST** — Migration **reversible** olmalı (`downgrade()` doğru implement edilmiş).

3.3.4. **MUST NOT** — Generated kod (migration auto-gen, OpenAPI client) review edilmeden merge edilmez.

---

## 4. MCP Tool Development

4.1. **MUST** — Her yeni MCP tool için: input schema (Pydantic model) + output schema + permission requirement + örnek payload + birim test.

4.2. **MUST** — Tool response'ları **context-efficient** olmalı. Default'ta minimum field set; opt-in genişletme (`include`, `fields` parametreleri).

4.3. **MUST** — Error response formatı sabit:
```json
{
  "error": "<error_code>",
  "message": "<human readable>",
  "required": "<missing permission or precondition>",
  "have": [...]
}
```

4.4. **MUST** — Response'larda `_links` objesinde ilgili tool önerileri (HATEOAS-vari). Agent'ın bir sonraki adımı keşfetmesini kolaylaştırır.

4.5. **MUST NOT** — Tool description'larında dolgu/yumuşatma. Açık, imperative, ≤ 2 cümle. Örnek değer ekle.

4.6. **MUST** — Tool naming convention: `<verb>_<resource>[_<qualifier>]`. Örn: `query_tickets`, `create_branch_for_ticket`, `update_agent_phase`.

4.7. **MUST NOT** — Tek bir tool'a birden fazla sorumluluk yükleme. Eğer tool 3+ farklı kullanım için optional flag'lere muhtaçsa, ayrı tool'lara böl.

4.8. **MUST** — Yeni bir tool eklendiğinde `docs/mcp-tools.md` güncellenir.

---

## 5. Permissions

5.1. **MUST** — Her mutation endpoint'i / MCP tool'u **service layer'ında** permission check yapar. Endpoint-only check yetersizdir (internal call'lar bypass eder).

5.2. **MUST** — Permission kontrol yardımcısı:
```python
require_permission(actor=actor, action="state.transition:to_done", resource=ticket)
```
`PermissionDenied` exception fırlatır.

5.3. **MUST** — Yeni bir permission anahtarı eklendiğinde:
   - Permission grammar dökümanına eklenir (`docs/permissions.md`).
   - Default role template'lerine bilinçli olarak dahil edilir veya hariç bırakılır.
   - Test yazılır.

5.4. **MUST NOT** — `if_assignee` scope'unu unutarak generic `ticket.update_field` verme. Default role template'leri review et.

5.5. **MUST** — `permission_denied` event'i her başarısız permission check'inden sonra `TicketHistory`'ye yazılır (security audit).

---

## 6. Audit & History

6.1. **MUST** — Her field değişikliği `TicketHistory`'ye `field_changed` event olarak düşer. `old_value` ve `new_value` JSON olarak.

6.2. **MUST** — Doğrudan SQL UPDATE ile ticket field'ı değiştirilmez. Servis fonksiyonu üzerinden geçer; servis history yazımını garantiler.

6.3. **MUST** — Soft delete: `deleted_at` set edilir, satır silinmez. History korunur.

6.4. **MUST NOT** — Geçmiş history kayıtlarını silme veya değiştirme. Yalnızca insert.

6.5. **SHOULD** — Toplu (bulk) update'lerde history yazımı tek transaction içinde olmalı.

---

## 7. Git Integration

### 7.1 Branch Zorunluluğu

7.1. **MUST** — Her kod değişikliği bir ticket branch'ında yapılmalı. `create_branch_for_ticket(id)` MCP tool'u çağrılarak branch adı hesaplanır ve ticket'a kaydedilir. Manuel branch adı yazmak yasaktır.

7.2. **MUST** — Branch format: `<ticket_key_lowercase>-<slugified-title>` (ör. `ph-17-add-auth-flow`). Tool bunu otomatik üretir.

7.3. **MUST NOT** — Ticket anahtarı olmayan bir branch üzerinden iş yapma. `main`/`master`'a doğrudan commit atmak yasaktır.

### 7.2 Conventional Commit Zorunluluğu

7.4. **MUST** — Her commit mesajı şu formatta olmalı: `<type>(<TICKET_KEY>): <description>`
- Geçerli type'lar: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `ci`, `build`, `revert`
- Örnek: `feat(PH-17): add webhook handler for push events`
- Breaking change: `feat(PH-17)!: redesign ticket schema`

7.5. **MUST** — Format uyumsuz commit push edildiğinde sistem history'e `git_commit_invalid_format` event yazar. Bu uyarı ticket timeline'da görünür; commit reddedilmez ama kayıt altına alınır.

### 7.3 Test Edilmeden Merge Yasağı

7.6. **MUST NOT** — `in_test` state'inden geçmeden PR merge edilmemeli. Workflow gate'i: `in_review → in_test → done`. Bu zincir atlanamaz — `in_test` state'i olmadan `done`'a geçiş için `impact_analysis` gate'i engeller.

7.7. **MUST** — PR merge edilmeden önce ticket en az `in_review` state'inde olmalı. `in_progress` state'indeki ticket'ın PR'ı merge edilemez.

7.8. **MUST** — PR açıldığında `link_pr(ticket_id, pr_url)` MCP tool'u çağrılarak ticket'a bağlanır. Webhook varsa otomatik; yoksa manuel.

### 7.4 Bi-directional Git Link

7.9. **MUST** — GitHub push webhook, commit mesajındaki ticket key'i parse ederek `git_commit_linked` history event'i yazar. Bu event ticket timeline'da görünür.

7.10. **MUST** — Webhook endpoint: `POST /api/boards/{board_key}/webhook/github`. Board'a `webhook_secret` konfigüre edilmişse HMAC-SHA256 doğrulaması zorunlu.

7.11. **MUST** — GitHub PAT `.env` içinde saklanır, asla commit edilmez.

7.12. **MUST NOT** — v1'de PR merge → state transition otomatiği ekleme. Manuel `transition_state` kullanılır. (v2 roadmap).

### 7.5 PR Merge ve Branch Silme

7.13. **MUST NOT** — Ticket `in_review` veya `in_test` state'inde olmadan PR merge edilmemeli. `in_progress` state'indeki ticket'ın PR'ı merge edilemez. Webhook bu durumu `warning` field ile history'e kaydeder.

7.14. **MUST** — PR merge edildiğinde branch **silinmeli**. GitHub repo ayarlarında "Automatically delete head branches" aktif olmalı veya merge sonrası elle silinmeli.

7.15. **MUST** — Branch silindiğinde sistem `git_branch_deleted` history event yazar ve `ticket.branch_name` alanını temizler. Bu iki yolla tetiklenir:
  - `pull_request` `closed+merged` event'inde branch adı eşleşiyorsa
  - GitHub `delete` event'inde (branch ref, ticket key içeriyorsa)

7.16. **MUST NOT** — Merge edilmiş branch'i silmeden `done` state'ine geçilmemeli. `git_branch_deleted` event'i ticket history'de olmalı.

7.17. **SHOULD** — Merge sonrası `main`/`master`'dan yeni branch açılacaksa önce local `git pull origin main` yapılmalı; eski branch ref'i üzerinden çalışmak yasaktır.

---

## 8. Real-time & Events

8.1. **MUST** — Her state-changing servis fonksiyonu işin sonunda Redis pub-sub'a event yayınlar:
```python
await event_bus.publish(event_type="state_changed", ticket_id=..., actor_id=..., data={...})
```

8.2. **MUST** — WebSocket gateway event'leri sadece **forward** eder; iş mantığı tutmaz.

8.3. **MUST NOT** — WebSocket handler içinde DB query veya blocking I/O. Tüm veri zaten event payload'da olmalı; UI invalidate ederek refetch eder veya payload'ı doğrudan kullanır.

8.4. **MUST** — Event şeması versiyonlu. Field eklerken backward compatible (yeni field optional). Breaking change'de yeni event type adı.

---

## 9. Security

9.1. **MUST NOT** — Token, password, secret loglara yazma. Logging filter'ı kullan.

9.2. **MUST** — Actor token'ları DB'ye **hash'lenmiş** (`bcrypt` veya `argon2`) yazılır. Plaintext yalnızca generate anında bir kez kullanıcıya gösterilir.

9.3. **MUST** — Webhook secret'ı board başına unique.

9.4. **MUST** — SQL injection imkânsız olmalı: ham SQL string concat yasak. SQLAlchemy core/ORM kullan; raw SQL gerekiyorsa parametrize et (`text(":x")`).

9.5. **MUST** — User input markdown render'ı **sanitize** edilir (XSS koruması, frontend tarafında `DOMPurify` veya backend `bleach`).

9.6. **MUST NOT** — CORS'u `*` olarak aç. Localhost dev için tunel domain'ini whitelist'le.

---

## 10. Scope Discipline

10.1. **MUST NOT** — Out-of-scope feature ekleme:
   - Email/Slack notification
   - Sprint planning / time tracking
   - Native iOS/Android app
   - Custom field sistemi (board-defined fields)
   - Multi-tenant / SaaS deployment
   - Otomatik PR-merge → state transition

10.2. **MUST** — Bir feature ekleme isteği geldiğinde önce `project_plan.md` § 1.3 (in scope) ve § 1.4 (out of scope) kontrol edilir. Out of scope ise admin'e danışılır.

10.3. **MUST NOT** — "Belki sonra lazım olur" diye opsiyonel parametre eklememe. YAGNI. v2'de eklenir.

10.4. **SHOULD** — Yeni dependency eklerken iki kez düşün; mevcut stack ile yapılabiliyorsa eklemeyin.

---

## 11. Documentation

11.1. **MUST** — Public API surface (REST + MCP tools) değiştiğinde `docs/` altındaki ilgili spec dosyası **aynı PR'da** güncellenir.

11.2. **MUST** — Permission değişikliklerinde `docs/permissions.md` güncellenir.

11.3. **MUST** — Migration için commit mesajında ne yaptığı açıkça yazılır.

11.4. **SHOULD** — Karmaşık business logic için kısa bir Mermaid sequence diagram `docs/flows/` altına eklenir.

---

## 12. Testing

12.1. **MUST** — Yeni MCP tool → en az bir integration test (FastAPI `TestClient` ile).

12.2. **MUST** — Yeni permission → en az bir "allowed" ve bir "denied" test case.

12.3. **MUST** — Yeni workflow transition → unit test (allowed_roles, from→to validation).

12.4. **MUST** — Test'ler isolation içinde çalışır: her test temiz bir DB transaction veya temp schema kullanır (`pytest-postgresql` veya `pytest-asyncio` fixture'ları).

12.5. **MUST NOT** — Test'leri commit etmeden önce `docker compose exec backend pytest -x` ile container içinde çalıştırmayı atlama.

---

## 13. PR & Review

13.1. **MUST** — Her PR title'ı `<type>(<TICKET>): <description>` formatında.

13.2. **MUST** — PR description'ında:
   - Ticket linki
   - Yapılan değişikliklerin özeti (≤ 5 madde)
   - Test coverage notu
   - Breaking change varsa açıkça belirtilir

13.3. **MUST** — Migration içeren PR'lar daha dikkatli review edilir; CI'da migration'ın `upgrade()` + `downgrade()` sırasıyla çalışması test edilir.

13.4. **SHOULD** — PR boyutu < 500 satır diff. Daha büyükse stack'e böl (preparatory PR + main PR).

---

## 14. Yasaklı Pratikler (özet hatırlatma)

- ❌ Bare-metal komut çalıştırma (Docker dışında)
- ❌ Lokal Python virtualenv veya `node_modules` kurulumu
- ❌ Ticket'sız commit
- ❌ Direct state write (workflow bypass)
- ❌ Claim almadan ticket üzerinde çalışmak
- ❌ Audit log'a yazmadan field update
- ❌ Permission check'siz endpoint
- ❌ Plaintext token storage
- ❌ Unverified webhook acceptance
- ❌ `any` (TS) veya untyped Python
- ❌ N+1 query
- ❌ Out-of-scope feature ekleme
- ❌ Generated migration'ı review etmeden merge

---

## 3.5 Ticket Alanları (technical_depth, impact_analysis, test_plan, acceptance_criteria)

Ticket'ın 4 kritik alanı state transition gate'lerinde kontrol edilir. Yanlış doldurulan alanlar geliştirme kalitesini düşürür.

### 3.5.1 technical_depth — Technical Debt

3.5.1.1. **MUST** — `technical_depth` alanı ertelenen işleri (borçları) içerir; yapılacak testleri veya acceptance criteria'yı değil.

3.5.1.2. **MUST** — Format: `## Technical Debt / Ertelemeler` başlığı altında checkbox listesi.

3.5.1.3. **MUST** — FIXME notları da bu alana yazılır.

3.5.1.4. **MUST NOT** — Bu alana test senaryoları (test_plan'a yaz), implementasyon detayları (description'a yaz), veya acceptance criteria (acceptance_criteria'ya yaz) yazma.

### 3.5.2 impact_analysis — Etki Analizi

3.5.2.1. **MUST** — `impact_analysis` alanı etkilenen flow'ları, dosyaları ve kritik uyarıları içerir.

3.5.2.2. **MUST** — Format:
   - `## Etkilenen Flowlar` — hangi iş akışları değişiyor
   - `## Etkilenen Dosyalar` — yeni/değişen dosyalar listesi
   - `## Dikkat Edilecekler` — kritik uyarılar, riskler

3.5.2.3. **MUST** — Her dosya değişikliği için relative path belirt: `app/events/bus.py` gibi.

### 3.5.3 test_plan — QA Test Senaryoları

3.5.3.1. **MUST** — `test_plan` alanı QA'nın test etmesi gereken senaryoları içerir.

3.5.3.2. **MUST** — Format: numaralı liste, her madde bir test senaryosu.

3.5.3.3. **MUST** — Test senaryoları observable behavior içermeli: "Redis kapalıyken exception fırlatmamalı" gibi.

3.5.3.4. **MUST NOT** — Bu alana teknik mimari detayları (impact_analysis'a yaz) veya vazgeçişler yazma.

### 3.5.4 acceptance_criteria — Definition of Done

3.5.4.1. **MUST** — `acceptance_criteria` alanı ticket'ın tamamlandı sayılması için gerekli maddeleri içerir.

3.5.4.2. **MUST** — Format: checkbox listesi (`- [x]` veya `- [ ]`).

3.5.4.3. **MUST** — Tamamlanan maddeler `[x]`, yapılmayan/test edilmeyen maddeler `[ ]` ile işaretlenir.

3.5.4.4. **MUST** — State transition yapmadan önce (özellikle `in_test` → `done`) tüm acceptance criteria `[x]` olmalı.

3.5.4.5. **MUST NOT** — Bu alana sonradan yapılacak işleri (technical_depth içine yaz) yazma.

### 3.5.5 State Transition Gate'leri

3.5.5.1. **MUST** — Aşağıdaki transition'lar için gerekli alanlar dolu olmalı:
   - `in_progress` → `in_review`: `technical_depth` + `acceptance_criteria` (implementasyon sonrası borç notları ve DoD)
   - `in_review` → `in_test`: `test_plan` (QA senaryoları)
   - `in_test` → `done`: `impact_analysis` (etki analizi)

3.5.5.1.1. **MUST NOT** — `technical_depth` alanını `to_do` → `in_progress` geçişinde doldurmaya çalışma. Bu alan implementasyon sırasında keşfedilen gerçek teknik borçları yansıtır; henüz başlanmamış işin borcu tahmin edilemez. Gate bilerek `in_progress` → `in_review` geçişine taşınmıştır.

3.5.5.2. **MUST** — Epic tipi ticket'lar (`type="epic"`) bu gate'lerden muaf.

3.5.5.3. **MUST** — Transition öncesinde eksik alan varsa `FieldGateNotMet` hatası alınır; eksik alanları doldurup tekrar deneyin.

---

## 15. Belirsizlik Durumunda

Bir kuralın bu duruma uygulanıp uygulanmadığından emin değilsen:

1. **MUST** — `add_comment(ticket_id, body="@admin question: ...")` ile soru sor.
2. **MUST** — Sorun çözülene kadar `release_ticket` ile claim'i bırak; başkasını bloke etme.
3. **MUST NOT** — Belirsizliği kendi yorumunla aşma; kural çiğneme riski varsa dur.
