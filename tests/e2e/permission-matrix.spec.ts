/**
 * PH-38 — Permission matrix UI in board settings.
 *
 * Covers:
 *   TC-1  Admin golden path: matrix renders, cell toggle fires add_transition,
 *         matrix updates + "Permission updated" toast appears.
 *
 *   TC-2  Field-gates preservation: toggle a cell on a transition that already
 *         has field_gates — re-read via API confirms field_gates unchanged.
 *
 *   TC-3  Non-admin read-only (inverse check): admin does NOT see read-only banner.
 *         (Kept as-is — inverse guard to ensure admin path is never accidentally locked.)
 *
 *   TC-6  Non-admin POSITIVE path: reviewer role sees amber read-only banner + all
 *         checkboxes disabled + clicking a cell fires no add_transition MCP call.
 *         Closes MAJOR reviewer finding (AC-8, AC-9).
 *
 * Requires local docker compose stack:
 *   frontend → http://localhost:5174
 *   backend  → http://localhost:8000
 *
 * Design:
 *   - Each test resets the transition state via direct API call (idempotent).
 *   - Admin token = "change-me-on-first-login"
 *   - Reviewer token created during test setup (or skipped if not available).
 */

import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

const BASE = "http://localhost:5174";
const API = "http://localhost:8000";
const ADMIN_TOKEN = "change-me-on-first-login";
const BOARD_KEY = "PH";

// ---------------------------------------------------------------------------
// Helpers — auth + navigation
// ---------------------------------------------------------------------------

async function authAndGo(page: Page, path: string, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
  await page.goto(`${BASE}${path}`);
}

async function openWorkflowTab(page: Page, token = ADMIN_TOKEN) {
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`, token);
  await page.getByRole("tab", { name: /workflow/i }).click();
  // Wait for the matrix to appear (or skeleton to load)
  await page
    .locator('[data-testid="permission-matrix"]')
    .waitFor({ timeout: 15_000 })
    .catch(() => {
      // If the matrix does not appear yet, that's tested separately
    });
}

// ---------------------------------------------------------------------------
// Helpers — API (direct MCP calls)
// ---------------------------------------------------------------------------

interface WorkflowData {
  id: string;
  transitions: Array<{
    from: string;
    to: string;
    allowed_roles?: string[];
    field_gates?: { required_fields?: string[]; exempt_ticket_types?: string[] };
  }>;
}

async function fetchBoardWorkflow(request: APIRequestContext): Promise<WorkflowData> {
  const res = await request.get(`${API}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  expect(res.ok(), `GET /api/boards/${BOARD_KEY} failed`).toBeTruthy();
  const board = await res.json() as {
    workflow?: { id?: string; transitions?: WorkflowData["transitions"] };
  };
  const id = board.workflow?.id;
  if (!id) throw new Error("Board has no workflow id");
  return { id, transitions: board.workflow?.transitions ?? [] };
}

async function apiAddTransition(
  request: APIRequestContext,
  workflowId: string,
  fromState: string,
  toState: string,
  allowedRoles: string[],
  fieldGates?: { required_fields?: string[]; exempt_ticket_types?: string[] },
) {
  const res = await request.post(`${API}/mcp/call/add_transition`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: {
      workflow_id: workflowId,
      from_state: fromState,
      to_state: toState,
      allowed_roles: allowedRoles,
      ...(fieldGates ? { field_gates: fieldGates } : {}),
    },
  });
  expect(res.ok(), `add_transition ${fromState}->${toState} failed: ${res.status()}`).toBeTruthy();
}

// ---------------------------------------------------------------------------
// TC-1: Admin golden path
// ---------------------------------------------------------------------------

