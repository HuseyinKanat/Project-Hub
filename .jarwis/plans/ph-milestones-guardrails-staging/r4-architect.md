# Architect — Round 4 (Parallel Layout)

## [COUNCIL R4 architect] 2026-08-03 — authoritative globs, disjointness, waves

> Grounding pass complete. Opened `docker-compose.yml`, `backend/app/api/boards.py`,
> `backend/app/db/models/core.py`, `backend/app/services/relationships.py` + `tickets.py`,
> `backend/app/mcp/server.py`, `backend/app/schemas.py`, `frontend/src/pages/BoardDetail.tsx`,
> `frontend/src/api/client.ts`, `frontend/src/types/api.ts`, the migrations dir, `scripts/`,
> and ran `docker compose version` / `docker compose ls` / `docker volume ls`. **VERIFIED** = confirmed
> in code/tooling; anything else is labelled `assumed`/`unverified`.

---

### 0. Grounding facts (all VERIFIED this round)

- **Compose is `v2.40.3`** (`docker compose version`) → **≥ v2.24 ⇒ the `!reset` / `!override` YAML merge tags ARE available.** This is load-bearing for P1a (below).
- **Live compose project = `project-hub`** (`docker compose ls`: `running(6)`). Live volumes (`docker volume ls`): `project-hub_postgres_data`, `project-hub_redis_data`, `project-hub_attachments_data`, `project-hub_sonarqube_*`. → a staging `-p projecthub_staging` deterministically namespaces to `projecthub_staging_postgres_data`, a **different Docker volume** the live project never mounts.
- **pg/redis host ports HARDCODED** — `docker-compose.yml:11` `postgres "127.0.0.1:5432:5432"`, `:21` `redis "127.0.0.1:6379:6379"`. Backend/frontend ports ARE env-driven (`:34` `${BACKEND_PORT:-8000}`, `:61` `${FRONTEND_PORT:-5173}`). Confirms R2 #1.
- **SonarQube boots on bare `up`** — `sonarqube-db` (`:87`) + `sonarqube` (`:102`) carry NO profile; only `sonar-scanner` (`:139`) is `profiles:["scan"]`-gated. Confirms R2 #2. Staging must omit them + set `SONARQUBE_ENABLED=false`.
- **Alembic HEAD = `ph330agentowner`** (`versions/20260720_0018_ph_330_agent_owner_attribution.py`: `revision="ph330agentowner"`, `down_revision="ph322userprofile"`; nothing references it as parent → single head). → **P6a migration `down_revision = "ph330agentowner"`.**
- **Board child-FK CASCADE pattern** — `ProjectPath.board_id` `ForeignKey(_FK_BOARDS_ID, ondelete="CASCADE")` (`core.py:295`), `Repository` same (`:508`); ORM side uses `_CASCADE_ALL_DELETE_ORPHAN` (`:199-216`). **No board-DELETE REST endpoint exists** (grep clean — only member-delete `boards.py:689`). → P6a `board_notes.board_id` = `ondelete="CASCADE"` for FK integrity (fires only on CLI/DB board delete, not a hot path).
- **P3 aggregation precedent** — `relationships.py:_epic_candidates` (`:471-486`): `select(Ticket).where(Ticket.deleted_at.is_(None), or_(Ticket.epic_id == src.id, ...)).where(Ticket.board_id == src.board_id)`. Exactly P3's shape. `Ticket.story_points Mapped[int|None] Integer` (`core.py:336`), `deleted_at` (`:340`), `epic_id` self-FK (`:320`) all present. `tickets.py:list_tickets` (`:113`) uses `selectinload` (`:52-54`) → one query, no lazy N+1.
- **schemas.py = single 1359-line module** (`backend/app/schemas.py`, NOT a package). Both P3 + P6a add classes here → shared, but additive.
- **MCP = single 1725-line `mcp/server.py`** — a read tool = 3 edits: `TOOLS` catalog (`:533`), `_dispatch_tool` elif (`:876`+), `_TOOL_INPUT_MODELS` registry (`:1408`; "keep dispatcher and TOOLS in sync" `:1458`). Precedent `related_tickets` (`:559`/`:939`/`:1413`). P3 has no MCP surface → P6a-only.
- **Frontend shared surfaces** — `client.ts:140` `export const api = {…}` object (add methods); `types/api.ts` add interfaces; **`BoardSettings.tsx` EXISTS** as a separate page from `BoardDetail.tsx` (`frontend/src/pages/`), so P6a's panel can live in Settings → page-disjoint from P3.

---

### 1. Authoritative `files_touched_globs`

