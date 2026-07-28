# UC-PH-322-01: Register and resolve a per-owner project local path

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-322-01 |
| Use Case Name: | Register and resolve a per-owner project local path |
| Description: | A caller (a human via UI/REST, or a Jarwis agent via MCP) records the local filesystem path at which THIS user has the board's project checked out on their machine, and later resolves "my path" (`get_my_project_path`) or "whose path is this" (`list_project_paths`) from the server. This lets multi-user coordination — deploy_pending handoff (hub-host resolves its own path), understanding a foreign worktree reference — be answered server-side. The server stores the path as OPAQUE machine-local data (no existence/format check). |
| Actors: | Human user (profile owner); Jarwis agent (`jarwis-<role>@<owner>`); project-hub backend + DB |
| Triggers: | Caller invokes `set_my_project_path(board, local_path)` (MCP) or `PUT /api/profile/project-paths` (REST); or a reader invokes `get_my_project_path(board)` / `list_project_paths(board)` |
| Pre-Conditions: | Stack healthy (health 200); board exists; caller authenticated; caller's owner is resolvable — agent from the `@<owner>` display_name suffix, human from `actor.owner_slug` |
| Post-Conditions: | Main Flow: a `project_paths` row is upserted for `(owner_slug, board_id)`, an audit event is written, and the value is readable via `get_my_project_path` + `list_project_paths` · Alternate Flow: a second set on the same `(owner, board)` updates the SAME row (upsert, `updated_at` advances); a human and their whole agent fleet share ONE path per owner+board · Exception Flow: a human with no `owner_slug` → 422, nothing written (clean) |
| Includes: | None |
| Extension Points: | None |
| References: | DRAFT-be ticket (backend profile + per-owner path); AC-1..AC-8; PH-317 owner slug regex `^[a-z0-9][a-z0-9-]{0,19}$`; `backend/app/db/models/core.py` (Actor.owner_slug + project_paths); `backend/app/mcp/server.py` (tools); `backend/app/api/` (profile REST); migration head `ph320tokenlookup` |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Caller (human or agent) invokes `set_my_project_path(board=<KEY>, local_path=<host path>)`. | Server authenticates the bearer token and begins owner resolution. |
| 2 | Owner resolution runs (from Step 1). | Agent → owner parsed from the `@<owner>` suffix of `display_name`; human → owner read from `actor.owner_slug`. An owner string is obtained. |
| 3 | Server inspects `local_path` (from Step 1). | Length validated (≤255); the value is treated as OPAQUE — NO filesystem existence/format check (it is meaningful only on the owning host). |
| 4 | Path accepted (from Step 3). | Server upserts `project_paths(owner_slug, board_id)` = `local_path` on the unique key and stamps `updated_at` — a row is inserted, or an existing row updated in place. |
| 5 | Row committed (from Step 4). | Server writes an audit/history event and returns `{owner, local_path}`. |
| 6 | Later, caller invokes `get_my_project_path(board)`. | Server resolves the caller's owner (Step 2) and returns `{owner, local_path\|null}` for that `(owner, board)`. |
| 7 | A board member invokes `list_project_paths(board)`. | Server returns every board member's `{owner, local_path\|null}` (resolves "whose path is this"). |

## Alternate Flows

### A1 – Upsert on re-set

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | A1 – A path already exists for `(owner, board)` and the caller sets a new value |
| Post-Condition: | The SAME row is updated (no duplicate); `updated_at` advances |
| Branch To: | Main Flow Step 5 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Server finds an existing `(owner_slug, board_id)` row while handling `set_my_project_path`. | Updates `local_path` in place; the unique `(owner_slug, board_id)` key prevents a second row. |
| A1-2 | Server bumps `updated_at`. | Returns the updated `{owner, local_path}` — the caller observes the new value, not a duplicate. |

### A2 – Human and agent fleet share one owner path

| | |
|---|---|
| Branched From: | Main Flow, Step 6 |
| Flow Scenario: | A2 – Human `owner_slug=X` set the path; an agent `jarwis-<role>@X` reads it |
| Post-Condition: | The agent receives the SAME path the human set (per-owner, NOT per-actor) |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Agent `jarwis-qa@X` calls `get_my_project_path(board)`. | Server resolves owner = X from the `@X` suffix. |
| A2-2 | Server reads `project_paths(X, board)`. | Returns the human-set path — the whole fleet shares one path per owner+board because the key is `owner_slug`, not `actor_id`. |

## Exception Flows

### E1 – Human with no owner_slug

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – A human caller has no `owner_slug` set |
| Post-Condition: | 422 `owner_undefined`; NO row written, no event (clean, no partial state) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Human with `actor.owner_slug = NULL` calls `set_my_project_path` (or `get_my_project_path`). | Server cannot resolve an owner from the token. |
| E1-2 | Server raises 422 "owner tanımsız — profilden ayarla" BEFORE any write. | Nothing persisted; the caller is told to set their owner slug from the profile first. |

### E2 – Cross-owner write attempt (self-only)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E2 – A crafted REST payload targets a DIFFERENT owner than the authenticated caller |
| Post-Condition: | 403; no write to the other owner's row |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Owner A's token submits a write naming owner B. | Server derives the owner from the TOKEN, not from the payload. |
| E2-2 | Server rejects the owner mismatch with 403. | A cannot write B's path; the MCP `set_my_project_path` exposes NO owner parameter at all (always self). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial draft (multi-user profile + per-owner project path registry) | jarwis-pm |
