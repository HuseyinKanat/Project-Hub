# UC-01: Admin creates a new board via REST/UI

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Admin creates a new board via REST/UI |
| Description: | A global/system admin self-serves creation of a brand-new board (key, name, description, project_type) through `POST /api/boards` (or the optional UI form), without needing the hub-host operator to run the CLI. The board is created fully seeded (default workflow + roles) with the calling admin as its board admin. |
| Actors: | Global/system admin (human, e.g. yusuf) holding a valid admin token |
| Triggers: | Admin submits the "New board" form (UI) or issues `POST /api/boards` (API) |
| Pre-Conditions: | Admin is authenticated with a valid bearer token AND satisfies the global-admin gate the Architect specifies; the requested board `key` is not already in use |
| Post-Conditions: | Main Flow: a new board exists, seeded with default workflow + roles, calling admin is a board admin member · Alternate Flow: admin then adds existing @owner agents via the pre-existing POST /members (full self-service) · Exception Flow: no board (and no orphan workflow/roles rows) is persisted; existing boards untouched |
| Includes: | None |
| Extension Points: | Add board members (existing `POST /api/boards/{board_id}/members`) — invoked after creation to reach full self-service |
| References: | PH-331; AC1 (shared service, CLI unchanged), AC2 (POST endpoint), AC3 (seeded + admin member), AC4 (global-admin gate), AC5 (validation/duplicate/atomicity), AC7 (no regression) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Admin submits create-board request with {key, name, description, project_type} and their bearer token | System authenticates the token and evaluates the global-admin gate |
| 2 | (gate passes) | System validates the payload and confirms `key` is not already taken |
| 3 | System invokes the shared create-board service (same function the CLI uses) | Board is created atomically: default workflow seeded + default roles seeded |
| 4 | System records the calling admin as a board admin member | Admin membership row created against the new board |
| 5 | System commits the transaction and returns the created board | Response 200/201 with board payload (id, key, name, project_type, ...) |

## Alternate Flows

### A1 – Admin proceeds to add existing @owner agents

| | |
|---|---|
| Branched From: | Main Flow, Step 5 |
| Flow Scenario: | A1 – After the board is created, the admin makes their existing `@owner` agents members to reach full self-service |
| Post-Condition: | New board has the admin plus the added agent members; ready for pipeline work |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Admin calls existing `POST /api/boards/{board_id}/members` for each `@owner` agent | System adds each agent as a member (existing board.member behavior, unchanged) |
| A1-2 | Admin verifies the board via `GET /api/boards` | System returns the new board with its membership list |

## Exception Flows

### E1 – Authorization gate rejects the caller

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E1 – The caller is unauthenticated (missing/invalid bearer) OR does not satisfy the global-admin gate |
| Post-Condition: | 403 PermissionDenied; no board created; handler body never runs for a bad bearer |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Non-admin (or bad-token) caller issues `POST /api/boards` | System rejects with 403 PermissionDenied and creates nothing |

### E2 – Invalid payload or duplicate key

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E2 – A required field is missing OR the requested `key` already exists |
| Post-Condition: | 4xx validation/conflict error; the create is atomic so NO orphan board/workflow/roles rows remain |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Admin submits a payload missing `key`/`name` or reusing an existing `key` | System returns a 4xx (validation/conflict) and rolls back — no partial board persisted |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-28 | 1.0 | Initial Version | jarwis-pm |
