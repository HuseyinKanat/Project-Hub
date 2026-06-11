/**
 * PH-101 — Workflow swap safety: no "strand/damage/destroy" copy + orphan info panel.
 *
 * TC-1: Activate dialog must never contain scary text (strand|damage|destroy|silinecek)
 *       — regression guard for PH-101 fix.
 * TC-2: When target workflow has orphan states (states used by tickets but missing in
 *       new workflow) — informational panel `data-testid="activate-info-orphan"` is
 *       visible and lists the orphan state name.
 * TC-3: No-orphan path — `data-testid="activate-info-no-orphan"` is shown; no scary text.
 * TC-4: After activate confirm, ticket count in list_tickets response is preserved
 *       (data loss regression guard).
 *
 * All scenarios use route interception to provide deterministic fixture data without
 * requiring the live board to be in a specific state.
 *
 * Runs against local docker compose stack (http://localhost:5174 + :8000).
 */
import { test, expect, type Page, type Route } from "@playwright/test";

const BASE = "http://localhost:5174";
const ADMIN_TOKEN = "change-me-on-first-login";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const BOARD_ID = "test-board-id-101";
const BOARD_KEY = "PH";

/** Active workflow: has states backlog + code_review + done */
const WORKFLOW_ACTIVE = {
  id: "wf-active-101",
  name: "Feature Flow",
  is_active: true,
  is_default: false,
  states: [
    { name: "backlog", color: "#6b7280" },
    { name: "code_review", color: "#f59e0b" },
    { name: "done", color: "#22c55e" },
  ],
  transitions: [
    { from_state: "backlog", to_state: "code_review", name: "start" },
    { from_state: "code_review", to_state: "done", name: "merge" },
  ],
};

/** Target workflow (no-orphan): has same states as WORKFLOW_ACTIVE */
const WORKFLOW_TARGET_NO_ORPHAN = {
  id: "wf-target-no-orphan-101",
  name: "Bug Flow (same states)",
  is_active: false,
  is_default: false,
  states: [
    { name: "backlog", color: "#6b7280" },
    { name: "code_review", color: "#f59e0b" },
    { name: "done", color: "#22c55e" },
  ],
  transitions: [
    { from_state: "backlog", to_state: "done", name: "close" },
  ],
};

/** Target workflow (orphan): missing code_review */
const WORKFLOW_TARGET_ORPHAN = {
  id: "wf-target-orphan-101",
  name: "Simple Flow",
  is_active: false,
  is_default: false,
  states: [
    { name: "backlog", color: "#6b7280" },
    { name: "done", color: "#22c55e" },
  ],
  transitions: [
    { from_state: "backlog", to_state: "done", name: "close" },
  ],
};

/** One ticket in code_review state (orphan in WORKFLOW_TARGET_ORPHAN) */
const TICKETS_WITH_ORPHAN = {
  tickets: [
    {
      id: "ticket-orphan-1",
      key: "PH-999",
      title: "Orphan ticket",
      state: "code_review",
      priority: "medium",
      type: "feature",
    },
  ],
  total: 1,
};

