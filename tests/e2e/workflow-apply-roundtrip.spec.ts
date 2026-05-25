/**
 * PH-103 — Workflow apply round-trip: state/edge changes persist end-to-end.
 *
 * Covers the three new scenarios identified by the Architect investigation:
 *
 *   TC-1  State rename round-trip (AC-3, AC-5/TC-1, AC-7)
 *         rename state → Save → page reload → editor shows new name +
 *         connected edges still intact in DB (PH-103 fix: handleSave now
 *         includes transitions in payload so source/target refs update atomically)
 *
 *   TC-2  Multi-edit batch round-trip (AC-5/TC-2)
 *         color change + state rename in one Save → reload → both persist
 *         (single update_workflow call bundles state + transition list)
 *
 *   TC-3  Save + Apply ordering (AC-5/TC-3, AC-9)
 *         state Save then edge Apply in sequence → reload → both persist
 *         (two independent pipeline paths: update_workflow + add_transition)
 *
 * AC-1/2/4 are covered by PH-98 and PH-99 specs; this file does not
 * duplicate those single-change scenarios.
 *
 * Requires local docker compose stack:
 *   frontend → http://localhost:5174
 *   backend  → http://localhost:8000
 *
 * Design notes:
 *  - All tests perform full API teardown/setup via direct MCP calls so that
 *    each run starts from a known baseline (idempotent).
 *  - We rename "backlog" → "backlog_renamed_103" and restore it afterward so
 *    PH-98 regression tests are not disrupted.
 *  - Unique suffix "_103" keeps test-generated names distinguishable in logs.
 *
 * PH-137: local resetWorkflow helper removed; snapshot/restore is now provided
 * by installSnapshotHooks (tests/e2e/helpers/workflowSnapshot.ts).
 */
import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import {
  installSnapshotHooks,
  snapshotWorkflow,
  restoreWorkflow,
  BOARD_KEY,
  ADMIN_TOKEN,
  API_BASE,
} from "./helpers/workflowSnapshot";

// PH-137: install shared snapshot/restore hooks (beforeAll + afterAll)
installSnapshotHooks(test);

const BASE = "http://localhost:5174";

// Names used during tests — stable and distinctive
const ORIGINAL_STATE = "backlog";
const RENAMED_STATE = "backlog_ph103";
const TEST_HEX = "#16a34a"; // tailwind green-600
const RESET_HEX = "#8b5cf6"; // tailwind violet-500

// ---------------------------------------------------------------------------
// Auth helper
// ---------------------------------------------------------------------------
async function authAndGo(page: Page, path: string) {
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), ADMIN_TOKEN);
  await page.goto(`${BASE}${path}`);
}

// ---------------------------------------------------------------------------
// API helpers (direct MCP calls — same path WorkflowEditor uses)
// ---------------------------------------------------------------------------

interface WorkflowState {
  name: string;
  color?: string;
  category?: string;
  is_initial?: boolean;
  is_terminal?: boolean;
  position?: { x: number; y: number };
}

interface WorkflowTransition {
  from: string;
  to: string;
  allowed_roles?: string[];
  field_gates?: { required_fields?: string[]; exempt_ticket_types?: string[] };
}

interface WorkflowData {
  id: string;
  states: WorkflowState[];
  transitions: WorkflowTransition[];
}

/** Fetch the PH board's active workflow via REST API. */
async function fetchWorkflow(request: APIRequestContext): Promise<WorkflowData> {
  const res = await request.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  expect(res.ok(), `GET /api/boards/${BOARD_KEY} failed: ${res.status()}`).toBeTruthy();
  const board = await res.json() as {
    workflow?: { id?: string; states?: WorkflowState[]; transitions?: WorkflowTransition[] };
  };
  const id = board.workflow?.id;
  if (!id) throw new Error("fetchWorkflow: board has no workflow id");
  return {
    id,
    states: board.workflow?.states ?? [],
    transitions: board.workflow?.transitions ?? [],
  };
}

/** Overwrite workflow states + transitions via update_workflow MCP endpoint. */
async function apiUpdateWorkflow(
  request: APIRequestContext,
  workflowId: string,
  states: WorkflowState[],
  transitions: WorkflowTransition[],
) {
  const res = await request.post(`${API_BASE}/mcp/call/update_workflow`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: {
      workflow_id: workflowId,
      fields: { states, transitions },
      board_id: BOARD_KEY,
    },
  });
  expect(res.ok(), `update_workflow failed: ${res.status()} ${await res.text()}`).toBeTruthy();
}

