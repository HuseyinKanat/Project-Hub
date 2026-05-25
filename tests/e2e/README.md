# End-to-End Test Suite — `tests/e2e/`

This directory contains Playwright E2E specs for the project-hub frontend.

---

## Workflow-mutating specs MUST call `installSnapshotHooks`

Any spec that touches the **live PH board's workflow shape** (states, transitions,
allowed_roles) MUST call `installSnapshotHooks(test)` at the top level of the file.

### Why

Six specs directly mutate the live PH workflow in the database.  Without
protection, a test crash can leave the workflow in a corrupted state, which
blocks ticket transitions for all users (this happened in the 2026-05-25
incident — see PH-137).

The `installSnapshotHooks` mechanism provides three layers of defence:

1. **Per-spec `beforeAll`**: restores the snapshot before each spec file runs.
2. **Per-spec `afterAll`**: restores the snapshot after each spec file finishes
   (including soft failures).
3. **`globalTeardown`** (wired in `playwright.config.ts`): unconditionally
   restores the canonical snapshot after the entire suite exits — catches hard
   crashes where `afterAll` is skipped.

Additionally, `globalSetup` captures the live PH workflow once at suite start
and compares it to the committed canonical fixture.  If they already differ,
**it fails fast** — preventing tests from using a corrupted baseline.

### How to use (copy-paste)

```typescript
// At the TOP LEVEL of your spec file — outside any describe/test block:
import { installSnapshotHooks } from "./helpers/workflowSnapshot";
installSnapshotHooks(test);
```

Full example:

```typescript
import { test, expect } from "@playwright/test";
import {
  installSnapshotHooks,
  ADMIN_TOKEN,
  API_BASE,
  BOARD_KEY,
} from "./helpers/workflowSnapshot";

// Install snapshot hooks — restores live PH workflow before/after this file.
installSnapshotHooks(test);

test("my workflow test", async ({ page }) => {
  // ... test body that mutates PH workflow freely ...
  // The workflow will be restored after this file finishes.
});
```

**Important:** Do NOT use the inline `ADMIN_TOKEN`, `API_BASE`, or `BOARD_KEY`
constants from a spec file directly. Always import them from
`./helpers/workflowSnapshot`.  This is enforced by AC-6 (grep check in CI).

### Currently protected specs (6 in-scope files)

The following spec files call `installSnapshotHooks` and are protected:

| Spec file | Mutation type |
|---|---|
| `workflow-apply-roundtrip.spec.ts` | Renames states, saves workflow |
| `workflow-editor-bugs.spec.ts` | Clicks `+ New workflow`, `Add state` |
| `workflow-editor-toast.spec.ts` | Clicks `Add state` repeatedly |
| `workflow-editor-edge-gates.spec.ts` | Hits live `/api/boards/PH`, mutates transitions |
| `workflow-editor-conditions.spec.ts` | Sets field gates via `set_field_gates` MCP |
| `workflow-state-color.spec.ts` | Changes state colors, saves workflow |

If you write a new spec that touches PH workflow shape, add it to this list
and add `installSnapshotHooks(test)` — otherwise the next test run may corrupt
the live board.

### Out-of-scope specs (already fully mocked — no action needed)

- `workflow-delete.spec.ts` — `page.route("**/api/boards/PH", ...)` + MCP mock
- `workflow-state-delete.spec.ts` — fully mocked
- `workflow-swap-safety.spec.ts` — fully mocked
- `members-tab.spec.ts` — mocks auth/API
- `permission-matrix.spec.ts` — mocks auth/API

---

## Canonical fixture

`backend/tests/fixtures/ph_workflow_canonical.json` is the **locked canonical
snapshot** of the PH workflow.  It defines exactly:

- 7 states: `backlog`, `blocked`, `done`, `in_progress`, `in_review`, `in_test`, `to_do`
- 10 transitions with non-empty `allowed_roles` on each

**Any intentional change to the PH workflow** (e.g. adding a new state) MUST
update this fixture in the same PR.  Otherwise the backend CI guard test
(`backend/tests/test_ph_workflow_canonical.py`) will fail and block the PR.

---

## Known limitations

### Orphan `Workflow` row leaks from `+ New workflow` clicks

`workflow-editor-bugs.spec.ts:TC-2` clicks `+ New workflow`, which creates
a new `Workflow` row and `BoardWorkflow` junction row in the database.

`installSnapshotHooks` restores the **active workflow's states and transitions**,
but does NOT delete the leaked inactive `Workflow` rows.  Over many test runs,
orphan inactive workflows accumulate in the DB.

This is out of scope for PH-137 (which addresses workflow-shape corruption only).
A follow-up ticket should implement orphan `Workflow` row cleanup.

**Workaround**: run `docker compose exec backend alembic downgrade -1 && upgrade head`
to reset the database if orphan rows cause issues.

### `globalTeardown` does not run on `SIGKILL`

If the Playwright process is killed with `SIGKILL` (not `SIGINT`/`SIGTERM`),
`globalTeardown` does not run.  The backend CI guard
(`backend/tests/test_ph_workflow_canonical.py`) is the second line of defence
in this case.

**Manual recovery** if the workflow is corrupted:

```bash
# 1. Get current workflow id
curl -s -H "Authorization: Bearer change-me-on-first-login" \
     http://localhost:8000/api/boards/PH | jq '.workflow.id'

# 2. Restore using the canonical fixture (replace <WORKFLOW_ID>):
curl -X POST \
     -H "Authorization: Bearer change-me-on-first-login" \
     -H "Content-Type: application/json" \
     -d "{\"workflow_id\": \"<WORKFLOW_ID>\", \"fields\": $(cat backend/tests/fixtures/ph_workflow_canonical.json), \"board_id\": \"PH\"}" \
     http://localhost:8000/mcp/call/update_workflow

# 3. Verify:
docker compose exec backend pytest backend/tests/test_ph_workflow_canonical.py -v
```

### Do not run Playwright while a human is editing the PH workflow in the UI

`restoreWorkflow` is an unconditional overwrite.  If a developer is editing
the PH workflow in the browser while the Playwright suite runs, the restore
will clobber those edits.  This is the same risk that existed before PH-137
(the inline `resetWorkflow` helper had the same semantics).