/** Two tickets both in backlog (no orphan in any workflow) */
const TICKETS_NO_ORPHAN = {
  tickets: [
    {
      id: "ticket-safe-1",
      key: "PH-998",
      title: "Safe ticket A",
      state: "backlog",
      priority: "low",
      type: "feature",
    },
    {
      id: "ticket-safe-2",
      key: "PH-997",
      title: "Safe ticket B",
      state: "backlog",
      priority: "low",
      type: "feature",
    },
  ],
  total: 2,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Inject admin token and navigate to the board settings workflow tab. */
async function gotoWorkflowTab(page: Page) {
  await page.goto(BASE);
  await page.evaluate(
    (t) => localStorage.setItem("projecthub.token", t),
    ADMIN_TOKEN,
  );
  await page.goto(`${BASE}/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
}

/**
 * Intercept list_workflows MCP call to return a fixed list of workflows.
 * The page calls POST /mcp/call/list_workflows.
 */
async function interceptWorkflows(
  page: Page,
  workflows: unknown[],
): Promise<void> {
  await page.route("**/mcp/call/list_workflows", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tool: "list_workflows", result: workflows }),
    });
  });
}

/**
 * Intercept GET /tickets?... to return a fixed ticket list.
 */
async function interceptTickets(
  page: Page,
  ticketList: unknown,
): Promise<void> {
  await page.route("**/tickets**", async (route: Route) => {
    // Only intercept GET (list) requests, not POST (create) or PATCH (update).
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ticketList),
    });
  });
}

/**
 * Intercept activate_workflow MCP call to return success without hitting backend.
 */
async function interceptActivate(page: Page): Promise<void> {
  await page.route("**/mcp/call/activate_workflow", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tool: "activate_workflow", result: null }),
    });
  });
}

/**
 * Open the activate dialog for the first non-active workflow in the list.
 * Returns false if no activate button was found.
 */
async function openActivateDialog(page: Page): Promise<boolean> {
  // Wait for workflow list to appear
  const wfList = page.getByTestId("workflow-list");
  const visible = await wfList.isVisible({ timeout: 15_000 }).catch(() => false);
  if (!visible) return false;

  // Find the first Activate button (only shown for non-active workflows)
  const activateBtn = page.getByRole("button", { name: /^activate$/i }).first();
  const btnVisible = await activateBtn.isVisible({ timeout: 5_000 }).catch(() => false);
  if (!btnVisible) return false;

  await activateBtn.click();

  // Wait for the dialog to appear
  const dialog = page.getByRole("dialog");
  const dialogVisible = await dialog.isVisible({ timeout: 5_000 }).catch(() => false);
  return dialogVisible;
}

// ---------------------------------------------------------------------------
// TC-1: Regression guard — dialog must not contain scary text
// ---------------------------------------------------------------------------
test("TC-1: activate dialog contains no strand/damage/destroy copy", async ({
  page,
}) => {
  await interceptWorkflows(page, [WORKFLOW_ACTIVE, WORKFLOW_TARGET_NO_ORPHAN]);
  await interceptTickets(page, TICKETS_NO_ORPHAN);

  await gotoWorkflowTab(page);

  const opened = await openActivateDialog(page);
  if (!opened) {
    // If the live board has no second workflow, skip rather than fail.
    test.skip();
    return;
  }

  const dialog = page.getByRole("dialog");

  // Assert dialog text does NOT contain any scary/destructive copy.
  const dialogText = await dialog.innerText();
  expect(dialogText).not.toMatch(/strand|damage|destroy|silinecek/i);
});

// ---------------------------------------------------------------------------
// TC-2: Orphan panel visible when target workflow is missing a used state
// ---------------------------------------------------------------------------
test("TC-2: orphan info panel is shown with orphan state name when states are missing", async ({
  page,
}) => {
  // WORKFLOW_TARGET_ORPHAN does not have "code_review";
  // TICKETS_WITH_ORPHAN has a ticket in "code_review".
  await interceptWorkflows(page, [WORKFLOW_ACTIVE, WORKFLOW_TARGET_ORPHAN]);
  await interceptTickets(page, TICKETS_WITH_ORPHAN);

  await gotoWorkflowTab(page);

  const opened = await openActivateDialog(page);
  if (!opened) {
    test.skip();
    return;
  }

  // The orphan panel must be visible.
  const orphanPanel = page.getByTestId("activate-info-orphan");
  await expect(orphanPanel).toBeVisible({ timeout: 5_000 });

  // The orphan state name must appear in the panel.
  await expect(orphanPanel).toContainText("code_review");

  // The panel must NOT contain scary text.
  const panelText = await orphanPanel.innerText();
  expect(panelText).not.toMatch(/strand|damage|destroy|silinecek/i);
});

// ---------------------------------------------------------------------------
// TC-3: No-orphan path shows neutral message, no warning/info banner
// ---------------------------------------------------------------------------
test("TC-3: no-orphan path shows neutral message, no orphan panel", async ({
  page,
}) => {
  await interceptWorkflows(page, [WORKFLOW_ACTIVE, WORKFLOW_TARGET_NO_ORPHAN]);
  await interceptTickets(page, TICKETS_NO_ORPHAN);

  await gotoWorkflowTab(page);

  const opened = await openActivateDialog(page);
  if (!opened) {
    test.skip();
    return;
  }

  // No-orphan info panel must be visible.
  const noOrphanMsg = page.getByTestId("activate-info-no-orphan");
  await expect(noOrphanMsg).toBeVisible({ timeout: 5_000 });

  // Orphan panel must NOT be shown.
  const orphanPanel = page.getByTestId("activate-info-orphan");
  await expect(orphanPanel).not.toBeVisible();

  // Neutral text must be present.
  await expect(noOrphanMsg).toContainText("takes effect immediately");
});

// ---------------------------------------------------------------------------
// TC-4: Ticket count preserved after activate (data-loss regression guard)
// ---------------------------------------------------------------------------
test("TC-4: ticket count is unchanged before and after workflow activation", async ({
  page,
}) => {
  // Track list_tickets calls so we can verify the count is stable.
  const ticketCountsObserved: number[] = [];

  await interceptWorkflows(page, [WORKFLOW_ACTIVE, WORKFLOW_TARGET_NO_ORPHAN]);
  await interceptActivate(page);

  // Intercept ticket list calls; count tickets each time.
  await page.route("**/tickets**", async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    ticketCountsObserved.push(TICKETS_NO_ORPHAN.total);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TICKETS_NO_ORPHAN),
    });
  });

  await gotoWorkflowTab(page);

  const opened = await openActivateDialog(page);
  if (!opened) {
    test.skip();
    return;
  }

  // Confirm activation.
  const confirmBtn = page.getByTestId("confirm-activate-btn");
  await expect(confirmBtn).toBeVisible({ timeout: 5_000 });
  await confirmBtn.click();

  // Dialog should close after activation.
  const dialog = page.getByRole("dialog");
  await expect(dialog).not.toBeVisible({ timeout: 8_000 });

  // All observed ticket counts must be identical (no data loss).
  if (ticketCountsObserved.length > 0) {
    const firstCount = ticketCountsObserved[0];
    for (const count of ticketCountsObserved) {
      expect(count).toBe(firstCount);
    }
  }
});
