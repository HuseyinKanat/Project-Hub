# UC-01: Create a new board from the UI

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Create a new board from the UI |
| Description: | An eligible user (admin-of-some-board) creates a new board through a "New board" button + form in the frontend, which calls the PH-331 backend endpoint `POST /api/boards`. On success the board is surfaced and the user is pointed to a member-add path. |
| Actors: | Board-creator user (a human who is admin of at least one board) |
| Triggers: | User clicks the "New board" action in the board list / nav |
| Pre-Conditions: | User is authenticated; user is admin-of-some-board (so `require_global_board_creator` will pass); board list UI is loaded |
| Post-Conditions: | Main Flow: a new board exists, the caller is its admin member, and the UI shows the new board · Alternate Flow: A1 ineligible user is prevented from reaching the form · Exception Flow: E1 duplicate key / E2 permission denied / E3 validation error — no board created, actionable error shown |
| Includes: | None |
| Extension Points: | Add board members (POST /api/boards/{board_id}/members) — reachable as a hint/link after creation |
| References: | PH-332 (this ticket), PH-331 (backend contract), AC1–AC9; endpoint POST /api/boards |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Eligible user opens the board list | UI shows a "New board" action, visible and enabled (AC1) |
| 2 | User clicks "New board" | UI opens the create-board form with fields: key, name, description, project_type (AC2) |
| 3 | User enters key, name, description and selects/enters project_type | UI validates client-side: key + name required, key matches API key format; submit stays disabled until valid (AC2) |
| 4 | User submits the valid form | UI sends `POST /api/boards` with the field values |
| 5 | Backend returns 201 (board created, caller auto-added as admin) | UI surfaces the new board (navigate to / show it) and the board list reflects it without manual refresh (AC3) |
| 6 | User views the success state | UI presents a visible path to add members (link to members UI / POST /members) (AC7) |

## Alternate Flows

### A1 – Ineligible user

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – A user who is NOT admin-of-some-board views the board list |
| Post-Condition: | User cannot start a board-create; no request is sent |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Ineligible user views the board list | UI either hides the "New board" action OR shows it disabled with a clear reason (AC1) |

## Exception Flows

### E1 – Duplicate key (409)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E1 – Submitted key already exists |
| Post-Condition: | No board created; user stays on the form; no navigation |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | User submits a key that already exists | Backend returns 409; UI shows a clear inline error on the key field indicating the key is taken (AC4) |

### E2 – Permission denied (403)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E2 – Actor is not eligible at submit time (403 fallback) |
| Post-Condition: | No board created; user informed with a permission message |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | User submits but backend returns 403 | UI shows a clear permission message, not a raw error (AC5) |

### E3 – Validation error (422)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E3 – Backend rejects field values |
| Post-Condition: | No board created; field errors mapped back onto the form |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E3-1 | User submits but backend returns 422 | UI maps field-level errors onto the corresponding form fields (AC6) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-28 | 1.0 | Initial Version | jarwis-pm |