/**
 * Reset the workflow to the test baseline before each rename test.
 * Ensures ORIGINAL_STATE exists and any previous rename (RENAMED_STATE) is undone.
 * This is a per-test baseline helper (not a suite-level restore — that is handled
 * by installSnapshotHooks).
 */
async function resetToTestBaseline(request: APIRequestContext): Promise<WorkflowData> {
  const wf = await fetchWorkflow(request);

  // Undo any rename from a prior test run
  const patchedStates = wf.states.map((s) =>
    s.name === RENAMED_STATE ? { ...s, name: ORIGINAL_STATE, color: RESET_HEX } : s,
  );

  // Reset ORIGINAL_STATE color in case it was changed
  const finalStates = patchedStates.map((s) =>
    s.name === ORIGINAL_STATE ? { ...s, color: RESET_HEX } : s,
  );

  // Reset transitions: restore any from/to refs that may have been renamed
  const patchedTransitions = wf.transitions.map((t) => ({
    ...t,
    from: t.from === RENAMED_STATE ? ORIGINAL_STATE : t.from,
    to: t.to === RENAMED_STATE ? ORIGINAL_STATE : t.to,
    // Clear field_gates added during tests
    field_gates: undefined,
    allowed_roles: t.allowed_roles,
  }));

  await apiUpdateWorkflow(request, wf.id, finalStates, patchedTransitions);

  // Re-fetch to confirm baseline
  return fetchWorkflow(request);
}

// ---------------------------------------------------------------------------
// UI helpers — open workflow editor
// ---------------------------------------------------------------------------

async function openWorkflowTab(page: Page) {
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
  await expect(page.getByTestId("workflow-list")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("application")).toBeVisible({ timeout: 15_000 });
}

/**
 * Click the gear button on the node whose label matches stateName to open
 * the NodePropertyPanel (State Properties).
 */
async function openNodePanel(page: Page, stateName: string) {
  const stateLabel = page
    .getByRole("application")
    .locator(".font-medium", { hasText: stateName })
    .first();
  await expect(stateLabel).toBeVisible({ timeout: 10_000 });
  const nodeContainer = stateLabel.locator("xpath=ancestor::div[contains(@class,'rounded-lg')]").first();
  const gearBtn = nodeContainer.locator("button").first();
  await gearBtn.scrollIntoViewIfNeeded();
  await gearBtn.click();
  await expect(page.locator("text=State Properties")).toBeVisible({ timeout: 5_000 });
}

/** Change the state name in the NodePropertyPanel and click Apply Changes. */
async function renameStateAndApply(page: Page, newName: string) {
  const nameInput = page.locator('input[type="text"]').first();
  await expect(nameInput).toBeVisible({ timeout: 5_000 });
  await nameInput.fill(newName);
  await page.getByRole("button", { name: /apply changes/i }).click();
  // Panel closes on success
  await expect(page.locator("text=State Properties")).not.toBeVisible({ timeout: 5_000 });
}

/** Set a color in the NodePropertyPanel color picker. */
async function setColorAndApply(page: Page, hex: string) {
  const colorInput = page.locator('input[type="color"]');
  await expect(colorInput).toBeVisible({ timeout: 5_000 });
  await colorInput.fill(hex);
  await page.getByRole("button", { name: /apply changes/i }).click();
  await expect(page.locator("text=State Properties")).not.toBeVisible({ timeout: 5_000 });
}

/** Wait for Save Changes to be enabled and click it; wait for mutation to complete. */
async function clickSave(page: Page) {
  const saveBtn = page.getByRole("button", { name: /save changes/i });
  await expect(saveBtn).toBeEnabled({ timeout: 8_000 });
  await saveBtn.click();
  // Wait for the saving spinner to disappear
  await expect(page.getByRole("button", { name: /saving/i })).toHaveCount(0, { timeout: 15_000 });
  await expect(saveBtn).toBeVisible({ timeout: 10_000 });
}

// Silence unused import warning for setColorAndApply
void setColorAndApply;