**P1a — staging clone (chore, NO migration)**
| path | new? | why this file |
|---|---|---|
| `docker-compose.staging.yml` | NEW | override; `ports: !reset []` on postgres+redis drops the hardcoded host publish (`:11`,`:21`); omits sonarqube/-db so they don't double-boot (`:102`) |
| `.env.staging.example` | NEW | alt `BACKEND_PORT`/`FRONTEND_PORT`, distinct `POSTGRES_DB`, `SONARQUBE_ENABLED=false`, distinct `SECRET_KEY` (backend/frontend `env_file: .env` `:32`,`:57`) |
| `scripts/staging-up.sh` | NEW | schema-only `pg_dump` live → restore into staging pg → synthetic seed → `alembic upgrade head` w/ `lock_timeout`; `scripts/` is the host-script home (sonar-scan*.sh) |
| `scripts/staging-refresh.sh` | NEW | idempotent teardown+reseed |
| `docs/**` | maybe | staging runbook |
| `docker-compose.yml` | **only if** base-parametrize path chosen (not recommended given v2.40.3 `!reset`) — still disjoint from P2/P3/P6a |

Glob: `{docker-compose.staging.yml, .env.staging.example, scripts/staging-up.sh, scripts/staging-refresh.sh, docs/**}`. **ZERO `backend/app/**`, ZERO `frontend/src/**`.**

**P2 — deploy gate MVP (chore, NO migration, `blocked_by:[P1a]`)**
| path | new? | why |
|---|---|---|
| `CLAUDE.md` | edit | extend `## Post-done deployment` — exit-protocol §8 reads this project override block |
| `scripts/staging-smoke.sh` | NEW | `alembic upgrade head` (lock_timeout) + `/health` + 1 critical endpoint vs staging port |
| `docs/**` | maybe | protocol note |

Glob: `{CLAUDE.md, scripts/staging-smoke.sh, docs/**}`.

**P3 — epic-progress rollup (feature, NO migration)**
| path | new? | why |
|---|---|---|
| `backend/app/services/progress.py` | NEW | aggregation — ONE board-wide query + in-memory group-by-`epic_id` (mirror `relationships.py:479-486`) |
| `backend/app/api/boards.py` | edit | `GET /{board_id}/epics/progress` (board-scoped → boards router) |
| `backend/app/schemas.py` | edit | `EpicProgressResponse` (+ item) — additive |
| `frontend/src/pages/BoardDetail.tsx` | edit | per-epic progress bars / rollup strip |
| `frontend/src/api/client.ts` | edit | `getEpicProgress` — additive method |
| `frontend/src/types/api.ts` | edit | `EpicProgress` types — additive |
| `frontend/src/components/progress/**` | NEW | ProgressBar (own subdir) |

Glob: `{backend/app/services/progress.py, backend/app/api/boards.py, backend/app/schemas.py, frontend/src/pages/BoardDetail.tsx, frontend/src/api/client.ts, frontend/src/types/api.ts, frontend/src/components/progress/**}`.

**P6a — board notes store+UI+MCP (feature, MIGRATION Y)**
| path | new? | why |
|---|---|---|
| `backend/app/db/migrations/versions/*_ph_XXX_board_notes.py` | NEW | additive `board_notes`, `down_revision="ph330agentowner"`; plain `CREATE TABLE` (no batch_alter needed) |
| `backend/app/db/models/core.py` | edit | `BoardNote` model + `Board.notes` rel (`cascade` all,delete-orphan; FK `ondelete="CASCADE"` per `:295`/`:508`) |
| `backend/app/schemas.py` | edit | `BoardNote`, `BoardNoteCreate`, `BoardNoteListResponse` — additive |
| `backend/app/services/board_notes.py` | NEW | CRUD |
| `backend/app/api/board_notes.py` | NEW | own router `GET/POST/DELETE /api/boards/{board_id}/notes` (NEW file, NOT boards.py — keeps disjoint from P3) |
| `backend/app/main.py` | edit | 1 line `include_router(board_notes.router)` |
| `backend/app/mcp/server.py` | edit | `get_board_notes` read tool (3 spots: `:533`/`:876`/`:1408`) |
| `frontend/src/pages/BoardSettings.tsx` | edit | Notes/Guardrails panel (Settings page, NOT BoardDetail — keeps disjoint from P3) |
| `frontend/src/api/client.ts` | edit | note methods — additive |
| `frontend/src/types/api.ts` | edit | BoardNote types — additive |
| `frontend/src/components/boardNotes/**` | NEW | NotesPanel (own subdir) |

