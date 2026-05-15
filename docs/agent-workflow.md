# Agent Workflow — Ticket Lifecycle Guide

> Bu doküman, ProjectHub üzerinde bir ticket'ı **claim'den done'a** kadar çalışırken izlenmesi gereken adım adım süreci tanımlar.
> Her agent bu workflow'a uymak zorundadır.

---

## Özet: 12 Adımlık Süreç + MCP Tools

```
1.  OKU        → list_boards → get_board → get_ticket(include=[history])
2.  CLAIM      → claim_ticket → update_agent_phase(planning)
3.  PLAN       → create_branch_for_ticket → update_ticket(impact_analysis ön taslak) → transition_state(in_progress)
4.  GELİŞTİR  → update_agent_phase(coding) → commit: feat(PH-XX): ... → add_comment(progress)
5.  AC         → update_ticket(acceptance_criteria) [DoD tanımla]
6.  TEST        → (shell) pytest → update_agent_phase(reviewing)
7.  TRANSITION → update_ticket(technical_depth + acceptance_criteria) → link_pr → transition_state(in_review)
8.  REVIEW     → add_comment(review notes) → query_history (git_commit_linked olaylarını gör)
9.  TEST PLAN  → update_ticket(test_plan)
10. QA         → transition_state(in_test)
11. IMPACT     → update_ticket(impact_analysis final)
12. DONE       → transition_state(done) → release_ticket
```

**Context-Efficient İlke:** Her tool çağrısı mümkün olan en az veriyi döndürmeli. Projection kullan.

---

## Detaylı Adımlar

### Adım 1: OKU — Ticket'ı Anla (Context-Efficient)

**MCP Tools:**
```python
# 1. Board listesi (bir kereliğine session başında)
boards = await mcp.list_boards()  # sadece key, name, my_role

# 2. Seçili board'un detayı
board = await mcp.get_board(board_id)  # workflow, roles, members

# 3. Ticket'ın detaylı okuması (history dahil)
ticket = await mcp.get_ticket(id, include=["history", "git_activity"])
# ❌ include=[] boş bırakma - sadece gerekli alanları iste

# 4. Eğer git entegrasyonu varsa aktiviteyi kontrol et
if ticket.git_integration_enabled:
    git_activity = await mcp.list_ticket_git_activity(id, limit=10)
```

**Zorunlu:**
- [ ] `get_ticket(id, include=["history", "git_activity"])` ile ticket'ı oku
- [ ] Board workflow'unu `get_board(board_id)` ile kontrol et
- [ ] Önceki agent'ların yaptıklarını history'den gör
- [ ] Git activity varsa branch ve PR durumunu `list_ticket_git_activity` ile kontrol et
- [ ] `skills.md` Skill 20'yi oku (ticket alanları nasıl doldurulur)

**Context-Efficient Pattern:**
- ✅ `include=["history"]` - sadece gerekli ilişkileri iste
- ✅ `list_ticket_git_activity(limit=10)` - son 10 aktivite yeterli
- ❌ `get_ticket(id)` içeriği tahmin etme - her zaman explicit include kullan
- ❌ Tüm board'ların tüm ticket'larını çekme - sadece ilgili ticket'ı oku

**Çıktı:** Ticket'ın ne istediğini, neyin yapıldığını, neyin kaldığını bil.

---

### Adım 2: CLAIM — Ticket'ı Kilitle

**MCP Tools:**
```python
# 1. Claim dene
try:
    await mcp.claim_ticket(id)
except AlreadyClaimed as e:
    # Başka agent claim'li
    await mcp.add_comment(id, body=f"@admin Claim conflict: {e.claimed_by} since {e.since}")
    return  # Başka ticket'a geç

# 2. Phase güncelle - planning modunda
await mcp.update_agent_phase(id, phase="planning", message="Analyzing ticket scope and technical debt")
```

**Zorunlu:**
- [ ] `claim_ticket(id)` çağrısı yap
- [ ] Claim başarılıysa `update_agent_phase(id, "planning", "Analyzing ticket scope")`
- [ ] Claim başarısızsa (AlreadyClaimed): `add_comment` ile admin'e yorum at, başka ticket'a geç

**Kural:** Claim alınmadan kod yazılmaz.

---

### Adım 3: PLAN — Scope ve Impact Ön Analizi

