# Architect (Feasibility) — Round 2

## [COUNCIL R2 architect] 2026-08-03

> Grounding pass complete. I opened `docker-compose.yml`, `.env.example`,
> `backend/app/db/models/core.py`, `backend/app/schemas.py` (grep), `services/relationships.py`,
> `services/history.py`, `services/tickets.py` (state-transition write path), a sample Alembic
> migration, and the MCP layout. Below, **Verified** = confirmed in code; assumptions are named as such.

### Infra reality check (corrections to R1 grounding — read before per-item)

1. **P1 PORT COLLISION — the PM's "compose zaten BACKEND_PORT/FRONTEND_PORT override'lı" is only HALF true.**
   - `backend: "${BACKEND_PORT:-8000}:8000"` and `frontend: "${FRONTEND_PORT:-5173}:5173"` **are** env-driven ✓.
   - **BUT** `postgres: "127.0.0.1:5432:5432"` and `redis: "127.0.0.1:6379:6379"` are **HARDCODED** (no env var).
     → `docker compose -p projecthub_staging up` will **fail to bind** host ports 5432/6379 (live stack already holds them).
   - Consequence: P1 is **NOT** "pure script + docs, app kodu değişmez." It needs a small `docker-compose.staging.yml`
     **override** that DROPS the pg/redis host-port publish (staging backend reaches them over the compose network;
     only backend+frontend need host ports, on the alternate BACKEND/FRONTEND_PORT). Override file keeps the live
     `docker-compose.yml` **untouched** (preferable to parametrizing ports, which edits the live file). Named-volume
     namespacing under `-p` **is** automatic (`projecthub_staging_postgres_data`) — that half of the PM's claim holds.

2. **P1 must NOT boot the SonarQube stack twice.** `sonarqube-db` + `sonarqube` start on a bare `up` (only
   `sonar-scanner` is `profiles:["scan"]`-gated). Two embedded-Elasticsearch instances (2–4 GB each + the
   `vm.max_map_count` kernel knob) on one laptop is a resource bomb. → staging MUST use an **explicit service list**
   (`up postgres redis backend frontend`) or an override that omits sonar. Staging `.env.staging` sets
   `SONARQUBE_ENABLED=false` so the backend never references sonar. **Staging skips SonarQube entirely** — answer to the
   brief's question: yes, staging can and must skip the sonar stack.

