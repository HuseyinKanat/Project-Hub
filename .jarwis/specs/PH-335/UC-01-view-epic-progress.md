# UC-01: View per-epic progress rollup on a board

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | View per-epic progress rollup on a board |
| Description: | A board viewer sees, for each epic on a board, how much of its work is done (a % + "N/M done") plus the state distribution of its child tickets — all derived read-only from the current state of existing child tickets, with no new stored data. |
| Actors: | Board viewer (human, any board member); Frontend (BoardDetail); Backend epic-progress endpoint |
| Triggers: | The viewer opens or refreshes a board's detail view |
| Pre-Conditions: | The board exists and the viewer can read it; the board has ≥1 epic (a ticket referenced by others via `epic_id`) |
| Post-Conditions: | Main Flow: per-epic progress bar + "N/M done" + state histogram are rendered, computed live from current child state · Alternate Flow: child-less epics show 0/0 and epic-less tickets are grouped as "ungrouped", without error; a no-story-points epic falls back to count-based % · Exception Flow: the progress area shows an error/empty state while the rest of BoardDetail stays usable |
| Includes: | None |
| Extension Points: | None (cross-board rollup is the deferred P4, not an extension of this UC) |
| References: | PH-335; AC1–AC5; `GET /api/boards/{board_id}/epics/progress`; aggregation precedent `relationships.py:_epic_candidates` |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Viewer opens the board detail view | Frontend requests `GET /api/boards/{board_id}/epics/progress` |
| 2 | Backend runs ONE board-wide query for non-deleted child tickets (`deleted_at IS None`, same `board_id`) and buckets them by `epic_id` in memory | No per-epic query loop (no N+1); a per-epic result set is assembled |
| 3 | Backend computes each epic's `weighted_pct` (story_points-weighted when children carry points, else count-based) + `done`/`total` + `state_histogram` | Returns per-epic `{done, total, weighted_pct, state_histogram}` |
| 4 | Frontend renders a progress bar + "N/M done" per epic row and a board-level epic rollup | Viewer sees high-level per-epic progress |

## Alternate Flows

### A1 – Epic children carry no story points

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | A1 – an epic's children all have NULL `story_points` |
| Post-Condition: | `weighted_pct` falls back to a count-based percentage (single automatic rule, no knob) |
| Branch To: | Main Flow Step 4 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Backend detects the epic's children carry no story points | It computes a count-based done/total percentage instead of a weighted one |

### A2 – Child-less epic or ungrouped tickets

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A2 – an epic has zero non-deleted children, or some tickets have no `epic_id` |
| Post-Condition: | A child-less epic reports 0/0; epic-less tickets are aggregated under an "ungrouped" bucket — no error |
| Branch To: | Main Flow Step 4 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Backend finds an epic with 0 non-deleted children | Reports `done=0, total=0` without dividing by zero |
| A2-2 | Backend finds tickets with `epic_id` NULL | Aggregates them under an "ungrouped" bucket |

## Exception Flows

### E1 – Progress endpoint failure

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – the epic-progress endpoint errors or times out |
| Post-Condition: | Board detail stays usable; a non-blocking error/empty state is shown only for the progress strip |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | The backend aggregation query fails | The endpoint returns an error status |
| E1-2 | The frontend catches the error | It shows an inline error/empty state for the progress area; the rest of BoardDetail renders normally |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-03 | 1.0 | Initial Version | jarwis-pm |