**MCP Tools:**
```python
# impact_analysis ön taslak: neyin etkileneceğini tahmin et
await mcp.update_ticket(id, impact_analysis="""
## Etkilenen Flowlar (ön tahmin)
- Ticket lifecycle: create, update, transition, claim, release
- Git webhook flow (gelecekte)

## Etkilenen Dosyalar (ön tahmin)
- `app/events/bus.py` — yeni
- `app/services/tickets.py` — publish çağrıları eklenecek
- `app/api/websocket.py` — WebSocket endpoint

## Dikkat Edilecekler (ön tahmin)
- Redis connection failure senaryosu incelenmeli
""")

# in_progress'e geç — GATE YOK, doğrudan geçiş
await mcp.transition_state(id, "in_progress")
```

**Zorunlu:**
- [ ] `create_branch_for_ticket(id)` → branch adını hesapla ve ticket'a kaydet
- [ ] `update_ticket(id, impact_analysis="...")` ile etkilenecek alanların ön tahmini
- [ ] `transition_state(id, "in_progress")` — gate yok, direkt geçiş

**Branch kuralı:** `create_branch_for_ticket` sonucu dönen `branch_name` üzerinde çalış. Başka branch açma.

**⚠️ `technical_depth` bu adımda doldurmaya çalışma.** Bu alan implement sırasında keşfedilen borçlar içindir; Adım 7'de in_review'a geçmeden önce doldurulur.

---

### Adım 4: GELİŞTİR — Kod Yaz

**MCP Tools (Progress Tracking):**
```python
# Her faz geçişinde phase güncelle
await mcp.update_agent_phase(id, phase="coding", message="Implementing EventBus.publish method")

# Önemli ilerleme noktalarında yorum ekle
await mcp.add_comment(id, body="Migration yazıldı: app/db/migrations/versions/...")
await mcp.add_comment(id, body="EventBus singleton implementasyonu tamamlandı")

# Heartbeat (her 60 saniyede bir)
async def heartbeat():
    while not done.is_set():
        await mcp.update_agent_phase(id, phase=current_phase, message=current_task)
        await asyncio.sleep(60)
asyncio.create_task(heartbeat())
```

**Commit Formatı (ZORUNLU):**
```
feat(PH-17): add webhook handler
fix(PH-17): correct hmac verification
test(PH-17): add push event handler tests
docs(PH-17): update agent-workflow with branch rules
```
Format: `<type>(<TICKET_KEY>): <description>`. Webhook bu mesajı parse eder, history'e bağlar.

**Zorunlu:**
- [ ] Docker-first: tüm komutlar `docker compose exec backend ...` içinde
- [ ] Migration gerekliyse: `alembic revision --autogenerate`
- [ ] Model → Service → API/MCP sırasında geliştir
- [ ] Her service metodunda: permission check + history write + event publish
- [ ] Test yaz: happy path, permission denied, invalid input
- [ ] `update_agent_phase(phase="coding")` ile düzenli güncelle
- [ ] Progress yorumları için `add_comment(id, body="...")` kullan

**Context-Efficient:**
- ✅ Sadece bu ticket ile ilgili dosyalarda çalış
- ❌ Tüm codebase'i tarayıp analiz etme (scope dışı)

---

### Adım 5: AC — Acceptance Criteria Güncelle

**MCP Tools:**
```python
# acceptance_criteria güncelle
await mcp.update_ticket(id, acceptance_criteria="""
## Definition of Done
- [x] Migration yazıldı
- [x] Model güncellendi
- [x] Service metodu yazıldı
- [x] MCP tool eklendi
- [x] EventEnvelope JSON serialization çalışıyor
- [ ] Integration test: Docker Compose Redis ile event akışı
- [ ] Load test: 1000 events/sec publish latency < 10ms
""")

# Progress yorumu ekle
await mcp.add_comment(id, body="AC güncellendi: 5/8 madde tamamlandı, 3 test maddesi kaldı")
```

**Zorunlu:**
- [ ] `update_ticket(id, acceptance_criteria="...")` ile DoD maddelerini işaretle
- [ ] `[x]` tamamlanan, `[ ]` kalan maddeler
- [ ] `add_comment(id, body="...")` ile ilerleme notu ekle

**Kural:** Tamamlanan maddeler `[x]`, kalanlar `[ ]`.

---

### Adım 6: TEST — Tüm Testleri Çalıştır

**Shell (Docker-first):**
```bash
# Tüm testleri çalıştır
docker compose exec backend pytest tests/ -v --tb=short

# Spesifik test dosyası
docker compose exec backend pytest tests/test_events.py -v

# Lint ve type check
docker compose exec backend ruff check app/
docker compose exec backend mypy app/
```

