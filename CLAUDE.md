# project-hub — Project-level CLAUDE.md (Jarwis pilot)

> Bu dosya project-hub'ı **Jarwis pilot projesi** olarak işaretler. Jarwis tüm rol/workflow kurallarını üst dizinden import eder.

@~/Jarwis/CLAUDE.md

## Project meta

- **board_id:** `PH` — kendi board'umuz; ticket'lar burada açılır
- **mcp_prefix:** `mcp__project-hub__` — `.mcp.json`'da tanımlı server adı
- **repo:**
  - default_branch: `main`
  - remote: `origin`
- **stack:**
  - backend: FastAPI 0.110+ + SQLAlchemy 2 + Alembic + PostgreSQL 16 + Redis 7
  - frontend: React 18 + Vite + Tailwind + shadcn/ui + Zustand + TanStack Query
  - tests: pytest (backend), Playwright (e2e), Vitest (frontend unit — yoksa Playwright)
  - lint/typecheck: ruff + mypy --strict (backend), tsc (frontend)
- **commit_format:** `type(<PH-ID>): subject` (type ∈ feat|fix|test|docs|refactor|chore)

## Conventions (project-hub'a özel)

- **Tüm geliştirme Docker üzerinden** ([rules.md](rules.md) §1):
  ```
  docker compose exec backend pytest -x          # tests
  docker compose exec backend ruff check .       # lint
  docker compose exec backend mypy --strict app  # typecheck
  docker compose exec frontend npm run typecheck # ts
  ```
- **Branch açma:** sadece `create_branch_for_ticket` MCP tool ile (Implementer rolü).
- **Migration:** `docker compose exec backend alembic revision --autogenerate -m "PH-XX <desc>"`
- **Permission/role değişikliği** yaparken her zaman `docker compose exec backend python -m app.cli update_board_roles` ile mevcut board'lar refresh edilmeli (bootstrap idempotent değil).

## Project-specific notes (kalıcı, sub-agentların hatırlaması gereken)

- **Hibrit kural seti:** project-hub'ın kendi `rules.md` ve `skills.md` dosyaları, Jarwis ruleset'inden bağımsız olarak da hâlâ geçerli kabul edilir; çelişki olursa Jarwis (workflow + ticket disiplini) baz alınır, project-hub rules (code style + bash patterns) tamamlayıcı.
- **Permission grammar:** `docs/permissions.md` her permission değişikliğinde güncellenmeli.
- **Test DB:** `test_ticket_lifecycle.py` ve `test_mcp_subscribe_events.py` şu anda env eksikliği nedeniyle hatalı (aiosqlite, test_client fixture). Bu ayrı bir cleanup ticket'ı — bug flow ile düzeltilebilir.
- **Heartbeat:** uzun süren implement'larda `update_agent_phase` her 60s çağrılmalı (stale claim cron 5dk timeout — PH-20).

## Bootstrap durumu (pilot için)

- [x] PH board mevcut + roles JSON güncel (`reviewer` dahil 8 rol).
- [x] `jarwis-pilot` actor + admin membership açıldı.
- [x] `.mcp.json` `project-hub` MCP server'ını bearer token ile tanımlıyor.
- [x] `.claude/agents/` sub-agent tanımları Jarwis'ten kopyalandı.
- [ ] İlk smoke test: küçük bir ticket'ı uctan uca akıt.

## İlk smoke test önerisi

Coordinator'a: **"PH'de küçük bir docs ticket'ı açalım: README'nin Quick Start kısmına `update_board_roles` komutunun nasıl çalıştırılacağını ekleyelim."**

Beklenen akış:
1. PM ticket açar (PH-XX), Architect'e devreder.
2. Architect technical_depth doldurur (trivial; "README'ye satır ekle"), approve.
3. Backend (veya frontend; bu README/docs ticket'ı için backend uygun) claim+branch+commit, in_review.
4. Reviewer approve, in_test.
5. QA: docs için smoke (Markdown lint varsa onu koş; yoksa görsel doğrulama).
6. Done.

Eğer 6 adımın hepsi yeşil dönerse pilot başarılı; gerçek feature/bug akışlarına geçilir.
