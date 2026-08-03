# Planning Council — project-hub self-extension

**Slug:** `ph-milestones-guardrails-staging`
**Board:** PH (project-hub itself)
**Opened:** 2026-08-03
**Output contract:** proposals & recommendations for the user only. NO tickets opened during council. Ideas (esp. Theme C) are admittedly immature — council's job is to mature them and surface concrete options.

---

## CONTEXT (Coordinator seed)

### User request (verbatim intent, TR)
1. **Theme A — Safe self-dev (hard constraint):** "ben geliştirme yaparken canlıdakinde bozulma olmasın — geliştirmeleri yaparken clone bir server kurup gerçekleştir." project-hub is live and in daily use managing 14 boards. Self-development must be validated on a clone/staging instance before touching the live one. Classic dogfooding hazard: the tool builds itself.
2. **Theme B — Milestones & progress:** "epicler ilerleyen işler oluyor ama bunları high-level milestones ve progress olarak göremiyoruz, bunları görmem lazım." Wants a high-level roll-up: milestones grouping epics/tickets, % progress, velocity, per-board and cross-board.
3. **Theme C — Per-project recurring-mistake / warning notes:** "proje bazında tekrarlanan hatalar ya da uyarıları önleyici proje notları gibi kısımlar hayal ediyorum ama fikirlerim olgun değil." Per-board accumulated lessons/gotchas/guardrails that surface to agents & humans during ticket flow to prevent repeating mistakes.

### Live-state facts (audited this session)
- Live backend healthy (`/health` = ok). 14 boards: BENCH, FB, FN, GXA, GXG, GXI, GXL, HB, IQB, KIM, LA, PH, PLANT, PRDEV.
- Active work is on OTHER projects (GameX/Unity, Lipsync/Android, Flappy) — PH board itself is quiet → safe window to plan PH changes.

### Codebase map (grounding — do not re-discover from scratch, but DO verify before proposing)
- **Backend:** FastAPI + SQLAlchemy 2 + Alembic + Postgres 16 + Redis 7.
  - Ticket model: `backend/app/db/models/core.py` — **`epic_id` is a self-FK on Ticket (`core.py:320`)**. Epics are ordinary tickets other tickets point to. There is **NO milestone entity** anywhere.
  - Schemas: `backend/app/schemas.py`. Services: `services/tickets.py`, `services/workflows.py`, `services/boards.py`, `services/relationships.py` (already computes epic adjacency, related_tickets scoring). API: `api/tickets.py`, `api/boards.py`. MCP tools: `backend/app/mcp/tools/` + `mcp/server.py`.
  - Migrations: `backend/app/db/migrations/versions/`. Migration discipline: run with `lock_timeout` (see CLAUDE.md). Live prod = Postgres volume `postgres_data`.
- **Frontend:** React 18 + Vite + Tailwind + shadcn/ui + Zustand + TanStack Query. `frontend/src/{pages,components,stores,api,types,hooks,lib}`. Existing feature components: git, diff, sonar/sonarqube, search, space, repository, attachments.
- **Infra:** single `docker-compose.yml` — services postgres(127.0.0.1:5432), redis(6379), backend, frontend, + full SonarQube stack (sonarqube-db, sonarqube, sonar-scanner). Named volumes: postgres_data, redis_data, attachments_data, sonarqube_*. `.env` drives POSTGRES_USER/PASSWORD/DB.

