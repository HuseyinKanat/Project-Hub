# UC-PH-317-01: Onboard a second user's Jarwis agent fleet with owner-scoped actors

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-317-01 |
| Use Case Name: | Onboard a second user's Jarwis agent fleet with owner-scoped actors |
| Description: | An operator provisions a second user's Jarwis sub-agent actors under an owner namespace (`jarwis-<role>@<owner>`) via the `create_jarwis_actors` CLI, so the friend's remote agents authenticate with isolated identities + tokens on the shared PH board without colliding with the primary user's suffix-less `jarwis-<role>` actors. |
| Actors: | Operator (host admin onboarding the second user); project-hub CLI (`app.cli create_jarwis_actors`); project-hub DB |
| Triggers: | Operator runs `docker compose exec backend python -m app.cli create_jarwis_actors --board PH --owner <slug> --json` |
| Pre-Conditions: | Stack healthy (health 200); PH board exists; operator has `docker compose exec` access; owner slug chosen (lowercase + digits + hyphen, ≤20 chars) |
| Post-Conditions: | Main Flow: N actors `jarwis-<role>@<owner>` exist with PH membership + freshly-minted tokens emitted as a JSON `{role: token}` map · Alternate Flow: idempotent re-run mints nothing (membership retained) / owner-scoped `--rotate` refreshes only that owner's tokens · Exception Flow: invalid slug or missing board → NO actor minted (no partial state) |
| Includes: | None |
| Extension Points: | None |
| References: | DRAFT-owner ticket (create_jarwis_actors --owner); AC-1..AC-6; `backend/app/cli.py` → `create_jarwis_actors` / `_jarwis_actor_name` / `_provision_jarwis_role` |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Operator runs `create_jarwis_actors --board PH --owner <slug> --json` | CLI parses args and validates the owner slug shape (lowercase + digits + hyphen, ≤20) |
| 2 | CLI resolves the target board by key (PH) | Board row found; provisioning proceeds |
| 3 | For each role in the mode's role set, CLI computes the actor name `jarwis-<role>@<slug>` | Names are namespaced to the owner, distinct from the suffix-less `jarwis-<role>` set |
| 4 | CLI looks up each actor by `display_name` | Absent → mints a new agent actor + random token (hashed); present → reuses without re-minting (unless `--rotate`) |
| 5 | CLI ensures each actor has PH board membership under its bare role | Membership created if missing; `agent_role_hint` stays the bare role (pm/architect/…) |
| 6 | CLI collects freshly-minted tokens into a `{role: token}` map | Only newly-minted (or rotated) tokens are included |
| 7 | CLI emits the token map as JSON to stdout | Operator captures the JSON for the second user's remote `.mcp.json` |
| 8 | Operator wires the tokens into the friend's remote `.mcp.json` and starts their session | Friend's sub-agents authenticate as `jarwis-<role>@<slug>` on the PH board |

## Alternate Flows

### A1 – Idempotent re-run (no --rotate)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | A1 – Actors for this owner already exist and no rotation is requested |
| Post-Condition: | No new actor minted, memberships retained, empty/placeholder token map returned |
| Branch To: | Main Flow Step 7 (emits empty map) → End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | CLI finds an existing `jarwis-<role>@<slug>` actor | Reuses it; no token minted |
| A1-2 | CLI checks board membership and finds it present | No membership change |

### A2 – Owner-scoped rotation

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | A2 – `--rotate` supplied for an owner-scoped set |
| Post-Condition: | Only `jarwis-<role>@<slug>` tokens refreshed; suffix-less set and other owners' tokens untouched (isolation) |
| Branch To: | Main Flow Step 5 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Operator adds `--rotate` to the owner-scoped call | CLI re-mints a fresh token only for each `jarwis-<role>@<slug>` actor |
| A2-2 | CLI never looks up the suffix-less `jarwis-<role>` names | Primary user's tokens remain valid |

## Exception Flows

### E1 – Invalid owner slug

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E1 – Owner slug fails validation (uppercase, space, symbol, or >20 chars) |
| Post-Condition: | CLI exits non-zero with a validation error; NO actor or membership created (clean, no partial state) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Operator passes `--owner "Alice!"` (or >20 chars, or uppercase) | CLI rejects the slug BEFORE any DB write |
| E1-2 | CLI prints the validation error and exits non-zero | Nothing persisted |

### E2 – Board not found

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E2 – The target board key does not resolve |
| Post-Condition: | CLI prints "board not found", returns an empty map, no actor minted |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | CLI queries the board by key and finds none | Aborts provisioning |
| E2-2 | CLI returns an empty token map | No side effect |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial draft (multi-user prep; owner-scoped actor namespacing) | jarwis-pm |
