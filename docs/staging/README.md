# Staging clone (isolated) — runbook  · PH-333

project-hub dogfoods itself on a **single live Postgres volume**
(`project-hub_postgres_data`) that manages 14 boards. A broken migration/DDL against
that volume would drop the live ticket store instantly. This staging instance stands up
an **isolated clone** so PH self-changes (especially migrations) can be validated
**off-live**.

**What P1a delivers:** an isolated instance with a **real, production-shaped schema** but
**synthetic rows** — migrations are proven off-live. It is **not** a live-data mirror
(that is the deferred P1b: live-data snapshot *with* sanitization).

---

## TL;DR

```bash
# stand it up (schema-only clone of live + synthetic seed)
./scripts/staging-up.sh

# wipe + re-seed to a clean state (idempotent)
./scripts/staging-refresh.sh

# tear it down manually (staging-scoped — live untouched)
docker compose -p projecthub_staging --env-file .env.staging \
  -f docker-compose.yml -f docker-compose.staging.yml down -v
```

Staging backend: <http://localhost:8020>  ·  frontend: <http://localhost:5183>
(`/health` is public; `/api/boards` needs `Authorization: Bearer <ADMIN_PASSWORD>`.)

---

## The one command prefix (`$STG`)

Every staging command MUST carry the four isolation levers. A **bare**
`docker compose ...` in this repo targets the **LIVE** `project-hub` project.

```bash
STG="docker compose -p projecthub_staging --env-file .env.staging \
  -f docker-compose.yml -f docker-compose.staging.yml"
```

| Lever | Why |
|---|---|
| `-p projecthub_staging` | Separate project namespace → volumes become `projecthub_staging_*` (a **different** volume the live project never mounts) and containers get their own names. |
| `--env-file .env.staging` | Drives `${VAR}` interpolation in the compose files (alt ports, staging DB name). |
| `-f docker-compose.yml -f docker-compose.staging.yml` | Layers the staging override on top of the untouched base. |

## Two independent live-safety guarantees

1. **Distinct volume namespace** — `-p projecthub_staging` → `projecthub_staging_postgres_data`,
   never the live `project-hub_postgres_data`.
2. **No pg/redis host port** — the override sets `ports: !reset []` on postgres + redis,
   clearing the base `127.0.0.1:5432 / 127.0.0.1:6379` publishes. Compose *appends* `ports`
   on merge, so a naive `ports: []` would leave the base publish and collide with live —
   `!reset` is required (Compose ≥ v2.24; local is v2.40.3).

Data flows **one way only**: `pg_dump --schema-only` **reads** live (via `exec`, zero
rows); the restore **writes** staging. Nothing ever flows staging → live.

## Secret isolation (`env_file: !override`)

The base compose pins `env_file: .env` on backend + frontend, and `--env-file` does **not**
redirect that directive. The override uses `env_file: !override [".env.staging"]` to
**replace** it, so staging containers load **only** `.env.staging` and never inherit the
live `SECRET_KEY` / `GITHUB_PAT` / `SONARQUBE_TOKEN`.

> Because `!override` *replaces* `.env`, `.env.staging` must be **complete** (every runtime
> var). Copy it from `.env.staging.example` (committed, placeholders only). The real
> `.env.staging` is gitignored — never commit it.

```bash
cp .env.staging.example .env.staging   # staging-up.sh also does this automatically
```

## What `staging-up.sh` does

1. Preflight (docker running; create `.env.staging` from the example if missing).
2. `up -d postgres redis` — data plane only (**SonarQube is excluded**: not in the service
   list, and profile-gated in the override, so even a bare `up` cannot boot its two
   embedded-Elasticsearch containers).
3. `pg_dump --schema-only` of **live** (read-only, zero rows, zero secrets).
4. Restore that schema into staging postgres.
5. `alembic stamp head` **then** `alembic upgrade head` with `PGOPTIONS="-c lock_timeout=15s"`.
   The stamp is why a schema-only restore does **not** hit `relation already exists`: the
   tables already exist but `alembic_version` is empty, so we stamp then no-op-upgrade.
   `lock_timeout` is mandatory — a lock-blocked upgrade otherwise hangs *silently* and
   looks like success.
6. `up -d backend frontend`, then `python -m app.cli bootstrap` — a **synthetic** fixture
   (workflow + admin + PH board + backlog). The admin bearer token is the synthetic
   `.env.staging` `ADMIN_PASSWORD`, never a live copy.
7. Verify: `GET /health` → 200 and `GET /api/boards` (with the admin bearer) → ≥ 1 board.

## Refresh / teardown

`staging-refresh.sh` runs `$STG down -v --remove-orphans` (removes **only**
`projecthub_staging_*` volumes; the live `project-hub_*` volumes are untouched) then
re-execs `staging-up.sh`. It is idempotent — run it twice, get the same clean state. The
script hard-refuses to run if the `-p projecthub_staging` namespace is ever removed from
the invocation.

## SonarQube note

SonarQube is intentionally excluded from staging (`SONARQUBE_ENABLED=false` +
profile-gated + never in the `up` list), so the host `vm.max_map_count` prerequisite for
its embedded Elasticsearch is **moot** here. See `docker-compose.yml` and
`docs/sonarqube-setup.md` if you ever need it elsewhere.

## Scope boundaries

- **Not P1b:** synthetic rows, not a sanitized live-data mirror.
- **Not P2 (PH-334):** the deploy *gate* (`scripts/staging-smoke.sh` + the CLAUDE.md
  `## Post-done deployment` edit that green-gates live merges) is a separate ticket that
  `blocked_by: [PH-333]`. This ticket only stands the instance up.