Glob: `{backend/app/db/migrations/versions/*board_notes*.py, backend/app/db/models/core.py, backend/app/schemas.py, backend/app/services/board_notes.py, backend/app/api/board_notes.py, backend/app/main.py, backend/app/mcp/server.py, frontend/src/pages/BoardSettings.tsx, frontend/src/api/client.ts, frontend/src/types/api.ts, frontend/src/components/boardNotes/**}`.

> **Routing choices that BUY disjointness** (deliberate, not incidental): P6a takes a **NEW `api/board_notes.py` router** (not boards.py) and its panel goes in **`BoardSettings.tsx`** (not BoardDetail.tsx). This removes `boards.py` and `BoardDetail.tsx` from the P3∩P6a set, leaving only the three unavoidable additive files (`schemas.py`, `client.ts`, `types/api.ts`).

---

### 2. Disjointness matrix (`parallel.md` §1.3)

| pair | glob-disjoint? | shared paths | conflict tier (`parallel.md` §6.2) | verdict |
|---|---|---|---|---|
| **P1a × P2** | NO | `scripts/**`, `docs/**` (different FILES) | Tier 1 | **MOOT** — `P2 blocked_by P1a` ⇒ serial regardless |
| **P1a × P3** | **YES** | — | — | ✅ parallel-safe — the zero-risk pair |
| **P1a × P6a** | **YES** | — | — | ✅ parallel-safe (infra vs app+migration; P1a has no migration ⇒ no migration-serialize clash) |
| **P2 × P3** | **YES** | — | — | ✅ (but P2 waits on P1a) |
| **P2 × P6a** | **YES** | — | — | ✅ (R3 strawman W2) |
| **P3 × P6a** | **NO** | `schemas.py`, `client.ts`, `types/api.ts` (all additive) | **Tier 1 mechanical** | serialize (see below) |

**P3 × P6a — the crux, honest read.** After the routing choices above, the residual overlap is exactly THREE files, all **append-only, no shared function body or logic**:
- `schemas.py` — P3 adds `EpicProgressResponse`; P6a adds `BoardNote*`. New classes, distinct regions of a 1359-line module.
- `client.ts` — P3 adds `getEpicProgress`; P6a adds note methods. New members of the `api` object literal.
- `types/api.ts` — P3 adds `EpicProgress`; P6a adds `BoardNote`. New interfaces.

Under `parallel.md` §6.2 these are **Tier 1 (mechanical): import/registry/non-overlapping additive** — Coordinator- or rebase-resolvable, NOT semantic (Tier 2). But the strict §1.3 independence test requires globs **path-disjoint**, and three shared files fail that test ⇒ **the pair is NOT independent ⇒ serialize.**

**Is "cautiously serial" costing us parallelism here? Honestly, NO.** The wave logic separates P3 and P6a into different waves for reasons INDEPENDENT of the shared files — P3 is the ideal zero-risk W1 partner for P1a; P6a is the round's sole migration item, best isolated (§3). Because they never run concurrently, the three additive files are never contended: P6a's branch simply rebases on top of P3's already-merged additions (trivial — non-overlapping appends). So we pay **no caution tax**; the file overlap merely *confirms* a separation the migration/risk logic already imposes. (Migration-serialization does NOT force this pair apart — P3 has no migration; only the shared additive files + the wave placement do.)

---

### 3. Final wave assignment (MAX_PARALLEL=2)

Unblocked at t0: **P1a, P3, P6a** (P2 `blocked_by P1a`). Migration-bearing this round: **P6a only** ⇒ R2 #6 satisfied automatically (no two migrations concurrent). Cap = 2.

**Wave 1 — `P1a ∥ P3`** (both no-migration, fully path-disjoint)
- P1a = infra/scripts/docs (backend-implementer or chore). P3 = full-stack read-only (backend + frontend).
- Rationale: the two lowest-risk items; P3 is a pure computed read (doesn't even need staging to validate) delivering the visible "epic %" win, while P1a stands up the safety platform — from t0, in parallel.

**Wave 2 — `P2 ∥ P6a`** (path-disjoint; P6a = sole migration, runs alone vs head `ph330agentowner`)
- P2 (`blocked_by P1a` now satisfied) = CLAUDE.md + `staging-smoke.sh`. P6a = table+CRUD+MCP+API+panel.
- **Intra-wave DEPLOY order is load-bearing: merge P2 FIRST (activates the staging-smoke gate), THEN deploy P6a's additive migration THROUGH the now-active gate.** Round 1 both builds the safety net (P2) and exercises it once, on its gentlest possible migration (a single additive table). Development is parallel; only the merge/deploy is ordered — and Coordinator serializes merges anyway (root-checkout mutex), so this is free.
- P3 (W1) and P6a (W2) never concurrent ⇒ the 3 additive shared files are never contended; P6a rebases on P3's merged additions.

**Total = 2 waves. R3 strawman VERIFIED correct**, with the single refinement that within W2 the P2 gate merges before P6a's migration (P6a = the gate's first customer).