**MCP Tools (sonuç bildirimi):**
```python
# Test sonuçlarını ticket'a yansıt
await mcp.update_agent_phase(id, phase="reviewing", message="All 19 tests passed, ready for review")
await mcp.add_comment(id, body="Test sonuçları: 19/19 passed, coverage 87%")
```

**Zorunlu:**
- [ ] `pytest tests/ -v --tb=short` - yeni testler pass
- [ ] Eski testler regression olmadan pass
- [ ] `ruff check app/` - lint clean
- [ ] `mypy app/` - type check clean
- [ ] `update_agent_phase(phase="reviewing")` ile durum güncelle

**Hata varsa:** Düzelt, tekrar test et, `add_comment` ile sorunu not et.

---

### Adım 7: TRANSITION — in_progress → in_review

**Bu geçişten önce iki alan doldurulmalı:**

**MCP Tools:**
```python
# 1. technical_depth: implementasyon sırasında keşfedilen borçlar
await mcp.update_ticket(id, technical_depth="""
## Technical Debt / Ertelemeler
- [ ] Retry mekanizması: Redis publish fail olursa exponential backoff yok
- [ ] Event persistence: Sadece Redis, DB event_log tablosuna yazılmıyor

## FIXME
- EventBus._redis singleton thread-safety test edilmedi
""")

# 2. acceptance_criteria: tamamlanan maddeleri işaretle
await mcp.update_ticket(id, acceptance_criteria="""
## Definition of Done
- [x] Migration yazıldı
- [x] Service metodu yazıldı
- [x] MCP tool eklendi
- [ ] Integration test: Docker Compose Redis ile event akışı
""")

# 3. Geçiş — GATE: technical_depth + acceptance_criteria zorunlu
try:
    await mcp.transition_state(id, "in_review")
    await mcp.add_comment(id, body="Code ready for review. All tests pass.")
except FieldGateNotMet as e:
    await mcp.add_comment(id, body=f"@admin Review blocked: missing {e.missing_fields}")
    # Eksik alanları doldur ve tekrar dene
```

**Zorunlu:**
- [ ] `update_ticket(id, technical_depth="...")` — gerçek borç notları yaz
- [ ] `update_ticket(id, acceptance_criteria="...")` — tamamlanan maddeler `[x]`
- [ ] `transition_state(id, "in_review")` çağrısı
- [ ] Gate kontrol: `technical_depth` **ve** `acceptance_criteria` dolu olmalı
- [ ] Başarılıysa `add_comment(id, body="...")` ile review notu

**Başarısızsa:** `FieldGateNotMet` hatasında eksik alanları `update_ticket` ile doldur, tekrar dene.

---

### Adım 8: REVIEW — Kod İncelemesi

**MCP Tools:**
```python
# Git activity kontrol et
git_activity = await mcp.list_ticket_git_activity(id, limit=5)

# Review notu ekle
await mcp.add_comment(id, body="""@admin Self-review complete:
- Migration reversible checked
- Service layer permission checks present
- Event publish after commit pattern followed
- Test coverage 87%
""")

# Peer review isteğinde bulun
await mcp.add_comment(id, body="@admin Ready for peer review. Please check error handling in edge cases.")
```

**Zorunlu:**
- [ ] Self-review: kendi kodunu oku
- [ ] `list_ticket_git_activity(id, limit=5)` ile git durumunu kontrol et
- [ ] Peer review gerekliyse: `add_comment(id, body="@admin ...")`
- [ ] `skills.md` ilgili skill'i kontrol et (uygulandı mı?)
- [ ] `rules.md` §0.1 Project Plan uyumluluğu kontrol et

---

### Adım 9: TEST PLAN — QA Senaryoları

**MCP Tools:**
```python
# test_plan doldur
await mcp.update_ticket(id, test_plan="""
## QA Test Senaryoları
1. EventEnvelope serialization: JSON roundtrip test
2. Publish fail-soft: Redis kapalıyken exception fırlatmamalı
3. WS auth valid token: connection kabul edilmeli
4. WS auth invalid token: 1008 policy violation ile kapanmalı
5. Board subscribe: PH boarda ticket create olunca frame almalı
6. Cross-board isolation: PH subscriber PH-2 events almamalı
""")

# Test plan hazır bildirimi
await mcp.add_comment(id, body="Test plan hazır: 6 QA senaryosu tanımlandı. in_test transition için hazır.")
```

**Zorunlu:**
- [ ] `update_ticket(id, test_plan="...")` ile QA senaryolarını tanımla
- [ ] `add_comment(id, body="...")` ile test plan hazır bildirimi
- [ ] Numaralı liste, her madde bir observable behavior

