# UC-01: Maintain and surface board-scoped notes / guardrails

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Maintain and surface board-scoped notes / guardrails |
| Description: | A board admin records a recurring-mistake / guardrail note against a board; the note is persisted, listed in the Board Settings panel for humans, and retrievable by agents via an MCP read tool. Passive surface only — no dispatch auto-injection in this UC. |
| Actors: | Board admin (human); Frontend (BoardSettings "Notes / Guardrails" panel); Backend `board_notes` API; Agent (via MCP `get_board_notes`) |
| Triggers: | A human opens the Board Settings "Notes / Guardrails" panel to read or add a note; OR an agent calls `get_board_notes(board)` during its work |
| Pre-Conditions: | The board exists; for add/delete the actor can administer the board; the `board_notes` table migration is applied |
| Post-Conditions: | Main Flow: the note is persisted (`body + created_by + created_at + board_id`) and appears in the panel · Alternate Flow: an agent receives the board's notes read-only via MCP; an admin can delete an obsolete note · Exception Flow: a failed create leaves no partial note and the panel shows an error while keeping the typed body |
| Includes: | None |
| Extension Points: | None (dispatch auto-injection is the deferred P6b; conditional trigger-fired rules are the deferred P7 — neither is an extension of this UC) |
| References: | PH-336; AC1–AC5; `GET/POST/DELETE /api/boards/{board_id}/notes`; MCP `get_board_notes`; migration `down_revision = ph330agentowner` |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Board admin opens the "Notes / Guardrails" panel in Board Settings | Frontend requests `GET /api/boards/{board_id}/notes`; backend returns the board's notes |
| 2 | Panel lists existing notes (body + author + timestamp) | Admin reads the accumulated board guardrails |
| 3 | Admin types a note body and submits | Frontend sends `POST /api/boards/{board_id}/notes` with `{body}` |
| 4 | Backend persists a `board_notes` row (`body, created_by, created_at, board_id`) | Returns 201 with the created note |
| 5 | Panel refreshes with the new note at the top of the list | Admin sees the note stored and board-scoped |

## Alternate Flows

### A1 – Agent pulls notes via MCP

| | |
|---|---|
| Branched From: | Main Flow, Step 1 (alternative entry — an agent instead of a human) |
| Flow Scenario: | A1 – an agent calls `get_board_notes(board)` while working on that board |
| Post-Condition: | The agent receives the board's current notes read-only; no write occurs |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Agent calls MCP `get_board_notes(board)` | Backend returns the board's notes (read-only tool) |
| A1-2 | Agent reads the notes as guardrail context | No mutation occurs; the store is unchanged |

### A2 – Delete an obsolete note

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A2 – admin removes a note that no longer applies |
| Post-Condition: | The note is removed from the store and from the panel list |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Admin triggers delete on a listed note | Frontend sends `DELETE /api/boards/{board_id}/notes/{id}` |
| A2-2 | Backend deletes the row | The list refreshes without the removed note |

## Exception Flows

### E1 – Create fails (validation or server error)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E1 – the create request fails (e.g. empty body rejected, or a server error) |
| Post-Condition: | No partial note is persisted; the panel shows an error and preserves the typed body for retry |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Backend rejects the create (empty body) or errors | Returns a non-201 status |
| E1-2 | Panel surfaces the error inline | No note is added; the admin can correct and retry |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-03 | 1.0 | Initial Version | jarwis-pm |