### Existing machinery Theme C MUST reconcile with (do not duplicate)
- **File-based agent memory** (`~/.claude/.../memory/*.md` + MEMORY.md index) — cross-session facts, feedback, gotchas. Already captures things like "run migrations with lock_timeout", "worktree docker test mount", etc.
- **Codewiki** (`<project>/docs/codewiki/`) — per-project synthesis pages (WHAT), `.codemap` file→page mapping, gated ingest. Triangle: ticket history (WHY) ↔ codewiki (WHAT) ↔ git (WHEN/HOW).
- **CLAUDE.md project notes** — per-project persistent rules (e.g. this repo's "run migrations with lock_timeout" block).
- **Ticket labels + comments + technical_depth/impact_analysis fields** — structured per-ticket knowledge.
- Theme C's open question: is the new thing a NEW surface, or a *board-scoped, queryable, surfaced-at-dispatch* view over knowledge that today lives in scattered files? YAGNI applies hard.

### Council rules reminder
- ⛔ No `create_ticket` during council. Append-only to this file. Deep design thinking over speed.
- YAGNI gatekeeper (Reviewer) has lazy default — proposer bears burden of proof.
- `[user-mandated]` items cannot be cut (all three themes are user-mandated at the theme level; sub-items within a theme are fair game for cut/defer/merge/split).
- Max 2 challenge↔rebuttal loops; unresolved → user decides at gate.

---

## [COUNCIL R1 pm] 2026-08-03

> PM proposer note: 8 candidate items, global-numbered P1..P8, grouped A(P1-P2) / B(P3-P5) / C(P6-P8). Each theme's SMALLEST-USEFUL-INCREMENT is flagged; richer/entity variants are split as separate P-items so the YAGNI gatekeeper can defer cleanly. All three themes are user-mandated at theme level; individual P-items are fair game to cut/merge/defer EXCEPT where marked `[user-mandated]` (the irreducible core of a theme).

### P1 — Staging clone instance (isolated compose project + live-DB snapshot seed)  (theme A)
- Değer: project-hub bugün kendi kendini geliştiriyor (dogfooding) ve tek canlı Postgres volume'ü (`postgres_data`) üstünde koşuyor; bozuk bir migration/DDL, 14 board'u yöneten canlı ticket store'u anında düşürür. İzole bir staging instance, PH self-change'lerini canlıya dokunmadan doğrulama penceresi açar. Ölçülebilir hedef: canlı DB'ye dokunan doğrulanmamış self-dev deploy sayısı → 0.
- Kaba AC:
  - `docker compose -p projecthub_staging` + `.env.staging` (farklı BACKEND_PORT/FRONTEND_PORT, farklı POSTGRES_DB; project-name ile volume'ler otomatik namespaced) ile canlıdan bağımsız ayağa kalkar; canlı 8000/5173 portlarına ve `postgres_data` volume'üne DOKUNMAZ.
  - `scripts/staging-up.sh`: canlı Postgres'ten `pg_dump` (canlıyı yalnız OKUR) → staging Postgres'e restore → `alembic upgrade head` (lock_timeout ile).
  - Staging `/health` staging portunda 200 + en az 1 board klonlanmış görünür (klon doğrulandı).
  - Idempotent refresh/teardown: staging'i sıfırlayıp taze snapshot ile yeniden seed eden komut.
- Kaynak: "geliştirmeleri yaparken clone bir server kurup gerçekleştir" | [user-mandated]
- Notes: Theme A'nın SMALLEST-USEFUL-INCREMENT'i. Saf infra + script + docs (app kodu değişmez). P2 (deploy gate) bunun üstüne oturur (blocked_by P1). Grounding: compose zaten `.env`-driven + BACKEND_PORT/FRONTEND_PORT override'lı + named-volume, yani `-p` izolasyonu mekanik olarak temiz.

### P2 — Self-dev deploy gate: PH değişiklikleri önce staging'de doğrulanır  (theme A)
- Değer: Staging instance (P1) tek başına yeterli değil — "canlıya ne zaman/nasıl geçilir" kuralı olmazsa dogfooding hazard sürer. Bu item, PH board'undaki self-change'ler için post-done deploy'un ÖNCE staging'de smoke edilmesini, ancak yeşilse canlı merge+restart'a geçmesini şart koşar (deploy-to-live gating).
- Kaba AC:
  - PH board self-dev protokolü: QA pass → staging'e deploy + smoke (migration + `/health` + kritik endpoint) → yeşilse canlı `main` merge + `docker compose restart` (proje CLAUDE.md `## Post-done deployment` override + opsiyonel guard script).
  - Migration içeren PH ticket'ları önce staging'de `lock_timeout` ile koşulur; canlıya yalnız staging'de sorunsuz uygulandıysa geçer.
  - Gate atlanıp doğrudan canlıya gidilirse görünür uyarı/engel (rollback hedefi PREV_MAIN korunur).
- Kaynak: "ben geliştirme yaparken canlıdakinde bozulma olmasın" | [user-mandated]
- Notes: P1'e bağımlı (blocked_by P1). RICHER varyant (ayrı P-item değil, bilinçli defer notu — gatekeeper isterse P2b'ye böler): self-dev Jarwis pipeline'ının MCP endpoint'ini staging instance'a yöneltmek → PH self-dev sırasında ticket YAZIMLARI da canlı store'a düşmez (en derin izolasyon; `.mcp.json`/token işi). YAGNI: çoğu risk migration/DDL'de, o yüzden staging-app + gate MVP'yi karşılar; MCP-izolasyonu faz-2.

### P3 — Türetilmiş epic-progress rollup (read-only, migration YOK)  (theme B)
- Değer: Epic'ler zaten `epic_id` self-FK ile child ticket gruplar, ama hiçbir yerde "% ne kadar bitti" görünmüyor; kullanıcı high-level ilerlemeyi göremiyor. Mevcut veriden (child state'leri) TÜRETİLEN bir progress rollup, sıfır şema riskiyle "milestones & progress" ihtiyacının çoğunu karşılar.
- Kaba AC:
  - Read-only computed endpoint: her epic için child'ların done oranı (story_points doluysa ağırlıklı, yoksa adet bazlı) + state dağılımı (backlog/in_progress/in_review/in_test/done).
  - BoardDetail'de her epic satırında progress bar + "N/M done"; board genelinde epic rollup listesi.
  - Edge: epic'i olmayan ticket'lar "ungrouped"; `deleted_at` set child sayılmaz; child'sız epic 0/0 → anlamlı degrade.
- Kaynak: "epicler ilerleyen işler oluyor ama bunları high-level milestones ve progress olarak göremiyoruz" | [user-mandated]
- Notes: Theme B SMALLEST-USEFUL-INCREMENT. P4 (gerçek Milestone entity) ile KARŞILAŞTIRMALI alternatif: P3 migration'sız + board-içi, P4 cross-board + yeni tablo. P5 (velocity) P3'ün üstüne oturur. Öneri: P3'ü ölç, entity ihtiyacı kanıtlanınca P4'e yüksel (P3 çoğu ihtiyacı görebilir).

### P4 — Milestone entity (epic'lerin üstünde gerçek tablo, cross-board)  (theme B)
- Değer: `epic_id` aynı-board self-FK olduğu için bir epic board'lar arası YAYILAMAZ; kullanıcı açıkça "cross-board" milestone istedi. Gerçek bir Milestone tablosu (N epic'i gruplar, target_date + ordering) hem cross-board rollup hem de zaman-hedefli milestone'ları mümkün kılar.
- Kaba AC:
  - Yeni `milestones` tablosu (id, title, target_date?, ordering, board_id NULL=cross-board) + epic↔milestone ilişkisi (epic ticket'a `milestone_id` ya da join tablosu).
  - CRUD (create/list/update/close) + MCP tool + UI (milestone listesi → altındaki epic'ler → toplam % + target_date).
  - Migration `lock_timeout` ile; mevcut epic'ler milestone'suz geçerli kalır (nullable) — geriye dönük kırılma yok.
- Kaynak: "bunları high-level milestones ve progress olarak göremiyoruz ... per-board ve cross-board" | [user-mandated theme; bu ENTITY varyantı fair-game]
- Notes: P3'ün RICHER alternatifi (aynı ihtiyacın entity'li hali) — ikisi birden ZORUNLU DEĞİL; gatekeeper P3 vs P4 seçmeli ya da P3→P4 fazlamalı. Cross-board grouping P3'ün veremediği tek gerçek fark (epic tek board'a bağlı). Migration riski Theme A ile doğal bağlı: P4 ilk STAGING'de (P1) denenmeli — Theme A'nın ilk gerçek müşterisi.

