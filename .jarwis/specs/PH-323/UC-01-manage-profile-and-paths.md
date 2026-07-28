# UC-PH-323-01: Manage profile owner slug and per-board local paths

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-323-01 |
| Use Case Name: | Manage profile owner slug and per-board local paths |
| Description: | A logged-in human opens the Profile page from the user menu to view/edit their `owner_slug` (their multi-user identity) and to CRUD the local path for each board they belong to. Board members/admins can additionally VIEW (read-only) other members' owner + path in the members view ("who works where"). Every mutation is self-only and invalidates the relevant React Query caches so all views refresh without a page reload. |
| Actors: | Human user (profile owner); board member/admin (reader); project-hub frontend; profile REST API |
| Triggers: | User opens Profile from the user menu; or edits `owner_slug` / a board's local path; or opens the board members view |
| Pre-Conditions: | User logged in; backend profile endpoints available (DRAFT-be done — this ticket is `blocked_by` it); the user's board memberships are known |
| Post-Conditions: | Main Flow: `owner_slug` + path changes persisted via REST, caches invalidated, views refresh without reload · Alternate Flow: members view shows each member's owner + path read-only · Exception Flow: an invalid `owner_slug`, or a path edit attempted before `owner_slug` is set, is blocked client-side (mirrors backend 422) |
| Includes: | None |
| Extension Points: | None |
| References: | DRAFT-fe ticket (frontend profile UI); AC-1..AC-8; DRAFT-be (blocked_by; REST `GET/PUT /api/profile` + `/api/profile/project-paths`); PH-317 slug regex `^[a-z0-9][a-z0-9-]{0,19}$`; `frontend/src/pages/` (new Profile page), `frontend/src/components/Layout.tsx` (user menu), `MembersTab.tsx` / `MembershipRow.tsx` (members view) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | User clicks their avatar/name in the user menu and selects Profile. | App navigates to the Profile page. |
| 2 | Profile page mounts. | Fetches `GET /api/profile` (owner_slug) + `GET /api/profile/project-paths`, then renders the owner_slug field and a per-board local-path list. |
| 3 | User edits `owner_slug` to a valid value and saves. | Client validates the shape, calls `PUT /api/profile`, shows a success toast, and invalidates the profile cache. |
| 4 | User adds or edits a board's local path and saves. | Client calls `PUT /api/profile/project-paths`; the list reflects the change after invalidation. |
| 5 | User removes a board's local path. | Path cleared via REST; the row disappears from the list after invalidation. |
| 6 | User (or admin) opens the board members view. | Each member row shows that member's owner + local path — read-only for everyone but self (permission-aware). |

## Alternate Flows

### A1 – Members view (who works where)

| | |
|---|---|
| Branched From: | Main Flow, Step 6 |
| Flow Scenario: | A1 – An admin inspects where each member has the project checked out |
| Post-Condition: | Every member's owner + path shown read-only; no edit control for others |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Admin opens the members tab. | Client fetches members including each one's owner + local path (list read). |
| A1-2 | Client renders each member row. | Only the current user's OWN row is editable; every other row is read-only (permission-aware: self writes, members read). |

## Exception Flows

### E1 – Invalid owner slug (client-side block)

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | E1 – User types an `owner_slug` that fails the shape rule |
| Post-Condition: | Save blocked; inline validation error shown; NO REST call (backend would 422 anyway) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | User enters `Alice!` (uppercase / symbol / >20 chars / leading hyphen). | Client validates against `^[a-z0-9][a-z0-9-]{0,19}$` and disables Save with an inline error. |
| E1-2 | User corrects the value to a valid slug. | Save re-enabled; `PUT /api/profile` proceeds. |

### E2 – Path set before owner_slug exists

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E2 – A user with no `owner_slug` tries to add a board path |
| Post-Condition: | UI prompts "set your owner slug first"; no path PUT is sent (mirrors backend 422) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | User with an empty `owner_slug` focuses the path editor. | Client shows "set your owner slug first" and blocks the path form. |
| E2-2 | User sets a valid `owner_slug`, then edits the path. | Path editing unlocked; `PUT /api/profile/project-paths` proceeds. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial draft (profile page + per-board local path CRUD) | jarwis-pm |