**Not:** Bu alan `in_review` → `in_test` transition'ı için gerekli.

---

### Adım 10: QA — in_review → in_test

**MCP Tools:**
```python
try:
    await mcp.transition_state(id, "in_test")
    await mcp.add_comment(id, body="in_test state'e geçildi. QA senaryoları uygulanıyor.")
except FieldGateNotMet as e:
    await mcp.add_comment(id, body=f"@admin test_plan eksik: {e.missing_fields}")
    # test_plan doldur ve tekrar dene
```

**Zorunlu:**
- [ ] `transition_state(id, "in_test")` çağrısı
- [ ] Gate kontrol: `test_plan` dolu olmalı
- [ ] Başarılıysa `add_comment(id, body="QA başlıyor...")`

**Sonra:** QA test senaryolarını uygula (manuel veya otomatik).

---

### Adım 11: IMPACT — Etki Analizi Kontrol

**MCP Tools (manuel kontrol):**
```python
# Ticket'ı son kez oku (include olmadan hafif okuma)
ticket = await mcp.get_ticket(id)  # sadece temel alanlar

# impact_analysis kontrol
impact = ticket.impact_analysis
# - Etkilenen flow'lar test edildi mi?
# - Etkilenen dosyalar review edildi mi?
# - Dikkat edilecekler kontrol edildi mi?

# acceptance_criteria son kontrol
ac = ticket.acceptance_criteria
# Tüm `[ ]` ler `[x]` olmalı
```

**Zorunlu:**
- [ ] `get_ticket(id)` ile temel alanları kontrol et (include kullanma - context az)
- [ ] `impact_analysis` - flow'lar ve dosyalar test/review edildi mi?
- [ ] `acceptance_criteria` - tüm maddeler `[x]` olmalı
  - Dikkat edilecekler kontrol edildi mi?
- [ ] `acceptance_criteria` tüm maddeler `[x]` olmalı

**Not:** Bu alan `in_test` → `done` transition'ı için gerekli.

---

### Adım 12: DONE — in_test → done

**MCP Tools:**
```python
# 1. Son test run (shell üzerinden)
# docker compose exec backend pytest tests/ -v

# 2. Final transition
try:
    await mcp.transition_state(id, "done")
    await mcp.add_comment(id, body="✅ Ticket tamamlandı. Tüm acceptance criteria karşılandı.")
    
    # 3. Release
    await mcp.release_ticket(id)
    await mcp.update_agent_phase(id, phase="idle", message="Ticket completed, moving to next task")
    
except FieldGateNotMet as e:
    # impact_analysis eksik olabilir (in_test → done gate'i)
    await mcp.add_comment(id, body=f"@admin Done transition blocked: {e.missing_fields}")
    # impact_analysis doldur ve tekrar dene
```

**Zorunlu:**
- [ ] Son test run: `pytest tests/ -v` - tüm testler pass
- [ ] `transition_state(id, "done")` çağrısı
- [ ] Gate kontrol: `impact_analysis` dolu olmalı (`in_test` → `done`)
- [ ] Başarılıysa: `add_comment(id, body="✅ Tamamlandı...")`
- [ ] Son olarak: `release_ticket(id)`

**Başarısızsa (gate hatası):**
1. `FieldGateNotMet` - eksik alanları gösterir
2. `update_ticket(id, impact_analysis="...")` ile eksik alanı doldur
3. Tekrar `transition_state(id, "done")` dene

---

## Checklist Özeti

Her ticket için bu 4 alan ZORUNLUDUR:

| Alan | Doldurulma Zamanı | Transition Gereksinimi |
|------|------------------|----------------------|
| `technical_depth` | Adım 3 (PLAN) | `to_do` → `in_progress` |
| `impact_analysis` | Adım 3 (PLAN) | `in_test` → `done` |
| `test_plan` | Adım 9 (TEST PLAN) | `in_review` → `in_test` |
| `acceptance_criteria` | Adım 5 (AC), güncel | `in_test` → `done` |

---

## Sık Karşılaşılan Hatalar

### 1. "FieldGateNotMet: technical_depth required"
**Çözüm:** `technical_depth` alanını doldur, borç notları ekle.

### 2. "FieldGateNotMet: test_plan required"
**Çözüm:** `test_plan` alanını doldur, QA senaryoları yaz.

### 3. "FieldGateNotMet: impact_analysis required"
**Çözüm:** `impact_analysis` alanını doldur, etkilenen flow'ları belirt.