### P5 — Progress & velocity view (per-board + cross-board)  (theme B)
- Değer: Kullanıcı sadece statik % değil "progress ve velocity" (gidişat hızı) istedi. Zaman-serili bir view (haftalık done adedi/story_points, burn-up) gerçek ilerleme hızını ve kabaca tahmini gösterir.
- Kaba AC:
  - Velocity: son N hafta done olan ticket adedi + story_points (done'a geçiş zamanı `TicketHistory`'den türetilir).
  - Burn-up/rollup: epic (P3) veya milestone (P4) bazında zamanla tamamlanan iş; per-board ve cross-board toggle.
  - Az/boş veri degrade: velocity=0 / "yeterli geçmiş yok" — beyaz ekran/patlamaz.
- Kaynak: "progress olarak göremiyoruz ... görmem lazım" (velocity/high-level gidişat) | [user-mandated theme]
- Notes: Rollup kaynağına bağımlı (P3 veya P4 üstüne oturur). Ayrı item çünkü time-series aggregation + yeni UI yüzeyi, P3'ün statik %'sinden ayrı iş. YAGNI: P3 varsa P5 defer edilebilir (statik % çoğu "görme" ihtiyacını karşılar); velocity nice-to-have.

### P6 — Board-scoped project notes / guardrails (thin store + UI + dispatch injection)  (theme C)
- Değer: Bugün "tekrarlanan hata" bilgisi üç dağınık yerde yaşıyor: CLAUDE.md prose (repo'da, PH UI'da GÖRÜNMEZ + queryable değil), agent memory (makine-local + board-scoped DEĞİL), ticket comment'leri (dağınık). Board'a bağlı, DB'de tutulan, UI'da görünen VE dispatch'te agent context'ine enjekte edilen ince bir not listesi bu bilgiyi tek, board-scoped, surfaced yüzeye toplar.
- Kaba AC:
  - `BoardNote` (id, board_id, body, severity/tag?, created_by, created_at) — board başına 0..N not; CRUD + MCP read tool.
  - Board UI'da "Notes / Guardrails" paneli (insan okur + ekler).
  - Dispatch injection: Coordinator sub-agent invoke ederken o board'ın notlarını prompt'a prepend eder (agent tekrarlanan hatayı iş sırasında görür).
- Kaynak: "proje bazında tekrarlanan hatalar ya da uyarıları önleyici proje notları gibi kısımlar" | [user-mandated theme]
- Notes: Theme C SMALLEST-USEFUL-INCREMENT. NET-NEW = board-scoped + queryable + UI + dispatch-injection yüzeyi. NOT-duplicate: CLAUDE.md (repo prose) ve memory (makine-local) bu üç özelliğe birden sahip değil; codewiki WHAT'ı anlatır, "guardrail" değil. Risk: sadece CLAUDE.md'yi kopyalarsa redundant → değer dispatch-injection + UI surfacing'de. İki yarı ayrılabilir: project-hub yarısı (store+UI+MCP read) vs Jarwis yarısı (dispatch inject) — MVP project-hub yarısı, injection hemen ardından.

### P7 — Guardrail rules with triggers (koşullu — eşleşen ticket'ta otomatik uyarı)  (theme C)
- Değer: Pasif not (P6) okunmayabilir; asıl ÖNLEME, kurala bir trigger bağlayıp EŞLEŞEN ticket'ta otomatik uyarı göstermekle olur. Örn: `alembic/**` dosyalarına dokunan her ticket → "migration'ı lock_timeout ile koş" uyarısı ticket UI'da + o agent'ın context'inde belirir. Kullanıcının "önleyici" kelimesinin tam karşılığı budur.
- Kaba AC:
  - `GuardrailRule` (board_id, trigger: files_touched_globs / label / ticket type / title-keyword eşleşmesi, message, severity).
  - Bir ticket kurala eşleştiğinde: uyarı ticket UI'da görünür + dispatch'te ilgili agent'a enjekte.
  - Kurallar board-scoped + insan tarafından yazılır/düzenlenir; eşleşme motoru deterministik + açıklanabilir (hangi kural neden fire etti).
- Kaynak: "önleyici ... uyarılar" | [user-mandated theme]
- Notes: P6'nın RICHER hali (statik not → koşullu kural motoru). NET-NEW: bugün hiçbir şey board-authored + data-driven + koşullu uyarı fire etmiyor (en yakın analog Reviewer codewiki sync gate ama o HARD-CODED, board-authored değil). Trigger eşleşmesi `relationships.py`/`labels.py` pattern'lerini yeniden kullanabilir. P6 varsa P7 defer edilebilir (faz-2); ama "önleyici"nin asıl motoru bu.

### P8 — Auto-harvested guardrails (tekrarlayan failure sinyallerinden türetme)  (theme C)
- Değer: P6/P7 insan yazımına dayanır; oysa "tekrarlanan hata" sinyali zaten VERİDE: tekrar eden `needs_revision` / `qa_failed` / `arch_rejected` / `deploy_failed` label'ları + reviewer-reject comment'leri. Bunları board bazında kümeleyip "şu hata tekrarlıyor" önerisi çıkarmak, guardrail'leri elle yazmadan geçmişten öğrenir.
- Kaba AC:
  - Analitik pass: board başına failure-label frekansı + reviewer-reject comment kümeleri → tekrar eden mod önerileri (read-only rapor).
  - Öneriler İNSANA sunulur; onaylanan bir guardrail P6 notuna / P7 kuralına dönüşür (otomatik guardrail YAZMAZ, ÖNERİR — insan-in-the-loop).
  - Gürültü bastırma: tek seferlik failure guardrail olmaz (frekans eşiği).