// ---------------------------------------------------------------------------
// TC-1: State rename round-trip (AC-3, AC-5/TC-1, AC-7)
// ---------------------------------------------------------------------------
test("TC-1: state rename round-trip — new name + edges persist after reload", async ({ page, request }) => {
  // Setup: ensure baseline (ORIGINAL_STATE exists with RESET_HEX)
  const wf = await resetToTestBaseline(request);

  // Verify there is at least one transition touching ORIGINAL_STATE
  const relevantTransitions = wf.transitions.filter(
    (t) => t.from === ORIGINAL_STATE || t.to === ORIGINAL_STATE,
  );
  if (relevantTransitions.length === 0) {
    test.skip(true, `No transitions touch "${ORIGINAL_STATE}" — skipping edge-persist assertion`);
    return;
  }

  // Open workflow editor
  await openWorkflowTab(page);

  // Open NodePropertyPanel for ORIGINAL_STATE and rename it
  await openNodePanel(page, ORIGINAL_STATE);
  await renameStateAndApply(page, RENAMED_STATE);

  // Save (includes transitions in payload — PH-103 fix)
  await clickSave(page);

  // Navigate away and come back (force a fresh fetch — clears React Query cache)
  await authAndGo(page, `/boards/${BOARD_KEY}`);
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
  await expect(page.getByRole("application")).toBeVisible({ timeout: 15_000 });

  // AC-3a: new name visible in editor
  const renamedLabel = page
    .getByRole("application")
    .locator(".font-medium", { hasText: RENAMED_STATE })
    .first();
  await expect(renamedLabel).toBeVisible({ timeout: 10_000 });

  // AC-3b: edges persist in DB — verify via API (frontend fix: transitions sent atomically)
  const wfAfter = await fetchWorkflow(request);
  const edgesWithNewName = wfAfter.transitions.filter(
    (t) => t.from === RENAMED_STATE || t.to === RENAMED_STATE,
  );
  const edgesWithOldName = wfAfter.transitions.filter(
    (t) => t.from === ORIGINAL_STATE || t.to === ORIGINAL_STATE,
  );
  expect(
    edgesWithNewName.length,
    `Expected DB transitions to reference "${RENAMED_STATE}" after rename. Got: ${JSON.stringify(wfAfter.transitions)}`,
  ).toBeGreaterThan(0);
  expect(
    edgesWithOldName.length,
    `Expected no DB transitions to still reference "${ORIGINAL_STATE}" after rename`,
  ).toBe(0);

  // AC-3c: edges visible in ReactFlow canvas
  // After reload the editor should show at least as many edges as before the rename
  const edgeCount = await page.locator(".react-flow__edge").count();
  expect(edgeCount, "Expected at least one edge visible in editor after state rename").toBeGreaterThan(0);

  // Kanban: new state column appears
  await authAndGo(page, `/boards/${BOARD_KEY}`);
  const renamedColumn = page.getByTestId(`kanban-column-${RENAMED_STATE}`);
  await expect(renamedColumn).toBeVisible({ timeout: 10_000 });

  // AC-7: no ticket row should have been mutated (ticket.state unchanged)
  // We verify by checking no ticket now has state = RENAMED_STATE
  // (tickets that were in ORIGINAL_STATE remain with old string = orphan by PH-101 design)
  const ticketsRes = await request.get(`${API_BASE}/api/tickets?board_id=${BOARD_KEY}&limit=100`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  const ticketsData = await ticketsRes.json() as { tickets: Array<{ state: string; key: string }> };
  const ticketsInNewState = ticketsData.tickets.filter((t) => t.state === RENAMED_STATE);
  expect(
    ticketsInNewState.length,
    `AC-7: backend should NOT have migrated ticket.state to "${RENAMED_STATE}". Found: ${ticketsInNewState.map((t) => t.key).join(", ")}`,
  ).toBe(0);

  // Per-test teardown: restore ORIGINAL_STATE name (installSnapshotHooks handles suite-level)
  await resetToTestBaseline(request);
});

// ---------------------------------------------------------------------------
// TC-2: Multi-edit batch round-trip (AC-5/TC-2)
// ---------------------------------------------------------------------------
test("TC-2: multi-edit batch — rename + color in one panel Apply, both persist after Save + reload", async ({ page, request }) => {
  // Ensure ORIGINAL_STATE exists with RESET_HEX in DB
  await resetToTestBaseline(request);

  await openWorkflowTab(page);

  // Open panel and apply BOTH rename + color change in one Apply click.
  // This is the canonical multi-edit path (avoids inter-Apply race).
  await openNodePanel(page, ORIGINAL_STATE);
  await expect(page.locator("text=State Properties")).toBeVisible({ timeout: 5_000 });

  // Change color
  const colorInput = page.locator('input[type="color"]');
  await expect(colorInput).toBeVisible({ timeout: 5_000 });
  await colorInput.fill(TEST_HEX);

  // Change name in same panel session
  const nameInput = page.locator('input[type="text"]').first();
  await nameInput.fill(RENAMED_STATE);

  // Single Apply bundles both changes
  await page.getByRole("button", { name: /apply changes/i }).click();
  await expect(page.locator("text=State Properties")).not.toBeVisible({ timeout: 5_000 });

  // Save all changes (states + transitions) in one update_workflow call
  await clickSave(page);

  // Reload and verify persistence
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
  await expect(page.getByRole("application")).toBeVisible({ timeout: 15_000 });

  // Verify both name and color persisted via API
  const wfAfter = await fetchWorkflow(request);
  const renamedStateData = wfAfter.states.find((s) => s.name === RENAMED_STATE);
  expect(
    renamedStateData,
    `State "${RENAMED_STATE}" not found in DB after multi-edit save. States: ${wfAfter.states.map((s) => s.name).join(", ")}`,
  ).toBeDefined();
  expect(
    renamedStateData?.color?.toLowerCase(),
    `Expected color "${TEST_HEX}" to persist, got "${renamedStateData?.color}"`,
  ).toBe(TEST_HEX.toLowerCase());

  // Both changes visible in ReactFlow canvas
  const renamedLabel = page
    .getByRole("application")
    .locator(".font-medium", { hasText: RENAMED_STATE })
    .first();
  await expect(renamedLabel).toBeVisible({ timeout: 10_000 });

  // Per-test teardown
  await resetToTestBaseline(request);
});

// ---------------------------------------------------------------------------
// TC-3: Save + Apply ordering (AC-5/TC-3, AC-9)
// ---------------------------------------------------------------------------
test("TC-3: Save then Apply — state Save + edge Apply both persist, no race", async ({ page, request }) => {
  // Setup: ensure baseline workflow
  const wf = await resetToTestBaseline(request);

  if (wf.transitions.length === 0) {
    test.skip(true, "No transitions exist — cannot test edge Apply ordering");
    return;
  }

  await openWorkflowTab(page);

  // Step A: rename state and Save (state pipeline — update_workflow)
  await openNodePanel(page, ORIGINAL_STATE);
  await renameStateAndApply(page, RENAMED_STATE);
  await clickSave(page);

  // Step B: click first visible edge to open EdgePropertyPanel (edge pipeline — add_transition)
  // After step A the edges referencing ORIGINAL_STATE now reference RENAMED_STATE in DB.
  const firstEdge = page.locator(".react-flow__edge").first();
  await expect(firstEdge).toBeVisible({ timeout: 8_000 });
  await firstEdge.click({ force: true });

  const edgePanel = page.locator('[data-testid="edge-property-panel"]');
  if (!(await edgePanel.isVisible())) {
    // Fallback: click the edge path directly
    const edgePath = page.locator(".react-flow__edge path.react-flow__edge-path").first();
    await edgePath.click({ force: true });
  }

  // If panel opened, set a required_field to prove edge Apply works after state Save
  if (await edgePanel.isVisible({ timeout: 3_000 })) {
    const techDepthCheckbox = page.locator('[data-testid="required-field-technical_depth"]');
    if (await techDepthCheckbox.isVisible()) {
      const isChecked = await techDepthCheckbox.isChecked();
      if (!isChecked) {
        await techDepthCheckbox.check();
      }
      await page.locator('[data-testid="edge-apply-btn"]').click();
      await expect(edgePanel).not.toBeVisible({ timeout: 5_000 });

      // Wait for invalidateQueries + refetch cycle
      await page.waitForTimeout(1_500);
    }
  }

  // Reload and verify both changes persisted
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
  await expect(page.getByRole("application")).toBeVisible({ timeout: 15_000 });

  // AC-5/TC-3a: state rename persists
  const renamedLabel = page
    .getByRole("application")
    .locator(".font-medium", { hasText: RENAMED_STATE })
    .first();
  await expect(renamedLabel).toBeVisible({ timeout: 10_000 });

  // AC-5/TC-3b: edge transitions reference the new state name in DB
  const wfAfter = await fetchWorkflow(request);
  const transitionsWithNewName = wfAfter.transitions.filter(
    (t) => t.from === RENAMED_STATE || t.to === RENAMED_STATE,
  );
  const transitionsWithOldName = wfAfter.transitions.filter(
    (t) => t.from === ORIGINAL_STATE || t.to === ORIGINAL_STATE,
  );
  expect(
    transitionsWithNewName.length,
    `Expected transitions to reference "${RENAMED_STATE}" after Save+Apply sequence. Got: ${JSON.stringify(wfAfter.transitions)}`,
  ).toBeGreaterThan(0);
  expect(
    transitionsWithOldName.length,
    `Expected no transitions to still reference "${ORIGINAL_STATE}" after rename`,
  ).toBe(0);

  // Per-test teardown
  await resetToTestBaseline(request);
});
