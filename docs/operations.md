# ProjectHub — Operations Guide

This guide covers how to mount a git repository, connect it to a ProjectHub board,
install the post-commit refresh hook, and manage the shared secret.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Docker Compose Mount](#docker-compose-mount)
3. [Connect a Repository (`connect_repository`)](#connect-a-repository)
4. [Install the Git Hook (`install-git-hook.sh`)](#install-the-git-hook)
5. [Secret Management and Rotation](#secret-management-and-rotation)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Docker Compose up and backend healthy: `curl -fs http://localhost:8000/health`
- The board exists (run `docker compose exec -T backend python -m app.cli bootstrap` if not)
- `git` and `curl` available on the host machine

---

## Docker Compose Mount

The backend container must be able to read the git repository at the path you provide
to `connect_repository`. The `local_path` argument must start with `/repos/` (container-side)
which maps to a host path via the Docker volume mount.

Example `docker-compose.yml` volume section (already configured for project-hub itself):

```yaml
services:
  backend:
    volumes:
      - ./:/repos/project-hub:ro   # host repo → container read-only mount
```

The `:ro` flag enforces the G2 read-only contract — the backend never writes to the repo.

To add a new project, append a new volume entry and restart the backend:

```yaml
      - /path/to/your/project:/repos/your-project:ro
```

Then `docker compose restart backend`.

---

## Connect a Repository

Once the volume is mounted, register the repo with its board and run the initial backfill:

```bash
# Basic (local provider, no remote URL needed):
docker compose exec -T backend python -m app.cli connect_repository \
  --board PH \
  --local-path /repos/project-hub \
  --default-branch main \
  --rotate-secret \
  --json
```

This command:

1. Creates or updates the `repositories` table row for the board.
2. Generates a fresh `refresh_secret` (48-hex chars) stored in `board.roles["refresh_secret"]`.
   The secret is printed **once** in JSON output — save it for the hook installer.
3. Runs `sync_repo` backfill (walks up to `git_backfill_limit=2000` commits).

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--board KEY` | (required) | Board key (e.g. `PH`) |
| `--local-path /repos/...` | (required) | Container-side path; must start with `/repos/` |
| `--remote URL` | `None` | Remote URL for display/webhook correlation; optional for `local` provider |
| `--default-branch NAME` | `main` | Default branch for graph navigation |
| `--provider local\|github\|gitlab` | `local` | Provider tag |
| `--rotate-secret` | false | Regenerate secret even if one already exists |
| `--no-backfill` | false | Skip initial `sync_repo` (useful for testing) |
| `--json` | false | Machine-readable JSON output (includes `refresh_secret` if minted) |

### JSON output fields

```json
{
  "repo_id": "<uuid>",
  "board_key": "PH",
  "local_path": "/repos/project-hub",
  "remote_url": "https://github.com/org/repo.git",
  "default_branch": "main",
  "provider": "local",
  "last_synced_sha": "<sha>",
  "new_commits": 42,
  "refresh_secret": "<48-hex-chars or null if unchanged>"
}
```

`refresh_secret` is `null` when `--rotate-secret` was **not** passed and a secret
already existed. Use `--rotate-secret` to force regeneration (see [Secret Rotation](#secret-management-and-rotation)).

### PH self-bootstrap (one-time)

```bash
# Get origin URL from host
REMOTE=$(git -C ~/Documents/project-hub remote get-url origin 2>/dev/null || echo "")

# Connect + mint secret + backfill
SECRET=$(docker compose exec -T backend python -m app.cli connect_repository \
  --board PH \
  --local-path /repos/project-hub \
  --remote "$REMOTE" \
  --default-branch main \
  --rotate-secret \
  --json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['refresh_secret'])")

# Install git hook
./scripts/install-git-hook.sh ~/Documents/project-hub PH http://localhost:8000 "$SECRET"

# Verify: dummy commit triggers live refresh
git commit --allow-empty -m "test(PH): hook smoke"
sleep 6
curl -s http://localhost:8000/api/boards/PH/git/graph?limit=3 | python3 -m json.tool | head -30
curl -s http://localhost:8000/api/boards/PH/git/status
```

---

## Install the Git Hook

`scripts/install-git-hook.sh` installs a fire-and-forget POST hook into the repo's
`.git/hooks/` directory (or the configured `core.hooksPath`). The hook triggers a
live git cache refresh on every commit, merge, checkout, and rebase.

### Usage

```bash
./scripts/install-git-hook.sh <repo-path> <board-key> [backend-url] [secret] [--hooks-dir <path>]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `repo-path` | (required) | Host-side path to the git repo |
| `board-key` | (required) | Board key (e.g. `PH`) |
| `backend-url` | `http://localhost:8000` | Backend URL reachable from the host |
| `secret` | (empty) | Value of `refresh_secret` from `connect_repository --json` |
| `--hooks-dir <path>` | auto-detected | Override hooks directory (for CI or custom `core.hooksPath`) |

### Idempotency

| Scenario | Outcome |
|---|---|
| First run | Creates hook files with `#!/bin/sh` shebang + marker block |
| Re-run with identical args | Detects matching marker block → `already installed (matched)`, no file change |
| Re-run with changed secret/url | Replaces marker block in-place; preserves any surrounding custom hook content |
| Hook file already exists (custom) | Appends marker block; does not overwrite existing content |

### Worktree safety

The script uses `git rev-parse --git-common-dir` to locate the shared hooks directory.
All worktrees of the same repo share one hooks set under the main repo's `.git/hooks/`.
Running the installer from any worktree path installs correctly into the main repo.

### What the hook does

Each installed hook (post-commit, post-merge, post-checkout, post-rewrite) fires:

```sh
(command -v curl >/dev/null 2>&1 && \
  curl -fsS -m 3 -X POST \
    -H 'X-Git-Refresh-Token: <secret>' \
    'http://localhost:8000/api/boards/PH/git/refresh' \
    >/dev/null 2>&1 &) || true
```

Key design points:
- **Background subshell** (`&`) — the commit returns immediately; hook latency <50ms.
- **3-second timeout** — if the backend is unreachable, the curl exits after 3s (already in background; no user impact).
- **`|| true`** — hook never fails; commit is never blocked.
- **`curl` guard** — if `curl` is not in PATH, the hook silently skips (warn at install time only).

---

## Secret Management and Rotation

The `refresh_secret` is a 48-hex-character random string stored in `board.roles["refresh_secret"]`
in the database. It is **never committed to git**; it lives only in:

- The PostgreSQL/SQLite database (DB-side)
- The `.git/hooks/` files of the linked repository (hook-side)

### Security notes

- `.git/` is git-ignored by definition — hook files are never committed.
- On shared/multi-user machines, consider `chmod 700 .git/hooks/` to restrict read access.
- The secret is printed **only** when first minted (or `--rotate-secret`) — store it immediately.
- After rotation, re-run `install-git-hook.sh` with the new secret to update all hook files.

### Rotation procedure

```bash
# 1. Rotate secret in DB
NEW_SECRET=$(docker compose exec -T backend python -m app.cli connect_repository \
  --board PH \
  --local-path /repos/project-hub \
  --rotate-secret \
  --json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['refresh_secret'])")

# 2. Update hook files with new secret (idempotent update)
./scripts/install-git-hook.sh ~/Documents/project-hub PH http://localhost:8000 "$NEW_SECRET"

# 3. Verify hooks updated
grep "X-Git-Refresh-Token" ~/Documents/project-hub/.git/hooks/post-commit
```

> **WARNING**: After rotation, the old secret is immediately invalid. Any hook files
> not updated will receive 401 responses (they log to stderr but never block commits).

---

## Troubleshooting

### Hook not triggering

1. Check hook is installed and executable:
   ```bash
   ls -la ~/.git/hooks/post-commit
   cat ~/Documents/project-hub/.git/hooks/post-commit
   ```
2. Run hook manually to see output:
   ```bash
   sh ~/Documents/project-hub/.git/hooks/post-commit
   ```
3. Check backend is reachable from host:
   ```bash
   curl -v http://localhost:8000/health
   ```
4. Check board has `refresh_secret` configured:
   ```bash
   curl -s -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8000/api/boards/PH | python3 -m json.tool | grep -i secret
   ```

### Port conflict (backend not on 8000)

Re-run `install-git-hook.sh` with the correct URL:

```bash
./scripts/install-git-hook.sh ~/Documents/project-hub PH http://localhost:9000 "$SECRET"
```

### `curl not found` warning

Install curl or specify the full path in the hook. The hook silently skips if curl is
missing — commits are never blocked. The background poller (`git_poll_cron`, every 30s)
will pick up any missed commits automatically.

### Hook installed but graph not updating

1. Verify the backend can read the repo mount:
   ```bash
   docker compose exec -T backend ls /repos/project-hub
   ```
2. Check backend logs for sync errors:
   ```bash
   docker compose logs backend --tail 50
   ```
3. Check `GET /api/boards/PH/git/status` for `last_synced_at` and `last_synced_sha`.
4. Trigger a manual refresh (bypass hook):
   ```bash
   curl -s -X POST \
     -H "X-Git-Refresh-Token: $SECRET" \
     http://localhost:8000/api/boards/PH/git/refresh
   ```

### `refresh_secret not configured` (403)

The board has no secret — run `connect_repository --rotate-secret` to mint one, then
reinstall the hook.

### Bare repository error

The hook installer does not support bare repositories. Use a standard (non-bare)
working-tree clone. The backend G2 reader also requires a non-bare clone.