- Kaynak: "tekrarlanan hatalar" (otomatik tespit boyutu) | [user-mandated theme; kullanıcı fikrin olgun-olmadığını söyledi — bu en spekülatif varyant]
- Notes: Theme C'nin en yüksek-değer / en yüksek-belirsizlik varyantı. NET-NEW: ticket history üzerinde failure-mode analytics (`relationships.py` aggregation tarzı, mevcut label+comment verisini okur). agent memory'den farkı: memory insan/agent'ın yazmayı SEÇTİĞİ notlar; P8 failure label'larından OTOMATİK türetir. Bağımlılık: P8 tek başına anlamsız — çıktısını koyacak bir yüzey (P6) ya da motor (P7) olmadan öneri gidecek yer bulamaz → P8 en sona/defer. Gatekeeper için muhtemel sonuç: C'de P6 = MVP, P7/P8 = faz-2.

---

## [COUNCIL R2 merged] 2026-08-03 — full detail in `r2-architect.md` + `r2-reviewer.md`

### R2 architect (Feasibility) — key findings (grounded in opened code)
- **Corrected R1 infra claim:** postgres/redis host ports are **HARDCODED** (`127.0.0.1:5432/6379`); only BACKEND_PORT/FRONTEND_PORT are env-driven. A `-p projecthub_staging up` collides on host ports AND double-boots the SonarQube+Elasticsearch stack. → P1 needs a `docker-compose.staging.yml` **override** (drops pg/redis host ports, explicit service list, `SONARQUBE_ENABLED=false`). Live `docker-compose.yml` stays untouched. NOT "app kodu değişmez."
- **P1 security surface (sharpest in council):** `pg_dump` of live copies `actors.token_lookup` (sha256hex = deterministic auth key, PH-320), token_hash, webhook secrets into a 2nd running instance. → **SPLIT P1a = staging bring-up with schema + synthetic seed (zero secret risk, unblocks everything); P1b = optional live-snapshot refresh WITH sanitization (deferrable).**
- **P5 data VERIFIED present:** `tickets.py:398-406` writes `TicketHistory(event_type='state_changed', new_value=...)` on every transition, indexed `(ticket_id, created_at)`. Velocity is retroactively computable → P5 cost is as PM implied, not higher.
- **P3-vs-P4 crux CONFIRMED:** `epic_id` is same-board (per-board keys/numbering); there is **no entity to hold a milestone belonging to neither board**. Cross-board grouping is the irreducible thing only P4 can do.
- **⚠️ MIGRATION SERIALIZATION:** P4, P6a, P7 each add an Alembic migration off a single linear head → **at most ONE migration-bearing item in flight at a time** — a stronger parallel-gate than files_touched_globs disjointness.
- **Theme C is TWO-REPO per item:** project-hub half (store/UI/API/MCP-read, this repo) + Jarwis half (Coordinator dispatch-injection, `~/Jarwis`, a different system no PH ticket can change). P6 must split P6a (PH) / P6b (Jarwis inject).
- **Ship-order:** `P1a ∥ P3` = zero-risk first pair (path-disjoint, no shared migration). Then P2 (needs P1), P5 (needs P3). P4/P6a/P7 serialize; **P4 must be validated on P1 staging first** (Theme A's first customer). highest-risk = P8 (ROI/uncertainty); P1 = sharpest security risk (mitigated by synthetic seed).

### R2 reviewer (YAGNI gatekeeper) — verdicts
- **P1 keep** (irreducible Theme-A core, user's literal words). **P2 merge** MVP into P1 (extend existing CLAUDE.md `## Post-done deployment` override + a staging-smoke step) + **defer** the enforcement engine (guard-script/gate-block/MCP-isolation). **P3 keep** (thinnest: % + state distribution only). **P4 defer** (biggest+riskiest; Theme A's first customer; do after staging). **P5 defer** (gold-plating; static % already meets "görmem lazım"). **P6 split → P6a keep** (store+UI+MCP-read, net-new board-scoped queryable surface) / **P6b defer** (dispatch auto-inject — overlaps CLAUDE.md reach; MCP pull already covers targeted need). **P7 defer** (rule engine = richer P6, premature before passive notes prove worth). **P8 defer bordering cut** (unvalidated hypothesis; inert without P6/P7 sink).
- **Round-1 recommended set: {P1(a), P2-MVP, P3, P6a}.** Deferred with triggers: P2-enforcement, P4, P5, P6b, P7, P8 (the entire richer half of every theme).
- **YAGNI flags for the user:** (1) **cross-board is NOT delivered in round 1** — deliberate, P3 can't, P4 waits for staging; do not let round 1 imply it's done. (2) P3: no weighting/slicing knobs in round 1. (3) P6a: **cut the severity/tag taxonomy** — a note is body+author+timestamp until 5 notes exist. (4) P6a earns its place only on the net-new UI+query+board-scope surface, not "render CLAUDE.md in UI." (5) Sequencing P1→P4 is a safety feature, not just priority.

### CONTESTED → user decision (not a council dispute): cross-board milestones
Both voices agree P3 (per-board, derived, zero-risk) ships now and P4 (entity, cross-board, migration) is deferred to *after staging exists*. The mandate said "per-board VE cross-board," so round-1 with P3-only consciously leaves cross-board unmet. **If the user needs cross-board NOW, P4 is unavoidable — but it means a live-schema migration before its own safety net (staging) is up, which contradicts Theme A.** The YAGNI-safe path: P3 now → staging (P1/P2) → P4 as staging's first customer once a real ≥2-board milestone is named.

---

## [COUNCIL R3 pm] 2026-08-03 — Rebuttal & Round-1 consensus

> PM rebuttal posture: R2 was highly convergent (architect feasibility + reviewer YAGNI agreed on nearly every item — defers with triggers, no outright cuts). I ACCEPT the great majority, deliberately. I OVERRIDE on exactly one packaging question (P2 merge-vs-separate) with justification, and I hand the one genuinely CONTESTED item (P4 cross-board) to the user as a sharpened either/or grounded in new evidence. I also accept both R2 corrections to my R1: pg/redis host ports are HARDCODED (so P1a needs a compose override — my "app kodu değişmez" was half-wrong) and P5's history data provably exists. The architect's infra correction is load-bearing; taken.

### Per-item rebuttal verdicts

**P1 — accept the P1a/P1b split (MODIFY: sharpen P1a's seed to schema-only + synthetic).**
Accept. The split is both a YAGNI win and a security win, and it repairs my R1 error. My R1 "compose zaten port-override'lı / app kodu değişmez" was HALF-WRONG: `postgres 127.0.0.1:5432` + `redis 6379` are hardcoded, and a bare `-p projecthub_staging up` also double-boots the SonarQube+Elasticsearch stack. So P1a legitimately needs a `docker-compose.staging.yml` override (drop pg/redis host-port publish, explicit service list / omit sonar, `SONARQUBE_ENABLED=false`); the live `docker-compose.yml` stays untouched.
MODIFY on the seed: P1a should seed **schema-only `pg_dump --schema-only` of live (real production-shaped schema, ZERO rows, ZERO secrets) + a tiny synthetic fixture** (≥1 board + a couple epics/tickets). Rationale: this threads the needle better than "pure synthetic schema" — a schema-only dump gives migration validation the REAL schema/constraints (schema is identical across all 14 boards and carries no per-board secret data), while copying NONE of `actors.token_lookup` (sha256hex deterministic auth key, PH-320) / `token_hash` / webhook secrets that a live-DATA dump would replicate into a second running instance. So P1a = full migration-validation fidelity at zero credential risk. P1b (live-DATA snapshot WITH sanitization) stays deferred — that is where the sanitization-scope audit and the "real clone" flavor live.
Honesty note for the gate: the user's word was "clone." P1a delivers the SAFETY intent of clone (an isolated instance where migrations are proven before touching live) with a real schema but synthetic rows; it does NOT deliver a real-data mirror. If the user specifically wants live data in staging, that is P1b (deferred; needs sanitization). I recommend P1a-now / P1b-on-demand and surface this so "clone" is not silently reinterpreted.

**P2 — MODIFY: thin SEPARATE item, `blocked_by: [P1a]` (I side with the architect over the reviewer's merge; justified in its own block below).**

**P3 — accept the taxonomy trim.**
Accept. `%` + state distribution only; no weighting KNOBS, no per-assignee/label slicing, no configurable buckets in round 1. One clarification (not a re-litigation — both voices already agree): the single automatic rule "story_points-weighted if present, else count-based" STAYS — it is not a knob, it is the natural single behavior; the reviewer's own thin version says "adet/story-point %," and the architect confirmed it is directly computable with an in-repo precedent (`relationships.py._epic_candidates`). So P3 = one computed endpoint, one %-rule, one state histogram, per-board. No dispute.

**P4 — accept-DEFER as PM recommendation; DEFEND the observation (a concrete candidate DOES exist) → hand to the USER as a sharpened either/or.** Full treatment in the CONTESTED block below.

**P5 — accept-defer. Trigger confirmed + refined.**
Reviewer's trigger stands: "user shifts from 'what %' to 'how FAST / when will it finish' — a voiced forecasting need." Refinement: architect finding #3 VERIFIED that `TicketHistory(state_changed, new_value='done', created_at)` rows exist for all past transitions → velocity is retroactively computable → P5 is a CHEAP fast-follow the moment the forecasting need is voiced (no data-backfill wait). I add only "data already present → low revival friction."

**P6 — accept the split (P6a keep / P6b defer) + accept cutting the severity/tag taxonomy.**
Accept both. P6a = project-hub half (`board_notes` store + BoardDetail panel + one MCP `get_board_notes` read tool) — survives the duplication test on a genuinely net-new surface (board-scoped IN THE DB + visible in the PH web UI + MCP-queryable; CLAUDE.md is a repo file invisible in UI & not queryable, agent memory is machine-local & not board-scoped, codewiki is WHAT-not-guardrail). P6b = Jarwis dispatch auto-injection (cross-repo, `~/Jarwis` Coordinator behavior — not a project-hub ticket at all). Cut the severity/tag taxonomy: a `BoardNote` is **body + author + timestamp + board_id** for round 1; my own R1 "severity/tag?" was correctly doubtful, and adding a severity enum + tag system before 5 notes exist is speculative structure. Drift guard (reviewer flag #4): P6a earns its place ONLY as a first-class human-editable board-scoped queryable store — NOT "render CLAUDE.md in the UI."

**P7 — accept-defer. Trigger confirmed.**
Reviewer's three-condition trigger stands: "P6a in active use AND user names a specific auto-fire rule (e.g. 'warn on any `alembic/**` ticket') AND passive notes are observably being missed." P7 is the natural P6a→P7 promotion (passive note → conditional rule engine) and migration-serializes vs P6a/P4 → even when triggered it runs one-migration-at-a-time. Confirm as-is.

**P8 — accept-defer (bordering cut). Ordered trigger confirmed + one sharpening.**
Reviewer's ordered trigger stands: "(1) P6a in use a meaningful window; (2) a human manually notices a recurring failure mode and wishes it auto-surfaced; (3) a one-off analytic SPIKE validates that real label data actually clusters into meaningful modes — if step 3 fails, CUT." Sharpening: step 3 is itself a cheap READ-ONLY analytic over existing `ticket_history` labels + `comments` (architect confirmed the `relationships.py` frequency/IDF shape is a proven in-repo precedent) → the trigger is SELF-TESTING and costs almost nothing to evaluate BEFORE committing to build P8's full surface. The mechanism is feasible; the usefulness is the unproven bet. Defer, and honor the explicit cut-clause.

### P2 — the one packaging override: thin SEPARATE, not merged (justification)

The reviewer's merge is defensible (P2-MVP is nearly free; it reuses the existing CLAUDE.md `## Post-done deployment` override; one Theme-A unit). I still choose **thin SEPARATE item, `blocked_by: [P1a]`**, for six compounding reasons:

1. **Separation of concerns.** P1a is PURE infra (compose override + up/refresh scripts + docs; zero app behavior). P2 is a PROCESS artifact (a deploy-protocol edit + a smoke script that ENCODES the "validate-on-staging-first" discipline). Two different test surfaces, two different reviewer concerns; merging muddies P1a's crisp "does staging come up healthy?" AC with "is the deploy protocol correct?"
2. **P1a purity buys the parallel win.** The architect's whole first-wave case is "P1a ∥ P3 = zero-risk, path-disjoint." Folding P2 (which edits CLAUDE.md deploy BEHAVIOR) into P1a erodes exactly that purity and delays the moment P1a unblocks the staging platform.
3. **The discipline IS the deliverable.** Theme A's HARD constraint ("canlıda bozulma olmasın") is satisfied by the PROTOCOL (P2), not by the mere existence of a server (P1a). P1a is necessary-but-not-sufficient; the sufficiency-delivering artifact deserves its own review focus, not burial inside infra.
4. **Clean ACs / clean QA.** P1a AC = "staging up, /health 200, ≥1 board visible, idempotent teardown." P2 AC = "smoke script runs migration(lock_timeout) + /health + 1 critical endpoint against the staging port AND green-gates live merge." Separate ACs → separate QA → a smoke-gate failure bounces only P2, not the staging infra.
5. **Honest dependency edge.** P2's smoke script literally targets the instance P1a stands up; `blocked_by: [P1a]` is the truthful sequencing (serial within Theme A, which they would be anyway).
6. **Independent revival for P2b.** When the deferred enforcement engine (P2b) is triggered, extending a standalone P2 with its own history beats re-opening a merged P1+P2 monolith.

I HONOR the reviewer's real points INSIDE this shape: P2-MVP stays maximally thin (EXTEND the existing override block; no bespoke gate engine), and I defer EXACTLY the gold-plated half they flagged (gate-skip detection/block + "görünür engel" + MCP-endpoint isolation) as **P2b** with their trigger. The override is only on packaging (separate vs merged), and it is the correct call because it keeps P1a pure and gives the user's actual hard constraint its own first-class artifact.

### CONTESTED — P4 cross-board: PM recommendation = DEFER, framed as the USER's decision (new evidence, not hand-waving)

I was asked to either DEFEND P4 into round 1 with a concretely-named ≥2-board milestone, or accept-defer AND sharpen the either/or. Honest result of that search:

**New evidence (named candidate, epistemic status flagged):** Of the 14 boards, FOUR share a `GX` prefix — **GXA, GXG, GXI, GXL** — and the seed says today's active work includes "GameX/Unity." This STRONGLY suggests a GameX board family (a platform/subsystem split of one product). IF so, a single **"GameX vX launch"** milestone rolling up GXA+GXG+GXI+GXL is a concrete, named ≥2-board (indeed ≥4-board) milestone — EXACTLY the capability only P4 delivers (P3's `epic_id` is same-board, so no per-board rollup can span them). This is a real, checkable candidate, not a hypothetical.
**BUT** I have NOT verified those four are one product/roadmap, nor that the user wants a cross-GameX milestone NOW. Asserting it as fact would be the hand-waving the council forbids. So its honest status is: a PLAUSIBLE, CHECKABLE candidate — the specific thing that would trip P4's revive-trigger — not a confirmed user-voiced need.

**Why I still recommend DEFER (not defend into the first wave):**
- The immediate, concretely-STATED pain is per-board epic progress ("epicler ilerliyor ama göremiyoruz") — P3 fully answers it, cheaply, zero migration.
- Even if cross-board is wanted, P4 is a live-DB migration + new table, and running it BEFORE staging (P1a) exists DIRECTLY CONTRADICTS Theme A's HARD constraint. So P4 can NEVER be in the first wave; at best it is a round-1-TAIL / round-2-HEAD item, `blocked_by: [P1a]`, as staging's first real migration customer.

**The either/or handed to the user at the gate (three options, honestly ranked):**
- **(A) DEFAULT — defer P4 [PM rec].** Round-1 = {P1a, P2, P3, P6a}. Cross-board consciously NOT delivered in round 1; P3 gives per-board progress now. Honest and Theme-A-safe.
- **(B) Promote P4, but sequenced.** If the user confirms a real cross-board milestone (candidate: the GameX GX* family), P4 joins round 1 — but STILL `blocked_by: [P1a]`, validated on staging first. Delivers cross-board in round-1's tail WITHOUT violating Theme A.
- **(C) P4 immediately, before staging — recommended by NO ONE.** The self-contradictory path (a live migration before its own safety net). Surfaced only to be explicitly rejected.

Concrete question that decides it: **"Are GXA/GXG/GXI/GXL one GameX product you'd want under a single 'GameX vX' milestone? If yes → option B. If they are independent products → option A (P3 per-board is enough)."**

### FINAL ROUND-1 CONSENSUS (candidate tickets)

`P1a — Staging clone instance (compose override + schema-only+synthetic seed) | chore | blocked_by: [] | migration? N | repo: project-hub | docker-compose.staging.yml override (drop pg/redis host ports, omit sonar, SONARQUBE_ENABLED=false) + .env.staging.example + scripts/staging-up.sh (schema-only pg_dump → restore → synthetic seed → alembic upgrade head w/ lock_timeout) + scripts/staging-refresh.sh (idempotent) + docs; alt ports; live compose untouched; zero secret material.`

`P2 — PH self-dev deploy gate (MVP) | chore | blocked_by: [P1a] | migration? N | repo: project-hub (enforcement half lives in ~/Jarwis, out of scope) | EXTEND CLAUDE.md ## Post-done deployment override for PH board (QA pass → staging-smoke → only-if-green live merge/restart) + scripts/staging-smoke.sh (alembic upgrade head w/ lock_timeout + /health + 1 critical endpoint vs staging port).`

`P3 — Derived epic-progress rollup (read-only) | feature | blocked_by: [] | migration? N | repo: project-hub | GET /api/boards/{id}/epics/progress → per-epic {done, total, weighted_pct (story_points-weighted else count), state_histogram} + BoardDetail progress bars + "N/M done"; deleted_at excluded, ungrouped + 0/0 child-less epic degrade; per-board only; no knobs.`

`P6a — Board-scoped notes store + UI + MCP read | feature | blocked_by: [] (dev-independent; migration deploy smoke-validated via staging once P1a/P2 land — Theme-A discipline) | migration? Y (additive board_notes table) | repo: project-hub | board_notes(id, board_id FK CASCADE, body, created_by, created_at) — NO severity/tag — + CRUD + MCP get_board_notes(board) read tool + BoardDetail "Notes / Guardrails" panel; net-new queryable store, NOT a CLAUDE.md mirror.`

Round-1 migration count = **exactly ONE (P6a)** → no migration-serialization conflict this round (architect finding #6 satisfied automatically). P6a's additive-table migration is also the gentlest possible first exercise of the P2 staging-smoke gate — round 1 both builds the safety net AND uses it once, on its lowest-risk migration.

### Deferred (with trigger)

- `P1b — Live-DATA snapshot refresh WITH sanitization | repo: project-hub | trigger: user wants real-data (not synthetic) staging AND sanitization scope audited (which tables carry token_lookup/token_hash/webhook secrets) → null/rotate on restore.`
- `P2b — Deploy-gate enforcement engine (gate-skip detect/block + MCP-endpoint isolation) | repo: project-hub + jarwis | trigger: after ≥2-3 self-dev cycles the staging-first protocol is actually skipped/nearly-skipped (demonstrated discipline failure, not hypothetical).`
- `P4 — Milestone entity (real table, cross-board) | repo: project-hub | migration Y | ⚑ CONTESTED, user decides at gate | trigger: a concrete ≥2-board milestone NAMED (candidate: GameX vX over GXA/GXG/GXI/GXL) OR P3 proven insufficient in daily use — AND P1a staging live so migration validated off-live first (blocked_by P1a; NEVER before staging).`
- `P5 — Progress & velocity view (burn-up/time-series) | repo: project-hub | migration N | trigger: user shifts from "what %" to "how fast / when done" (voiced forecasting need); data already present → fast follow.`
- `P6b — Jarwis dispatch auto-injection of board notes | repo: jarwis (Coordinator invoke template — NOT a project-hub ticket) | trigger: P6a notes curated+valued AND agents demonstrably fail to PULL them via MCP when relevant (pull proven insufficient).`
- `P7 — Guardrail rules with triggers (conditional auto-warning engine) | repo: project-hub (+jarwis inject half) | migration Y | trigger: P6a in active use AND user names a specific auto-fire rule (e.g. warn on any alembic/** ticket) AND passive notes observably missed.`
- `P8 — Auto-harvested guardrails (failure-signal mining) | repo: project-hub | migration N | trigger (ordered): (1) P6a in use a meaningful window; (2) human feels manual-recurrence pain + wishes auto-surface; (3) cheap read-only validation SPIKE proves failure labels cluster into meaningful modes — IF step 3 fails, CUT.`

### Suggested parallel layout strawman (for architect R4 — NOT fully settled)

- **Wave 1:** `P1a ∥ P3` — architect-confirmed zero-risk pair (P1a = infra-only, path-disjoint from everything; P3 = no migration).
- **Wave 2:** `P2 ∥ P6a` — P2 (blocked_by P1a; touches CLAUDE.md/scripts/docs) is path-disjoint from P6a (backend table/CRUD/MCP + frontend); P6a is the ONLY migration-bearing item → runs alone against the single Alembic head (no multi-head risk).
- **Open item for R4 (real work — layout NOT fully settled):** P3 and P6a likely SHARE `backend/app/schemas.py`, `backend/app/api/boards.py`, and `frontend/src/pages/BoardDetail.tsx` → they are probably NOT path-disjoint and must serialize against each other (hence P3 in Wave 1, P6a in Wave 2). Architect owns the authoritative `files_touched_globs` disjointness confirmation + final wave assignment.

---

## [COUNCIL R4 architect] 2026-08-03 — Parallel layout (full detail in `r4-architect.md`)

> Grounding pass VERIFIED against real code + live tooling (`docker compose version|ls`, `docker volume ls`, opened compose/boards.py/core.py/relationships.py/mcp-server/schemas.py/BoardDetail.tsx/client.ts/types + migrations dir). Every claim below is file:line- or tool-grounded; see `r4-architect.md` for the evidence tables.

### Final wave plan (MAX_PARALLEL=2) — **2 waves, R3 strawman CONFIRMED**
- **Wave 1: `P1a ∥ P3`** — both no-migration, **fully path-disjoint** (P1a = infra/scripts/docs, zero `backend/app`+`frontend/src`; P3 = app-code, zero infra). The zero-risk pair.
- **Wave 2: `P2 ∥ P6a`** — path-disjoint; P6a is the round's **only** migration → runs alone vs the single Alembic head `ph330agentowner` (R2 #6 satisfied). **Load-bearing intra-wave DEPLOY order: merge P2 (gate) FIRST, then deploy P6a's additive migration THROUGH the now-active staging-smoke gate** — round 1 builds the safety net AND exercises it once on its gentlest migration. (Dev is parallel; only the merge is ordered, which Coordinator serializes anyway.)
- **P6a packaging:** RECOMMEND **single full-stack ticket** (thin surface, keeps MCP-read contract + panel in one head, stays 2-wave). Fallback = split `P6a-be → P6a-fe` (mirrors PH-331→PH-332) at the cost of **+1 wave** (fe solo tail).

### P3 × P6a — is it disjoint? **NO, but serialization is FREE.**
After routing P6a to a **NEW `api/board_notes.py` router** (not boards.py) and its panel to **`BoardSettings.tsx`** (not BoardDetail.tsx — VERIFIED that page exists), the residual overlap is exactly THREE **additive, append-only** files: `schemas.py` (1359-line monolith — new classes), `client.ts` (`api` object — new methods), `types/api.ts` (new interfaces). Per `parallel.md` §6.2 these are **Tier-1 mechanical** (no shared logic/function body); strict §1.3 flags them non-disjoint → serialize. **But the wave logic already puts P3 (W1) and P6a (W2) in different waves for migration/risk reasons independent of the files**, so they're never concurrent and the 3 files are never contended (P6a rebases on P3's merged appends — trivial). **No caution tax paid.** (Migration-serialization does NOT force this pair apart — P3 has no migration; only the shared files + wave placement do.)

### If user picks **option B** (promote P4 into round 1)
P4 (milestone entity, cross-board, **migration Y**, `blocked_by:[P1a]`) + P6a are **two migration items → migration-serialization forbids sharing a wave**. Layout becomes **3 waves**: W1 `P1a∥P3`, W2 `P2∥P6a`, **W3 `P4` solo** (2nd migration, alone; validated on staging via the now-active gate). **Migration order load-bearing: P6a (trivial additive table) before P4 (table + join + cross-board FK)** — prove the new gate on the simple migration first. The +1 wave is entirely the P4↔P6a migration serialize, NOT file overlap.

### Two most critical gate warnings
1. **P1a "drop pg/redis host ports in an override" is NOT mechanically sufficient as written** — Compose *appends* `ports` on merge, so a naive override leaves the base `127.0.0.1:5432/6379` and staging collides with live on first `up`. Concrete fix (**VERIFIED available — local Compose v2.40.3 ≥ v2.24**): `ports: !reset []`. The ticket MUST name the mechanism (or parametrize base ports / standalone compose).
2. **W2 merge order (P2 gate → P6a migration) + `lock_timeout` on every `alembic upgrade head`** (CLAUDE.md discipline; VERIFIED not yet scripted anywhere — a hung migration otherwise looks like success).

**Live-safety guarantee (answers Theme-A Q):** `docker compose -p projecthub_staging` namespaces volumes to `projecthub_staging_postgres_data` — VERIFIED distinct from the running `project-hub` project's `project-hub_postgres_data` (`docker volume ls`); staging never mounts the live volume, and (after the ports fix) staging pg has no host port. `pg_dump` reads live, restore writes staging-only. Schema-only + synthetic seed ⇒ zero `token_lookup`/secret copy.

**Migration facts for P6a:** head = `ph330agentowner` (`down_revision`); `board_notes.board_id ondelete="CASCADE"` matches `project_paths`(core.py:295)/`repositories`(:508); **no board-DELETE REST exists** (grep clean) so CASCADE is integrity-insurance, not a hot path. P3 aggregation precedent = `relationships.py:_epic_candidates` (:471-486, `deleted_at IS None` + `epic_id` + board-scope).

---

## [DECISION GATE] 2026-08-03 — user ruled (Coordinator record)

User was presented the consolidated council output (R1–R4) and decided on all four open questions. **The user chose the council's recommended position on every one** — no force, no override.

| Question | User decision | Effect |
|---|---|---|
| **P4 cross-board milestone** (the one CONTESTED item) | **Option A — DEFER** | Cross-board consciously NOT delivered in round 1. P3 per-board progress ships now. P4 stays deferred with its trigger (a concrete ≥2-board milestone named, e.g. the GX* family, AND P1a staging live). Layout stays **2 waves**. |
| **Theme A (staging + deploy gate)** — user had not restated it this turn | **KEEP in round 1 (P1a + P2)** | Theme A's `[user-mandated]` status reconfirmed explicitly. Round-1 = 4 items. P6a's migration becomes the staging-smoke gate's first customer, as R4 planned. |
| **Theme C aggressiveness** | **P6a passive notes only** | P6b (dispatch auto-inject) and P7 (trigger rule engine) stay deferred with their triggers. Learn from the passive surface first — matches the user's own "fikirlerim olgun değil". |
| **Ticketize** | **APPROVED — open tickets, start pipeline** | Council closes. TICKETIZE proceeds for exactly {P1a, P2, P3, P6a}. |

**APPROVED ROUND-1 SET (binding — PM may not add scope):** `P1a`, `P2`, `P3`, `P6a`
**Wave plan:** W1 `P1a ∥ P3` · W2 `P2 ∥ P6a` (intra-W2 merge order: P2 gate FIRST, then P6a migration through the gate)
**Deferred, unchanged (with triggers):** P1b, P2b, P4 ⚑, P5, P6b, P7, P8

Two R4 warnings carried into the tickets as hard AC requirements:
1. `ports: !reset []` (or equivalent) in `docker-compose.staging.yml` — a naive override leaves the base `127.0.0.1:5432/6379` published and staging collides with live.
2. Every `alembic upgrade head` runs with `lock_timeout` — currently scripted nowhere; a hung migration otherwise reads as success.

Council phase ENDS here. From this point the normal Jarwis pipeline applies (`exit-protocol.md` §2 transition map).
