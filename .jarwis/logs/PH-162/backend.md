# PH-162 Backend Log — G13 Board Settings Repository (backend portion B1-B6)

## [2026-06-05] Backend implementation

**Actor**: jarwis-backend
**Branch**: ph-162-g13-board-settings-repository-b-l-m
**Phase**: B1-B6 (backend only; frontend F1-F11 follows on same branch)

### Changes

**`backend/app/api/repositories.py`**
- Added `_resolve_actor_from_bearer(session, authorization)` helper — optional bearer resolution that loads all active actors and bcrypt-verifies without raising; returns `Actor | None`.
- Extended `api_git_refresh` with optional `Authorization: Bearer` header parameter. Auth precedence: (1) bearer present + actor is board admin → 202 via bearer path; (2) bearer present + actor NOT admin → 403 (does NOT fall through to shared-secret — R1 safety ordering); (3) no bearer → shared-secret path unchanged. Both paths dispatch to the same `_locked_sync_repo` sync code.
- Added `POST /repository/rotate-refresh-secret` endpoint (`api_rotate_refresh_secret`) — uses existing `_require_board_admin` dep; calls `rotate_refresh_secret` service; commits; returns `RotateRefreshSecretResponse` with plaintext secret + hook install command.
- Added `selectinload` import (needed for `_resolve_actor_from_bearer`).
- Added `verify_token` import.
- Added `RotateRefreshSecretResponse`, `rotate_refresh_secret` imports.

**`backend/app/schemas.py`**
- Added `RotateRefreshSecretResponse(BaseModel)` — `refresh_secret: str = Field(min_length=48, max_length=48)`, `hook_install_command: str`.

**`backend/app/services/repositories.py`**
- Added `rotate_refresh_secret(session, board) -> str` — `secrets.token_hex(24)` (48 hex), `dict(board.roles)` copy, `flag_modified(board, "roles")`, `session.flush()`, returns plaintext. Caller must commit.
- Added `secrets`, `flag_modified` imports.

**`backend/tests/test_repository_settings.py`** (new file, 6 tests)
- `test_refresh_bearer_admin_no_secret_header_queued` — AC-B1
- `test_refresh_non_admin_bearer_rejected_403` — AC-B2
- `test_refresh_shared_secret_still_works` — AC-B3 (regression)
- `test_rotate_secret_admin_returns_plaintext_and_masked_on_get` — AC-B4
- `test_rotate_secret_non_admin_403` — AC-B5
- `test_refresh_after_rotate_old_secret_fails_new_succeeds` — AC-B6

**`docs/codewiki/components/git-integration.md`** — G13 entries in Current behavior (G6 section), Design decisions (3 bullets), Known gotchas (2 bullets); frontmatter `last_touched_ticket: PH-162`.

**`docs/codewiki/.codemap`** — 2 new entries: `backend/app/services/repositories.py` + `frontend/src/components/repository/*.tsx`.

**`docs/codewiki/log.md`** — G13 ingest entry appended.

### Test results

- New tests: 6/6 pass
- Regression: `pytest -k "git or repository or refresh"` → 138 pass, 176 deselected
- ruff: clean on changed files
- mypy --strict: 0 errors on changed files (8 pre-existing errors in email.py/background_tasks.py/events/bus.py)

### Decisions

- Bearer alt-auth added as `_resolve_actor_from_bearer` inline helper (not `Depends(current_actor)`) — refresh endpoint is also called by anonymous hooks; restructuring deps.py for optional bearer would break existing clean patterns
- R1 safety: admin membership check happens BEFORE shared-secret branch — non-admin bearer cannot bypass secret
- `public_base_url` not in Settings → `getattr(settings, "public_base_url", None) or "http://localhost:8000"` fallback for hook_install_command
- No migration needed — roles column is JSON
