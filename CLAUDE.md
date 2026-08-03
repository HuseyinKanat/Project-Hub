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
- **Migration ÇALIŞTIRMA — her zaman `lock_timeout` ile** (PH-331):
  ```bash
  docker compose exec -e PGOPTIONS="-c lock_timeout=15s" backend alembic upgrade head
  ```
  Sebep: `alembic upgrade head` bir kilidi bekleyerek asılırsa **hiçbir hata basmaz** — sadece
  `Running upgrade ...` satırını yazar ve sessizce bekler; bu ekranda **başarı gibi görünür**.
  `lock_timeout` ile hızlıca `canceling statement due to lock timeout` alırsın. Asılırsa bloklayanı
  bul: `select pid, pg_blocking_pids(pid), state, left(query,80) from pg_stat_activity where datname='projecthub';`
  Uygulama tarafındaki kök neden (WS long-lived session) PH-331'de kapatıldı ve
  `idle_in_transaction_session_timeout=60s` (backend/app/db/session.py) artık sunucu tarafı emniyet
  supabı; yine de DDL öncesi bu flag alışkanlık olmalı.
- **Permission/role değişikliği** yaparken her zaman `docker compose exec backend python -m app.cli update_board_roles` ile mevcut board'lar refresh edilmeli (bootstrap idempotent değil).

## Post-done deployment (override)

> Per `~/Jarwis/contracts/exit-protocol.md` §8 + §8.PR. Coordinator önce bu bloğa bakar; merge-to-main sonrası `post_merge_commands` listesini repo root'tan, host'ta çalıştırır. Komutlar best-effort — başarısızlık deploy'u bloklamaz.

```yaml
post_merge_commands:
  - scripts/sonar-scan.sh   # PH-194: best-effort main-branch SonarQube scan; ALWAYS exits 0 (self-guards on SONARQUBE_ENABLED + reachability — no-op when sonar is off, the default)

# Board-bazlı merge stratejisi (bu repo iki board sunar: PH direkt-dev, PRDEV pr-gate-dev).
# Coordinator ticket'ın board'una göre §8 (direct) veya §8.PR (pr-reviewer gate) uygular.
merge_strategy_by_board:
  PH: direct              # varsayılan — QA pass → doğrudan merge (mevcut davranış)
  PRDEV: pr               # done → PR/diff aç → bağımsız pr-reviewer (fable-5) gate → merge (§8.PR)
sonar_gate: on            # pr-modda: pr-reviewer Faz-5 değişen-dosya SonarQube taraması (sonar_pr_issues); sonar erişilemezse pr-blocked
pr_provider: github       # gh CLI yoksa diff-modu (pr-reviewer diff main...HEAD; comment/attachment relay + ticket kind=review)

# PH-334: PH board self-dev staging gate. QA pass → staging-smoke → only-if-green live.
# ADVISORY (round 1): script yalnız green/red (exit code) verir; security §4 irreversible-deploy
# insan-onayı enforcement backstop'tur. Mekanik gate-skip block = deferred (P2b).
staging_smoke_gate:
  boards: [PH]                               # yalnız PH self-dev; diğer board'lar etkilenmez
  script: scripts/staging-smoke.sh
  run_when: migration_or_backend_change      # docs/CLAUDE.md-only + frontend-static deploy'da atla
  insertion: after_local_merge_before_push   # exit-protocol §8 (a) `git merge --no-ff` SONRASI, push ÖNCESİ
  green_exit: 0                              # non-zero ⇒ reset --hard PREV_MAIN, live deploy YOK
```
> **PRDEV** = pr-reviewer'ın canlı dev board'u. Bir ticket PRDEV'de done olunca Coordinator merge yerine
> §8.PR akışını uygular: `pr-reviewer` bağımsız verdict (✅/⚠️/❌/🛑) + sonar + YAGNI → ✅/⚠️ sonrası merge.
> Diğer board'lar (PH dahil) `direct` — hiç etkilenmez. Board-anahtarı `merge_strategy_by_board`'da yoksa `direct`.

> **PH self-dev staging gate (PH-334)** — PH board'unda migration veya backend kodu değişikliği içeren bir deploy'da:
> QA pass → `done` → **`scripts/staging-smoke.sh`** (exit-protocol §8 (a) LOCAL `git merge --no-ff` SONRASI, irreversible
> `git push` + live `alembic upgrade head` + `docker compose restart backend` ÖNCESİ) → **yalnız exit 0 (GREEN)** ise
> live'a devam. Non-zero (RED) → Coordinator `git reset --hard PREV_MAIN` (un-merge; hiçbir şey push edilmedi, LIVE
> dokunulmadı) + ticket `in_progress`'e bounce + rapor. Gate izole staging'i (`-p projecthub_staging`, PH-333) **FORWARD
> alembic path**'iyle (empty DB → `alembic upgrade head`, `lock_timeout`) kaldırır → pending migration GERÇEKTEN koşar,
> stamp-ahead false-green'i önler (staging-up.sh'in `stamp head`'i schema-restore içindir; gate onu kullanmaz). **Advisory**
> (round 1): mekanik skip-block deferred (P2b), backstop = security §4 insan-onayı. Sadece migration/backend deploy'unda
> çalışır — docs/CLAUDE.md-only veya frontend-static deploy'da atlanır (`run_when`).

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