3. **P5 history data VERIFIED PRESENT (this lowers P5's cost vs a pessimistic read).**
   `tickets.py:398-406` writes `TicketHistory(event_type="state_changed", field="state", old_value=old_state,
   new_value=to_state)` on **every** transition, with `created_at server_default=now()` and index
   `ix_ticket_history_ticket_created (ticket_id, created_at)`. So "weeks-N done velocity" = query
   `ticket_history WHERE event_type='state_changed' AND new_value='done'` grouped by week — **retroactively computable
   over all existing history**. The PM's AC ("done'a geçiş zamanı TicketHistory'den türetilir") is correct and grounded.
   Caveat: the only index is `(ticket_id, created_at)`; a board-wide velocity scan filters on `(event_type,new_value)`
   → fine at 14-board scale, a partial index is a later optimization, not a blocker.

4. **Model facts (Verified in core.py):** `story_points Integer nullable` (336) ✓, `state String(80)` — **workflow-driven
   string, NOT a native enum** (315) ✓, soft-delete `deleted_at` (340) ✓, `epic_id` self-FK to `tickets.id` (320) ✓,
   `labels` STRING_ARRAY (321), `files_touched_globs`/`blocked_by` STRING_ARRAY (327-328). `Comment` + `TicketHistory`
   tables exist. **No milestone / velocity / progress code exists anywhere** (grep clean) — all Theme B is net-new.

5. **Migration discipline (Verified):** Alembic autogenerate, `batch_alter_table` for SQLite-test-DB parity, additive
   nullable columns with no backfill is the house style (see PH-322 migration). `lock_timeout` is a **runtime
   discipline** (CLAUDE.md: `PGOPTIONS="-c lock_timeout=15s" alembic upgrade head`) — NOT in code/scripts today; grep
   found no `lock_timeout` in `backend/app/db` or `scripts/`. Any migration-bearing item (P4/P6/P7) inherits this.

6. **⚠️ MIGRATION SERIALIZATION (load-bearing for the council's parallel layout):** P4, P6, P7 each add an Alembic
   migration. Alembic autogenerate runs off a **single linear head**; two migration-bearing tickets developed in
   parallel produce **multi-head** merge pain. → **at most ONE migration-bearing item in flight at a time**, regardless
   of file-glob disjointness. This is a stronger constraint than `files_touched_globs` and the Coordinator's
   parallel-independence test must honor it.

---

### P1 — Staging clone instance  (theme A)
- **Dependency:** blocks P2 (P2 is meaningless without it). Is the **staging platform** for every migration-bearing
  item — **P4/P6/P7's first customer**. Independent of all Theme B/C *app code* (touches infra/scripts only).
- **files_touched_globs (est):** `docker-compose.staging.yml` (NEW override), `.env.staging.example` (NEW),
  `scripts/staging-up.sh` (NEW), `scripts/staging-refresh.sh` (NEW), `docs/**`. **Zero `backend/app/**` or
  `frontend/src/**`.** → path-disjoint from every other P-item → **best first-parallel partner (e.g. P1 ∥ P3).**
- **Risk: MED** — the sharpest **security surface** in this council. `pg_dump` of live copies `actors.token_hash`
  (bcrypt), `actors.token_lookup` (**sha256hex of the real token — a deterministic auth key**, PH-320), per-board
  webhook secrets, and any GITHUB_PAT-adjacent data into a **second running instance**. Not plaintext tokens, but
  cloned credential material. **Recommendation: staging seed must SANITIZE** — post-restore SQL that nulls/rotates
  `token_lookup` + neuters agent tokens, OR seed a **schema-only** dump + a tiny synthetic fixture (enough to prove
  "≥1 board cloned"). A full live-data clone is only safe if (a) loopback-bound (127.0.0.1, already true), (b)
  same-host, (c) `.env.staging` carries a **distinct SECRET_KEY**. Also MED for the port-collision + double-sonar
  hazards above.
- **Verified:** pg/redis ports hardcoded (collision real); volume namespacing auto under `-p`; sonar boots on bare
  `up` (must be excluded); `.env.example` confirms `POSTGRES_HOST=postgres` (compose-network hostname → staging backend
  needs no pg host port). **Assumption remaining:** exact sanitization scope (which tables carry live secrets) needs a
  one-pass audit at implement time.
- **Shape note:** Cleanest = `docker-compose.staging.yml` override (drops pg/redis host ports) + explicit service list
  + `scripts/staging-up.sh` doing `pg_dump live → restore staging → sanitize → alembic upgrade head (lock_timeout)`.
  **SPLIT suggestion: P1a = staging brings-up (schema + synthetic seed, no live data)** — unblocks P2/P4 validation
  immediately with zero secret risk; **P1b = optional live-snapshot refresh WITH sanitization** — deferrable. This
  lets the [user-mandated] core (an isolated instance to validate against) land fast without waiting on the
  sanitization design.

### P2 — Self-dev deploy gate  (theme A)
- **Dependency:** `blocked_by P1` (hard). Gates every future PH self-change deploy (a process wrapper around
  `exit-protocol §8`).
- **files_touched_globs (est):** `CLAUDE.md` (project — `## Post-done deployment` override block),
  `scripts/staging-smoke.sh` (NEW), `docs/**`. **CROSS-REPO OTHER HALF:** the enforcement lives in `~/Jarwis`
  (`contracts/exit-protocol.md §8` / Coordinator deploy behavior) — a **different repo/system no PH ticket can
  change**. The PH-board half is only the guard script + the CLAUDE.md override that §8 already reads.
- **Risk: LOW-MED** — low technical surface (mostly a script + doc). Real risk is **enforceability**: it's an advisory
  Coordinator protocol; a human/agent can still merge to live directly. The migration-first-on-staging rule is only as
  strong as the Coordinator honoring it. Cross-repo coordination is the friction, not code.
- **Verified:** `exit-protocol §8` already supports per-project `## Post-done deployment` overrides + `PREV_MAIN`
  rollback snapshot (in the eager-imported contract) → the hook point exists; P2 fills it. No new backend code.
- **Shape note:** Keep P2 **thin** — a `staging-smoke.sh` (migration + `/health` + one critical endpoint against the
  staging port) + a CLAUDE.md override that says "PH self-change: smoke on staging → green → live merge." Do NOT build
  a bespoke gate engine (YAGNI). The deepest variant (MCP-endpoint isolation so PH self-dev ticket *writes* also miss
  live) is correctly a **phase-2 defer** — most blast-radius is migration/DDL, which P1+smoke already covers.

### P3 — Derived epic-progress rollup (read-only, NO migration)  (theme B)
- **Dependency:** none inbound; **P5 sits on top** (P5 needs a rollup source). The **safest ship-first** Theme-B item.
- **files_touched_globs (est):** `backend/app/services/progress.py` (NEW, or fold into `services/tickets.py`),
  `backend/app/api/boards.py` (or `api/tickets.py` — new computed endpoint), `backend/app/schemas.py`
  (`EpicProgress` response), `frontend/src/pages/BoardDetail.tsx`, `frontend/src/api/**`,
  `frontend/src/components/**`, `frontend/src/types/**`. **No migration.**
- **Risk: LOW** — the lowest-risk item in the whole council. Pure computed read over existing `epic_id` + child
  `state` + `story_points`. Zero schema change, zero live-store risk → **doesn't even need staging to validate.**
- **Verified:** `epic_id` self-FK (320), `story_points` nullable (336), `deleted_at` soft-delete (340) all present →
  the AC's "story_points doluysa ağırlıklı, yoksa adet; deleted_at hariç" is directly computable. `relationships.py`
  `_epic_candidates` (471-486) already demonstrates the exact `WHERE epic_id = :id AND deleted_at IS NULL` query
  shape P3 reuses — **so P3 has a proven aggregation precedent in-repo.**
- **Shape note:** One computed endpoint `GET /api/boards/{id}/epics/progress` returning
  `[{epic_key, done, total, weighted_pct, state_histogram}]`. Degrade rules (ungrouped tickets, 0/0 child-less epic)
  are trivial in the aggregation. **Ship P3 first, standalone.**

### P4 — Milestone entity (real table, cross-board)  (theme B)
- **Dependency:** the **RICHER alternative to P3** (not both mandatory). Alternative rollup source for P5.
  **`blocked_by P1`** in practice — its migration is exactly what staging exists to de-risk (**Theme A's first real
  customer**). Migration-serializes against P6/P7 (finding #6).
- **files_touched_globs (est):** NEW migration `backend/app/db/migrations/versions/*`,
  `backend/app/db/models/core.py`, `backend/app/schemas.py`, `backend/app/services/milestones.py` (NEW),
  `backend/app/api/milestones.py` (NEW), `backend/app/mcp/server.py` (MCP tool — monolithic single file, confirmed),
  `frontend/src/pages/**`, `frontend/src/components/**`. **Has a migration.**
- **Risk: MED** — the **highest irreversible-infra risk** among Theme B (new table + FK on the live ticket store the
  whole council exists to protect). Fully **mitigated by P1** — that is the entire point of sequencing it after
  staging. Additive/nullable (existing epics stay milestone-less) → backward-compatible, matches house style.
- **Verified — the ONE structural thing P3 cannot do that P4 can:** `epic_id` is a **same-board** grouping in
  practice — keys are per-board (`uq_ticket_board_key`), `next_ticket_number` is per-board, and every epic consumer
  (relationships.py, UI) treats epics as intra-board. Even if `epic_id` were hacked cross-board, **there is no entity
  to represent "the milestone" that belongs to neither board.** P4's `board_id NULL = cross-board` row is the only way
  to group board-A's epic + board-B's epic under one target-dated milestone. → **cross-board grouping is the
  irreducible P3-can't / P4-can difference.** Confirmed exactly as the brief hypothesized.
- **Shape note:** `milestones(id, title, target_date?, ordering, board_id NULL=cross-board)` + **prefer a
  `milestone_epics` join table over a `tickets.milestone_id` column** — a milestone groups N epics across boards, and a
  join keeps the cross-board M2M clean without widening the hot `tickets` row. **MERGE note:** P4 reuses P3's rollup
  math (progress % over an epic's children) one level up (progress over a milestone's epics) → **build P3 first, P4
  wraps it.** Recommend **sequence P3 → measure → promote to P4 only when cross-board is proven needed**, not both up
  front.

### P5 — Progress & velocity view  (theme B)
- **Dependency:** sits on a rollup source (**P3 or P4**). Independent net-new surface otherwise (time-series).
- **files_touched_globs (est):** `backend/app/services/velocity.py` (NEW — reads `ticket_history`),
  `backend/app/api/**`, `backend/app/schemas.py`, `frontend/src/pages/**` (+ a chart component),
  `frontend/src/api/**`. **No migration.**
- **Risk: MED** — **NOT a data-availability risk (data VERIFIED present, finding #3)** — the risk is aggregation
  correctness + a new charting surface. Contra a pessimistic read: the PM's cost estimate is **right**, the history
  exists, velocity is retroactively computable. Minor perf note: no `(event_type,new_value)` index → board-wide scan,
  fine at scale, partial index deferrable.
- **Verified:** `state_changed` history rows with `new_value='done'` + `created_at` exist for all past transitions
  (tickets.py:398-406). Empty/low-data degrade ("yeterli geçmiş yok") is a trivial guard.
- **Shape note:** `GET /api/velocity?board=&weeks=N&cross_board=` → weekly `{done_count, done_points}` buckets from
  `ticket_history`. Reuse P3/P4's grouping for the burn-up. **DEFER-friendly:** if P3 ships the static %, P5 is a
  nice-to-have — static % satisfies most of the user's "görmem lazım." Lowest-priority Theme-B item.

### P6 — Board-scoped notes / guardrails (store + UI + dispatch injection)  (theme C)
- **Dependency:** Theme-C foundation; **P7 richer than it, P8 feeds into it.** Migration-serializes vs P4/P7.
- **files_touched_globs (est) — PROJECT-HUB HALF:** NEW migration + `backend/app/db/models/core.py`,
  `backend/app/schemas.py`, `backend/app/services/board_notes.py` (NEW), `backend/app/api/boards.py` (or NEW
  `api/board_notes.py`), `backend/app/mcp/server.py` (**MCP read tool** — the surface agents query),
  `frontend/src/pages/BoardDetail.tsx` or `BoardSettings.tsx` + `frontend/src/components/**`.
  **⚠️ CROSS-REPO — JARWIS HALF:** the "dispatch injection" (Coordinator prepends board notes to sub-agent prompts)
  lives in `~/Jarwis` (`roles/coordinator.md` invoke template + `.claude/agents/*`) — **a different system; no PH
  ticket implements it.** **The two halves land in different repos.**
- **Risk: MED** — the project-hub half (store + CRUD + UI + MCP read) is **easy and low-risk** (mirror the
  `ConceptTag`/`ProjectPath` table+service+MCP precedent). The **value** is the injection half, which is **outside this
  board's control** (Jarwis ruleset). Risk: shipping only the PH half = a note list nobody reads at dispatch → the
  PM's own "redundant with CLAUDE.md" failure mode.
- **Verified:** MCP server is a single `backend/app/mcp/server.py` (a read tool is a small addition, precedent:
  `related_tickets`). `BoardNote` table is a straight mirror of the additive-table pattern (ProjectPath migration is
  the template). No existing board-note surface (grep clean) → genuinely net-new, not a duplicate.
- **Shape note:** `board_notes(id, board_id FK CASCADE, body, severity?, created_by, created_at)` + CRUD + one MCP
  `get_board_notes(board)` read tool + a BoardDetail panel. **SPLIT is mandatory and already correct in R1:**
  **P6a = project-hub store+UI+MCP-read (this repo, shippable, migration-serialized)**; **P6b = Jarwis dispatch
  injection (cross-repo, Coordinator behavior).** P6a is the Theme-C SMALLEST-USEFUL-INCREMENT; P6b immediately after.

### P7 — Guardrail rules with triggers (conditional auto-warning)  (theme C)
- **Dependency:** RICHER P6 (statik not → koşullu kural). Migration-serializes vs P4/P6. Its warnings feed the same
  dispatch/UI surface as P6.
- **files_touched_globs (est) — PROJECT-HUB HALF:** NEW migration + `core.py`, `schemas.py`,
  `backend/app/services/guardrails.py` (NEW — trigger-matching engine), `backend/app/api/**`,
  `backend/app/mcp/server.py`, `frontend/src/components/**` (ticket-UI warning banner).
  **⚠️ CROSS-REPO — JARWIS HALF:** same as P6 — the dispatch-time injection of a fired rule into the matched ticket's
  agent context is Coordinator/`~/Jarwis` behavior.
- **Risk: MED-HIGH** — highest of Theme C's *buildable* items: a migration **plus** a deterministic, explainable
  trigger-matching engine (`files_touched_globs` / label / type / title-keyword). Cross-repo injection compounds it.
- **Verified:** matching can reuse in-repo glob machinery — `Ticket.files_touched_globs` (327) is the same field the
  Jarwis parallel-independence test uses, and `relationships.py` already does label/type predicate matching → the
  engine has precedents, not greenfield. Determinism ("which rule fired, why") is a design requirement, not a code
  unknown.
- **Shape note:** `guardrail_rules(board_id, trigger_type, trigger_value, message, severity)` + a pure
  `match_rules(ticket) -> [FiredRule]` function (explainable). **DEFER-friendly:** if P6 ships, P7 is phase-2 — but P7
  is where "önleyici" actually bites (a passive note may go unread). Recommend **P6 → P7** sequence, not parallel
  (migration-serialize + P7 semantically extends P6's store).

### P8 — Auto-harvested guardrails (from repeating failure signals)  (theme C)
- **Dependency:** **P8 is inert without a sink** — its suggestions need P6 (note) or P7 (rule) to land in. **Strictly
  last / most-deferrable.** No migration (read-only analytics) → does NOT migration-serialize.
- **files_touched_globs (est):** `backend/app/services/guardrail_harvest.py` (NEW — read-only analytics over
  `ticket_history` labels + `comments`), `backend/app/api/**`, `frontend/src/**` (a suggestions/report surface).
  **No migration.** Mostly this-repo (the harvest is data analytics); human-approval turns a suggestion into a P6/P7
  row.
- **Risk: HIGH (uncertainty, not blast-radius)** — read-only so it **can't break the live store**, but it's the most
  **speculative** item: the user explicitly said "fikirlerim olgun değil." Product risk = will clustered failure-label
  frequency + reviewer-reject comment mining produce **signal or noise**? Success has no crisp metric. This is the
  **highest-risk item overall** on the ROI/uncertainty axis.
- **Verified:** the raw signal exists — failure labels (`needs_revision`/`qa_failed`/`arch_rejected`/`deploy_failed`
  are all in `relationships.py:GENERIC_LABELS`, i.e. real live labels) + `comments` table + `ticket_history`. The
  aggregation style is exactly `relationships.py`'s frequency/IDF pattern (a proven in-repo shape). So the *mechanism*
  is feasible; the *usefulness* is the unknown.
- **Shape note:** Keep it **read-only + human-in-the-loop** (R1 got this right — it SUGGESTS, never auto-writes a
  guardrail). Frequency threshold to kill one-off noise. **Strong defer:** ship P6 (and ideally P7) first, let real
  board-notes accumulate, THEN evaluate whether harvesting adds signal. Building P8 before P6/P7 exist = suggestions
  with nowhere to go.

---

### Feasibility summary (Architect verdict)

- **Ship-order by risk/dependency:** **P1a (staging, synthetic-seed) ∥ P3 (derived rollup)** is the ideal
  zero-risk first parallel pair (path-disjoint, no shared migration). Then P2 (gate, needs P1) and P5 (velocity, needs
  P3) can follow. **P4/P6/P7 each carry a migration → serialize them one-at-a-time**, and **P4 must be validated on P1
  staging first** (Theme A's first customer). Theme C: **P6a (PH half) → P6b (Jarwis inject) → P7 → P8**, deferring the
  speculative tail.
- **Theme B recommendation:** P3 first (derived, zero-risk), promote to **P4 only when cross-board is proven needed**
  (the sole capability P3 structurally lacks). P5 is a defer-able nice-to-have on top.
- **Theme C recommendation:** every C-item is **two-repo** (project-hub store/UI/MCP + Jarwis dispatch injection).
  MVP = **P6a**; the injection half (P6b) is where value lives but it's outside PH-board control. P7 phase-2, P8 last.
- **Two R1 grounding corrections:** (1) P1 pg/redis host ports are **hardcoded** → P1 needs a compose override + must
  exclude the sonar stack (not "app kodu değişmez"); (2) P5's history data **provably exists** → P5 cost is as PM
  implied, not higher.