**P6a — single ticket vs backend/frontend split?**
- **RECOMMEND: single full-stack P6a ticket.** The surface is thin (one table `body+author+timestamp+board_id`, one Settings panel, one MCP read tool). Keeping the `get_board_notes` MCP contract + the panel that renders it in ONE head avoids a contract handoff, and it stays 2-wave.
- **Fallback (if skill-pure tickets preferred):** split `P6a-be` (migration+model+CRUD+MCP+API) → `P6a-fe` (panel, `blocked_by P6a-be`), mirroring the **PH-331→PH-332** precedent (the two most recent merges: backend board-create, then frontend button). Cost: **+1 wave** — `P6a-fe` becomes a solo W3 tail because nothing else remains to pair it with. Only worth it if the team wants a pure-backend migration ticket reviewed in isolation.

---

### 4. Conditional layout — if the user picks **option B** (promote P4 into round 1)

P4 = milestone entity (real `milestones` table + `milestone_epics` join, cross-board, `board_id NULL`), **migration Y**, `blocked_by:[P1a]`. P4 shares `schemas.py`/`client.ts`/`types/api.ts` (additive) with P3/P6a and `mcp/server.py` (additive dispatch branch) with P6a.

**The hard constraint: P4 + P6a are BOTH migration-bearing ⇒ migration-serialization (R2 #6) forbids them in the same wave** (two Alembic migrations off one head `ph330agentowner` = multi-head merge pain). This, not file globs, drives the layout.

- **W1: `P1a ∥ P3`** (unchanged).
- **W2: `P2 ∥ P6a`** — P6a = FIRST migration; merge P2 gate first, then P6a's additive-table migration through it.
- **W3: `P4`** — SECOND migration, **MUST be solo** (migration-serialize vs P6a). Validated on staging via the now-active P2 gate. Nothing left to pair ⇒ P4 solo.
- **Total = 3 waves** (+1 vs option A, entirely attributable to the P4↔P6a migration-serialization — NOT to file overlap).

**Migration ORDER is load-bearing: P6a (W2) BEFORE P4 (W3), not swappable.** Prove the brand-new staging-smoke gate on the TRIVIAL migration (single additive `board_notes` table) first; only then run the HEAVIER cross-board migration (new table + join + cross-board FK semantics) through the proven gate. Swapping them would debut the gate on the riskiest migration — defeating the sequencing-is-safety intent.

---

### 5. Risk & first-break notes (per item)

**P1a — MED (sharpest infra + security surface)**
- **First break — the `ports` merge trap (answers user Q5).** "Drop the pg/redis host ports in an override" as written in R1/R3 is **NOT mechanically sufficient**: Docker Compose **merges `ports` by append**, so a naive override `postgres: ports: []` leaves the base `127.0.0.1:5432:5432` in place → staging still binds 5432 → collides with the running live stack on first `up`. **Fix (VERIFIED available — local Compose v2.40.3 ≥ v2.24): `ports: !reset []`** on postgres+redis in `docker-compose.staging.yml`. Alternatives: parametrize base ports (`${PG_HOST_PORT:-127.0.0.1:5432}:5432` — backward-compat but edits the live file) or a standalone (non-merged) staging compose. **The P1a ticket MUST name the mechanism.**
- **Live `postgres_data` untouched — mechanical guarantee (VERIFIED).** `docker compose ls` → live project `project-hub`; `docker volume ls` → live volume `project-hub_postgres_data`. `docker compose -p projecthub_staging` namespaces to `projecthub_staging_postgres_data` — a distinct volume the `project-hub` project never mounts. `pg_dump` READS live (over live's published 5432 or a one-off `exec` on the live container); the restore WRITES only into the staging container/volume; after the ports fix staging pg has NO host port, so nothing external can even reach it. Two independent guarantees (volume namespace + no host port).
- **Secret material (2nd — already resolved by P1a's shape).** A live-DATA dump would copy `actors.token_lookup` (sha256hex deterministic auth key — migration `ph320tokenlookup` is in the chain) / `token_hash` / webhook secrets into a 2nd running instance. P1a's **schema-only dump (`pg_dump --schema-only`, real schema, ZERO rows) + synthetic seed** copies none of it. (Live-DATA-with-sanitization is the deferred P1b.)
- **Don't double-boot sonar (3rd).** Explicit service list (`up postgres redis backend frontend`) or an override that omits sonarqube/-db; `.env.staging` `SONARQUBE_ENABLED=false`. VERIFIED both sonar services start on bare `up` (`:102`, no profile).

**P2 — LOW-MED**
- **First break — enforceability is advisory.** P2 is a CLAUDE.md protocol + a smoke script; nothing MECHANICALLY blocks a merge-to-live that skips the staging smoke. The gate is only as strong as the Coordinator honoring the `## Post-done deployment` override. This is exactly why the enforcement engine (gate-skip detect/block) is the deferred P2b; round-1 backstop = the existing security §4 human-approval gate on irreversible deploy.
- **2nd — lock_timeout must be scripted.** `staging-smoke.sh` (and every migration) MUST run `alembic upgrade head` under `PGOPTIONS="-c lock_timeout=15s"` (CLAUDE.md discipline). VERIFIED not encoded in any current script (R2 #5 grep clean) — a hung migration otherwise prints `Running upgrade …` and silently waits, looking like success.

**P3 — LOW (lowest-risk item in the council)**
- **First break — N+1 if implemented naively.** Iterating epics and issuing a child query each = O(epics) queries. AVOID via the `_epic_candidates` precedent (`relationships.py:479-486`): ONE board-wide query `WHERE board_id=:b AND deleted_at IS None`, then bucket by `epic_id` in Python. `list_tickets` already `selectinload`s related entities (`tickets.py:52-54`) → no lazy N+1.
- **`story_points` NULL handling (VERIFIED nullable, `core.py:336`).** Weighted % must fall back to count-based when an epic's children carry no points (per AC "story_points doluysa ağırlıklı, yoksa adet") — mixed/None is the common case.
- **`deleted_at` soft-delete (VERIFIED `core.py:340`; filter used at `tickets.py:60,113,481`).** Exclude soft-deleted children from BOTH numerator and denominator — replicate `WHERE deleted_at IS None` exactly.

**P6a — MED (the round's only migration; value-risk > blast-radius)**
- **Migration shape (VERIFIED).** Additive `board_notes`, `down_revision="ph330agentowner"` (single head). Plain `CREATE TABLE` — `batch_alter_table` is only needed for ALTER on the SQLite test DB (R2 #5), not a fresh create. Follows the ProjectPath additive-table template.
- **CASCADE (VERIFIED pattern).** `board_id` FK `ondelete="CASCADE"` (matches `project_paths` `:295`, `repositories` `:508`) + ORM `Board.notes` rel with `_CASCADE_ALL_DELETE_ORPHAN` (`:199-216`). **No board-DELETE REST endpoint exists** (grep clean) → CASCADE fires only on CLI/DB board delete; it's referential-integrity insurance, not a hot path.
- **MCP tool (VERIFIED seam).** `get_board_notes` = 3 edits in the 1725-line `mcp/server.py` (`TOOLS` `:533`, `_dispatch_tool` `:876`, `_TOOL_INPUT_MODELS` `:1408`), precedent `related_tickets`. P3 has no MCP surface → no contention.
- **First break — NOT technical, it's VALUE.** Shipping the PH half without the deferred Jarwis dispatch-injection (P6b) risks "a note list nobody reads at dispatch." Mitigated by the `get_board_notes` MCP read tool (agents PULL). The council already decided the net-new board-scoped queryable surface earns its place and deferred injection — honor the "no severity/tag taxonomy in round 1" trim (body+author+timestamp+board_id only).

---

### 6. Two most critical warnings for the user gate

1. **P1a's "drop the ports in an override" needs a specific mechanism or staging collides with live on first `up`.** Docker Compose appends `ports` on merge; the clean fix is `ports: !reset []` (VERIFIED available on the local v2.40.3). This is a real implementation fork the P1a ticket must pin, not a detail.
2. **W2 merge order is load-bearing: P2 (gate) before P6a (migration), and every migration/smoke runs `alembic upgrade head` with `lock_timeout`.** Otherwise P6a's migration is deployed without the gate it was meant to be the first customer of, and a hung migration masquerades as success.

(Mechanical live-safety, answering "staging'in canlı `postgres_data`'ya dokunmadığını nasıl garanti ederiz": distinct `-p projecthub_staging` ⇒ distinct `projecthub_staging_postgres_data` volume — VERIFIED against the running `project-hub` project — PLUS no staging pg host port after the ports fix. pg_dump reads live, restore writes staging-only.)