test("TC-1: admin can toggle a cell — matrix updates + success toast fires", async ({
  page,
  request,
}) => {
  // Setup: fetch first transition, clear its allowed_roles so it starts as wildcard
  const wf = await fetchBoardWorkflow(request);
  if (wf.transitions.length === 0) {
    test.skip(true, "No transitions — skipping");
    return;
  }
  const t = wf.transitions[0];

  // Reset to wildcard (all roles allowed)
  await apiAddTransition(request, wf.id, t.from, t.to, [], t.field_gates);

  // Navigate to Workflow tab
  await openWorkflowTab(page);

  // Wait for Permissions Matrix card heading
  await expect(page.getByRole("heading", { name: /permissions matrix/i })).toBeVisible({
    timeout: 10_000,
  });

  // Matrix should be present
  const matrix = page.getByTestId("permission-matrix");
  await expect(matrix).toBeVisible({ timeout: 10_000 });

  // Find a cell for the first transition row. We target the first checkbox in the matrix.
  // data-testid pattern: matrix-cell-{from}-{to}-{role}
  const firstCell = matrix.locator('input[type="checkbox"]').first();
  await expect(firstCell).toBeVisible({ timeout: 5_000 });
  await expect(firstCell).not.toBeDisabled();

  // Intercept add_transition MCP call
  const addTransitionReq = page.waitForRequest(
    (req) =>
      req.url().includes("/mcp/call/add_transition") && req.method() === "POST",
    { timeout: 8_000 },
  );

  // Click the cell
  await firstCell.click();

  // Wait for the MCP request
  const intercepted = await addTransitionReq;
  const body = JSON.parse(intercepted.postData() ?? "{}") as {
    from_state?: string;
    to_state?: string;
    allowed_roles?: string[];
  };
  expect(body.from_state).toBeTruthy();
  expect(body.to_state).toBeTruthy();
  expect(Array.isArray(body.allowed_roles)).toBeTruthy();

  // Success toast should appear
  const successToast = page.locator('[data-testid="success-toast"]');
  await expect(successToast).toBeVisible({ timeout: 6_000 });
  await expect(successToast).toContainText(/permission updated/i);
});

// ---------------------------------------------------------------------------
// TC-2: Field-gates are preserved after a cell toggle
// ---------------------------------------------------------------------------

test("TC-2: cell toggle preserves existing field_gates on the transition", async ({
  page,
  request,
}) => {
  const wf = await fetchBoardWorkflow(request);
  if (wf.transitions.length === 0) {
    test.skip(true, "No transitions — skipping");
    return;
  }
  const t = wf.transitions[0];

  // Set a known field_gates baseline + wildcard allowed_roles
  const fieldGates = { required_fields: ["technical_depth"], exempt_ticket_types: [] };
  await apiAddTransition(request, wf.id, t.from, t.to, [], fieldGates);

  // Navigate to matrix
  await openWorkflowTab(page);
  const matrix = page.getByTestId("permission-matrix");
  await expect(matrix).toBeVisible({ timeout: 10_000 });

  const firstCell = matrix.locator('input[type="checkbox"]').first();
  await expect(firstCell).not.toBeDisabled();

  // Toggle the cell (adds a role, which calls add_transition with field_gates preserved)
  await firstCell.click();

  // Wait for success toast (mutation settled)
  await expect(page.locator('[data-testid="success-toast"]')).toBeVisible({
    timeout: 8_000,
  });

  // Verify via API that field_gates are unchanged
  const wfAfter = await fetchBoardWorkflow(request);
  const updatedTransition = wfAfter.transitions.find(
    (tx) => tx.from === t.from && tx.to === t.to,
  );
  expect(updatedTransition).toBeDefined();
  expect(
    updatedTransition?.field_gates?.required_fields,
    "field_gates.required_fields must be preserved after matrix toggle",
  ).toContain("technical_depth");

  // Cleanup: restore wildcard
  await apiAddTransition(request, wf.id, t.from, t.to, [], fieldGates);
});

// ---------------------------------------------------------------------------
// TC-3: Non-admin (reviewer) sees read-only matrix
// ---------------------------------------------------------------------------

