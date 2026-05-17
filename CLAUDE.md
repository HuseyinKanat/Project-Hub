# project-hub — Project-level CLAUDE.md (Jarwis pilot)

> Bu dosya project-hub'ı **Jarwis pilot projesi** olarak işaretler. Jarwis tüm rol/workflow kurallarını üst dizinden import eder.

@~/Jarwis/CLAUDE.md

## Project meta

- **board_id:** `PH` — kendi board'umuz; ticket'lar burada açılır
- **mcp_prefixes (per-role):** her rol kendi MCP server'ına bağlanır
  - pm        → `mcp__project-hub-pm__*`
  - architect → `mcp__project-hub-architect__*`
  - backend   → `mcp__project-hub-backend__*`
  - frontend  → `mcp__project-hub-frontend__*`
  - reviewer  → `mcp__project-hub-reviewer__*`
  - qa        → `mcp__project-hub-qa__*`
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

## Bootstrap durumu

- [x] PH board mevcut + roles JSON güncel (8 rol: admin, pm, architect, backend_dev, frontend_dev, reviewer, qa, orchestrator).
- [x] **Per-role actor + token provision tamamlandı** (`create_jarwis_actors --board PH`):
  - jarwis-pm, jarwis-architect, jarwis-backend, jarwis-frontend, jarwis-reviewer, jarwis-qa
- [x] `.mcp.json` 6 ayrı MCP server entry içeriyor — her rol kendi token'ı ile authenticate olur.
- [x] `.claude/agents/<role>.md` whitelist'leri `mcp__project-hub-<role>__*` ile kısıtlı.
- [x] PH-28 done (manual cleanup sonrası; ilk pilot smoke).
- [ ] Per-role identity smoke test (sub-agent tarafından her oturum başında).
- [ ] Gerçek feature/bug akışı tekrar denemesi.

## Sonraki smoke testi

Coordinator'a: **"Tüm sub-agent'ları identity check için sırayla çağır, sonra küçük bir feature ile uçtan uca akıt."**

Beklenen akış:
1. Her sub-agent kendisini doğrular: pm→jarwis-pm, architect→jarwis-architect, ... Hepsi yeşilse devam.
2. PM ticket açar (PH-XX) — actor history'de `jarwis-pm` olmalı.
3. Architect tech_depth + mermaid + AC — actor `jarwis-architect`.
4. Implementer worktree açılır, **branch claude/<random> → `ph-XX-<slug>`** rename, commit'ler ticket-aligned branch'a düşer.
5. Reviewer approve / reject — actor `jarwis-reviewer`, technical_depth düzeltirse audit'te o görünür.
6. QA (web/UI ise Playwright, backend ise pytest) — actor `jarwis-qa`.
7. Done — Coordinator MCP'den state doğrular (sub-agent "done" demesi yetmez).

Her actor history'de **kendi rolü** ile görünürse + state machine eksiksizse pilot başarılı.