### 4. "AlreadyClaimed"
**Çözüm:** Başka bir agent claim'li. Admin'e yorum at veya force_release iste.

### 5. Test failure
**Çözüm:** Düzelt, tekrar test et, acceptance_criteria'yı güncelle.

---

## Örnek Tam Oturum

```python
# 1. OKU
ticket = await mcp.get_ticket("PH-8", include=["history"])
board = await mcp.get_ticket(ticket.board_id)

# 2. CLAIM
await mcp.claim_ticket("PH-8")

# 3. PLAN
await mcp.update_ticket("PH-8", technical_depth="""
## Technical Debt
- [ ] Retry mekanizması
- [ ] Event persistence
""")
await mcp.update_ticket("PH-8", impact_analysis="""
## Etkilenen Flowlar
- MCP tool execution
## Etkilenen Dosyalar
- `app/mcp/server.py`
""")

# 4. GELİŞTİR
# ... kod yaz ...

# 5. AC
await mcp.update_ticket("PH-8", acceptance_criteria="""
## Definition of Done
- [x] Tool şeması yazıldı
- [x] Handler implement edildi
- [ ] Test coverage > 80%
""")

# 6. TEST
# pytest tests/test_mcp_...

# 7. TRANSITION
await mcp.transition_state("PH-8", "in_review")

# 9. TEST PLAN
await mcp.update_ticket("PH-8", test_plan="""
## QA Test Senaryoları
1. Tool valid input → success
2. Tool invalid input → validation error
""")

# 10. QA
await mcp.transition_state("PH-8", "in_test")

# 11. IMPACT kontrol (manuel)

# 12. DONE
await mcp.transition_state("PH-8", "done")
await mcp.release_ticket("PH-8")
```

---

## MCP Tools Katalogu (Context-Efficient Kullanım)

### Read Tools (Projection Kullan)

| Tool | Amaç | Context-Efficient Kullanım |
|------|------|---------------------------|
| `list_boards()` | Board listesi | Sadece `key`, `name`, `my_role` döner |
| `get_board(board_id)` | Board detayı | Workflow, roles, members içerir |
| `get_ticket(id, include=[...])` | Ticket okuma | **Daima `include` kullan:** `history`, `git_activity` gerekliyse ekle |
| `query_tickets(filters, fields, limit)` | Ticket listesi | `fields=["key", "title", "state"]` ile dar projection |
| `list_ticket_history(id, limit)` | History okuma | `limit=20` - son N event yeterli |
| `list_ticket_git_activity(id, limit)` | Git aktivite | `limit=5` - son commit/PR'lar |

### Mutate Tools

| Tool | Amaç | Ne Zaman Kullan |
|------|------|-----------------|
| `claim_ticket(id)` | Ticket claim | **Her ticket başında ZORUNLU** |
| `release_ticket(id)` | Ticket release | Done olduğunda VEYA blocked/başka işe geçerken |
| `update_ticket(id, **fields)` | Alan güncelleme | `technical_depth`, `impact_analysis`, `test_plan`, `acceptance_criteria` |
| `transition_state(id, to_state)` | State geçiş | `in_progress` → `in_review` → `in_test` → `done` |
| `add_comment(id, body)` | Yorum ekle | Progress, review notes, admin mention |
| `update_agent_phase(id, phase, message)` | Live badge | Her faz değişiminde, heartbeat (60sn) |

### Anti-pattern'ler

❌ **Context şişirici kullanımlar:**
```python
# HATALI - Tüm ticket'ların tüm alanlarını çek
tickets = await mcp.query_tickets(board_id="PH")  # fields yok = tüm alanlar

# HATALI - Gereksiz include
ticket = await mcp.get_ticket(id, include=["history", "comments", "git_activity", "all_relations"])

# HATALI - Limit yok
git_activity = await mcp.list_ticket_git_activity(id)  # tüm history
```

✅ **Context-efficient kullanımlar:**
```python
# DOĞRU - Dar projection
tickets = await mcp.query_tickets(
    board_id="PH",
    fields=["key", "title", "state", "assignee"],
    limit=20
)

# DOĞRU - Sadece gerekli include
ticket = await mcp.get_ticket(id, include=["history"])

# DOĞRU - Limit ile
git_activity = await mcp.list_ticket_git_activity(id, limit=5)
```

---

## Referans

- `rules.md` §3.5 — Ticket Alanları kuralları
- `skills.md` Skill 20 — Ticket alanlarını doğru doldurmak
- `docs/mcp-tools.md` — MCP tool input/output schema detayları
- `docs/project_plan.md` — Ürün ve mimari kaynak