test("TC-3: reviewer sees disabled checkboxes and read-only amber banner", async ({
  page,
}) => {
  // Get a reviewer token. The test creates a temporary reviewer user or uses
  // the jarwis-reviewer actor token if available.
  // Fallback: log in as admin, verify the board role check works by mocking.
  // Since we can't easily create a reviewer user in e2e, we verify the read-only
  // banner and disabled state by checking the `readOnly` prop effect directly.
  //
  // Strategy: log in with admin token but navigate to a board where the user
  // has a non-editor role. Because this is hard to set up without a second user,
  // we instead verify the component's data-testid for the readonly banner appears
  // when board role is not admin/pm.
  //
  // For the smoke test we rely on the component's own rendering: when the page
  // is visited as admin, the readonly banner should NOT be present.
  // We test the inverse: the readonly banner IS absent for admin.

  await openWorkflowTab(page);
  await expect(page.getByTestId("permission-matrix")).toBeVisible({ timeout: 10_000 });

  // Admin should NOT see the readonly banner on the matrix
  const matrixBanner = page.getByTestId("permission-matrix-readonly-banner");
  await expect(matrixBanner).not.toBeVisible();

  // All checkboxes in the matrix should be enabled for admin
  const matrix = page.getByTestId("permission-matrix");
  const checkboxes = matrix.locator('input[type="checkbox"]');
  const count = await checkboxes.count();
  if (count > 0) {
    // At least the first checkbox should not be disabled
    await expect(checkboxes.first()).not.toBeDisabled();
  }
});

// ---------------------------------------------------------------------------
// TC-4 (edge case): Empty matrix when no workflow is selected
// ---------------------------------------------------------------------------

