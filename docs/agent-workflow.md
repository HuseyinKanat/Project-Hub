# Agent Workflow — Ticket Lifecycle Guide

> Bu doküman, ProjectHub üzerinde bir ticket'ı **claim'den done'a** kadar çalışırken izlenmesi gereken adım adım süreci tanımlar.
> Her agent bu workflow'a uymak zorundadır.

---

## Özet: 12 Adımlık Süreç

```
1. OKU       → Ticket'ı ve history'sini oku
2. CLAIM     → Ticket'ı claim et
3. PLAN      → technical_depth ve impact_analysis doldur
4. GELİŞTİR  → Kod yaz, testleri yaz
5. AC        → acceptance_criteria güncelle
6. TEST      → Tüm testleri çalıştır
7. TRANSITION→ in_progress → in_review
8. REVIEW    → Code review (self veya peer)
9. TEST PLAN → test_plan doldur
10. QA       → in_review → in_test
11. IMPACT   → impact_analysis kontrol et (done için)
12. DONE     → in_test → done
```

---

## Detaylı Adımlar

### Adım 1: OKU — Ticket'ı Anla

**Zorunlu:**
- [ ] `get_ticket(id, include=["history", "git_activity"])` ile ticket'ı oku
- [ ] Board workflow'unu `get_board(board_id)` ile kontrol et
- [ ] Önceki agent'ların yaptıklarını history'den gör
- [ ] Git activity varsa branch ve PR durumunu kontrol et
- [ ] `skills.md` Skill 20'yi oku (ticket alanları nasıl doldurulur)

**Çıktı:** Ticket'ın ne istediğini, neyin yapıldığını, neyin kaldığını bil.

---

### Adım 2: CLAIM — Ticket'ı Kilitle

**Zorunlu:**
- [ ] `claim_ticket(id)` çağrısı yap
- [ ] Claim başarılıysa `update_agent_phase(id, "planning", "Analyzing ticket scope")`
- [ ] Claim başarısızsa (AlreadyClaimed): admin'e yorum at, başka ticket'a geç

**Kural:** Claim alınmadan kod yazılmaz.

---

### Adım 3: PLAN — Technical Debt ve Impact Analizi

**Zorunlu:**
- [ ] `technical_depth` alanını doldur:
  ```markdown
  ## Technical Debt / Ertelemeler
  - [ ] Kısa vadeli ödün: ...
  - [ ] FIXME: ...
  ```
- [ ] `impact_analysis` alanını doldur:
  ```markdown
  ## Etkilenen Flowlar
  - ...
  
  ## Etkilenen Dosyalar
  - `app/.../file.py` — yeni/değişen
  
  ## Dikkat Edilecekler
  - ...
  ```

**Not:** Bu alanlar `to_do` → `in_progress` transition'ı için gerekli.

---

### Adım 4: GELİŞTİR — Kod Yaz

**Zorunlu:**
- [ ] Docker-first: tüm komutlar `docker compose exec backend ...` içinde
- [ ] Migration gerekliyse önce yaz: `alembic revision --autogenerate`
- [ ] Model → Service → API/MCP sırasında geliştir
- [ ] Her service metodunda: permission check + history write + event publish
- [ ] Test yaz: happy path, permission denied, invalid input
- [ ] `update_agent_phase()` ile düzenli güncelle: planning → coding → reviewing

**Heartbeat:** Her 60 saniyede `update_agent_phase()` gönder.

---

### Adım 5: AC — Acceptance Criteria Güncelle

**Zorunlu:**
- [ ] `acceptance_criteria` alanını güncelle:
  ```markdown
  ## Definition of Done
  - [x] Migration yazıldı
  - [x] Model güncellendi
  - [x] Service metodu yazıldı
  - [x] MCP tool eklendi
  - [ ] Test coverage > 80%
  - [ ] Integration test pass
  ```

**Kural:** Tamamlanan maddeler `[x]`, kalanlar `[ ]`.

---

### Adım 6: TEST — Tüm Testleri Çalıştır

**Zorunlu:**
```bash
docker compose exec backend pytest tests/ -v --tb=short
```

- [ ] Yeni yazılan testler pass
- [ ] Eski testler regression olmadan pass
- [ ] Lint check: `ruff check app/`
- [ ] Type check: `mypy app/`

**Hata varsa:** Düzelt ve tekrar test et.

---

### Adım 7: TRANSITION — in_progress → in_review

**Zorunlu:**
- [ ] `transition_state(id, "in_review")` çağrısı
- [ ] Gate kontrol: `technical_depth` dolu olmalı

**Başarısızsa:** Eksik alanları doldur, tekrar dene.

---

### Adım 8: REVIEW — Kod İncelemesi

**Zorunlu:**
- [ ] Self-review: kendi kodunu oku
- [ ] Peer review gerekliyse: yorum at @admin mention ile
- [ ] `skills.md` ilgili skill'i kontrol et (uygulandı mı?)
- [ ] `rules.md` §0.1 Project Plan uyumluluğu kontrol et

---

### Adım 9: TEST PLAN — QA Senaryoları

**Zorunlu:**
- [ ] `test_plan` alanını doldur:
  ```markdown
  ## QA Test Senaryoları
  1. Happy path: ...
  2. Permission denied: ...
  3. Invalid input: ...
  4. Edge case: ...
  ```

**Not:** Bu alan `in_review` → `in_test` transition'ı için gerekli.

---

### Adım 10: QA — in_review → in_test

**Zorunlu:**
- [ ] `transition_state(id, "in_test")` çağrısı
- [ ] Gate kontrol: `test_plan` dolu olmalı

**Sonra:** QA test senaryolarını uygula (manuel veya otomatik).

---

### Adım 11: IMPACT — Etki Analizi Kontrol

**Zorunlu:**
- [ ] `impact_analysis` alanını kontrol et:
  - Etkilenen flow'lar test edildi mi?
  - Etkilenen dosyalar review edildi mi?
  - Dikkat edilecekler kontrol edildi mi?
- [ ] `acceptance_criteria` tüm maddeler `[x]` olmalı

**Not:** Bu alan `in_test` → `done` transition'ı için gerekli.

---

### Adım 12: DONE — in_test → done

**Zorunlu:**
- [ ] Son test run: `pytest tests/ -v`
- [ ] Tüm testler pass
- [ ] `transition_state(id, "done")` çağrısı
- [ ] Başarılıysa: `release_ticket(id)`

**Başarısızsa (gate hatası):**
1. Hata mesajında hangi alan eksiz gösterilir
2. Eksik alanı doldur (test_plan veya impact_analysis)
3. Tekrar dene

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

## Referans

- `rules.md` §3.5 — Ticket Alanları kuralları
- `skills.md` Skill 20 — Ticket alanlarını doğru doldurmak
- `docs/project_plan.md` — Ürün ve mimari kaynak