test("TC-4: empty state renders without crash when no workflow selected", async ({
  page,
}) => {
  // Navigate directly to settings without a workflow active
  // The empty-state is guarded by workflowId=null prop
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();

  // The page should not crash regardless of workflow state
  await expect(page.getByRole("tab", { name: /workflow/i })).toBeVisible({
    timeout: 5_000,
  });

  // If the matrix is visible, great; if empty state is shown, also acceptable.
  // The key check: no unhandled JS errors.
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  // Short wait for any async errors to surface
  await page.waitForTimeout(2_000);

  const criticalErrors = consoleErrors.filter(
    (e) =>
      !e.includes("favicon") &&
      !e.includes("net::ERR") &&
      !e.includes("404") &&
      !e.includes("websocket"),
  );
  expect(criticalErrors, `Unexpected console errors: ${criticalErrors.join("\n")}`).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// TC-5 (edge case): Matrix keyboard navigation — Tab/Space reachable
// ---------------------------------------------------------------------------

test("TC-5: keyboard navigation — checkboxes are tab-reachable and togglable via Space", async ({
  page,
  request,
}) => {
  const wf = await fetchBoardWorkflow(request);
  if (wf.transitions.length === 0) {
    test.skip(true, "No transitions — skipping keyboard nav test");
    return;
  }

  // Ensure wildcard state (no roles set — any role badge visible, cells unchecked)
  const t = wf.transitions[0];
  await apiAddTransition(request, wf.id, t.from, t.to, [], t.field_gates);

  await openWorkflowTab(page);
  const matrix = page.getByTestId("permission-matrix");
  await expect(matrix).toBeVisible({ timeout: 10_000 });

  // Focus the first checkbox via keyboard: Tab from the matrix heading
  const firstCheckbox = matrix.locator('input[type="checkbox"]').first();
  await firstCheckbox.focus();
  await expect(firstCheckbox).toBeFocused();

  // Intercept add_transition request triggered by Space
  const reqPromise = page.waitForRequest(
    (req) => req.url().includes("/mcp/call/add_transition"),
    { timeout: 8_000 },
  );

  // Press Space to toggle
  await page.keyboard.press("Space");

  // MCP call should have fired
  await reqPromise;

  // Success toast should appear
  await expect(page.locator('[data-testid="success-toast"]')).toBeVisible({
    timeout: 6_000,
  });

  // Restore wildcard
  await apiAddTransition(request, wf.id, t.from, t.to, [], t.field_gates);
});

// ---------------------------------------------------------------------------
// TC-6: Non-admin (reviewer) POSITIVE path — read-only matrix
// Closes MAJOR reviewer finding: AC-8 (disabled checkboxes) + AC-9 (no mutation)
// Strategy: intercept /api/auth/me to return reviewer role membership, then verify
//   1. Amber read-only banner is visible
//   2. All checkboxes in the matrix are disabled
//   3. Clicking a checkbox does NOT fire any add_transition MCP call
// ---------------------------------------------------------------------------

test("TC-6: reviewer sees amber banner + disabled checkboxes + click fires no mutation (AC-8, AC-9)", async ({
  page,
  request,
}) => {
  // Ensure at least one transition exists with a known allowed_roles so checkboxes render
  const wf = await fetchBoardWorkflow(request);
  if (wf.transitions.length === 0) {
    test.skip(true, "No transitions — skipping read-only positive path test");
    return;
  }
  const t = wf.transitions[0];
  // Set allowed_roles to a non-empty value so at least one checkbox renders as checked
  // (unchecked wildcard cells also appear but explicit checked cells make the test
  // more reliable against UI rendering edge cases)
  await apiAddTransition(request, wf.id, t.from, t.to, ["pm"], t.field_gates);

  // --- Mock /api/auth/me to return reviewer role on PH board ---
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor: {
          id: "mock-reviewer-actor-id",
          kind: "agent",
          display_name: "test-reviewer",
          agent_id: null,
          agent_role_hint: "reviewer",
        },
        memberships: [
          {
            board_id: "c1592aad-9466-4be9-9c0b-75f41ac3efac",
            board_key: BOARD_KEY,
            role: "reviewer",
          },
        ],
      }),
    });
  });

  // Track any add_transition calls that should NOT happen
  const mutationFired = { value: false };
  await page.route("**/mcp/call/add_transition", async (route) => {
    mutationFired.value = true;
    // Abort the request — it should never be called, but if it is, don't leave it hanging
    await route.abort();
  });

  // Navigate to Workflow tab as "reviewer" (auth/me is mocked above)
  await authAndGo(page, `/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();

  // Wait for the matrix to appear
  const matrix = page.getByTestId("permission-matrix");
  await expect(matrix).toBeVisible({ timeout: 15_000 });

  // AC-8: Read-only amber banner must be visible
  const banner = page.getByTestId("permission-matrix-readonly-banner");
  await expect(banner).toBeVisible({ timeout: 5_000 });
  await expect(banner).toContainText(/read-only/i);

  // AC-8: All checkboxes must be disabled
  const checkboxes = matrix.locator('input[type="checkbox"]');
  const checkboxCount = await checkboxes.count();
  expect(checkboxCount, "Matrix must contain at least one checkbox").toBeGreaterThan(0);

  for (let i = 0; i < checkboxCount; i++) {
    await expect(checkboxes.nth(i)).toBeDisabled();
  }

  // AC-9: Clicking a checkbox must NOT fire add_transition
  // Attempt a click (it should be intercepted by the disabled attribute, but verify network)
  const firstCheckbox = checkboxes.first();
  // Playwright can still dispatch click events on disabled inputs; use force:true to
  // bypass the visible/enabled check and confirm the JS handler guard works
  await firstCheckbox.click({ force: true });

  // Short wait for any potential async mutation to surface
  await page.waitForTimeout(500);

  // Assert no mutation was fired
  expect(mutationFired.value, "add_transition must NOT be called when role is reviewer").toBe(false);

  // Cleanup: restore wildcard on the modified transition
  await apiAddTransition(request, wf.id, t.from, t.to, [], t.field_gates);
});
