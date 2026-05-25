/**
 * PH-106 — Workflow state delete with ticket-count guard + cascade transition cleanup.
 *
 * TC-1: State with no tickets → click trash → confirm dialog opens → confirm → toast shows
 *       "State '<name>' deleted" and dialog closes.
 * TC-2: State with tickets > 0 → trash button is disabled, tooltip says
 *       "Cannot delete: tickets exist in this state".
 * TC-3: Last remaining state in workflow → trash button is disabled, tooltip says
 *       "Cannot delete: last state in workflow".
 * TC-4: Cancel button in dialog → dialog closes, no API call made.
 * TC-5: Backend 409/400 guard error → dialog stays open, inline error message visible.
 * TC-6: Cascade toast shows removed transition count when cascade > 0.
 *
 * All TCs use route interception for deterministic, fast test execution without
 * requiring the live board to be in a specific state.
 */
import { test, expect, type Page } from "@playwright/test";

const BASE = "http://localhost:5173";
const ADMIN_TOKEN = "change-me-on-first-login";
const BOARD_KEY = "PH";
const BOARD_ID = "test-board-id-106";
const WORKFLOW_ID = "wf-test-106";

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const STATE_EMPTY = { name: "empty-state", color: "#8b5cf6", is_initial: false, is_terminal: false };
const STATE_WITH_TICKETS = { name: "busy-state", color: "#ef4444", is_initial: true, is_terminal: false };
const STATE_LAST = { name: "only-state", color: "#6b7280", is_initial: true, is_terminal: false };

const WF_TWO_STATES = {
  id: WORKFLOW_ID,
  name: "Test Workflow",
  is_active: true,
  is_default: false,
  states: [STATE_EMPTY, STATE_WITH_TICKETS],
  transitions: [
    { from_state: STATE_EMPTY.name, to_state: STATE_WITH_TICKETS.name, name: "start" },
    { from_state: STATE_WITH_TICKETS.name, to_state: STATE_EMPTY.name, name: "reset" },
  ],
};

const WF_ONE_STATE = {
  id: WORKFLOW_ID,
  name: "Solo Workflow",
  is_active: true,
  is_default: false,
  states: [STATE_LAST],
  transitions: [],
};

function makeBoardFixture(workflow: object) {
  return {
    id: BOARD_ID,
    key: BOARD_KEY,
    name: "Test Board",
    description: "",
    project_type: "web_app",
    roles: {},
    workflow,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

// Tickets fixture: busy-state has 2 tickets, empty-state has 0
const TICKETS_FIXTURE = {
  tickets: [
    { id: "t1", key: "PH-T1", title: "Ticket A", state: STATE_WITH_TICKETS.name, priority: "medium", type: "feature" },
    { id: "t2", key: "PH-T2", title: "Ticket B", state: STATE_WITH_TICKETS.name, priority: "low", type: "feature" },
  ],
  total: 2,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function gotoWorkflowTab(page: Page) {
  await page.goto(BASE);
  await page.evaluate(
    (t) => localStorage.setItem("projecthub.token", t),
    ADMIN_TOKEN,
  );
  await page.goto(`${BASE}/boards/${BOARD_KEY}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
}

function mockBoard(page: Page, workflow: object) {
  const fixture = makeBoardFixture(workflow);
  page.route(`**/api/boards/${BOARD_KEY}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fixture),
    });
  });
  page.route("**/api/boards", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ boards: [fixture] }),
    });
  });
}

function mockMe(page: Page) {
  // Mock /auth/me so the test user appears as an admin on board PH,
  // which makes isWorkflowEditor=true and disabled=false on WorkflowStateList.
  page.route("**/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "user-test-106",
        display_name: "Test Admin",
        email: "test@example.com",
        memberships: [{ board_key: BOARD_KEY, role: "admin" }],
      }),
    });
  });
}

function mockListWorkflows(page: Page, workflows: object[]) {
  return page.route("**/mcp/call/list_workflows", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tool: "list_workflows", result: workflows }),
    });
  });
}

function mockTickets(page: Page) {
  page.route("**/api/tickets**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TICKETS_FIXTURE),
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("PH-106: workflow state delete", () => {
  test("TC-1: happy path — empty state confirms + toast shows", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_TWO_STATES);
    mockTickets(page);
    await mockListWorkflows(page, [WF_TWO_STATES]);

    let deleteCallCount = 0;
    await page.route("**/mcp/call/delete_state", async (route) => {
      deleteCallCount++;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tool: "delete_state",
          result: { deleted: true, state_name: STATE_EMPTY.name, removed_transitions: 0 },
        }),
      });
    });

    // After delete, board returns updated fixture without the deleted state
    let boardCallCount = 0;
    await page.route(`**/api/boards/${BOARD_KEY}`, async (route) => {
      boardCallCount++;
      const wf = boardCallCount <= 1 ? WF_TWO_STATES : { ...WF_TWO_STATES, states: [STATE_WITH_TICKETS], transitions: [] };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeBoardFixture(wf)),
      });
    });

    await gotoWorkflowTab(page);

    // Empty state trash button should be enabled
    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_EMPTY.name}`);
    await expect(trashBtn).toBeVisible();
    await expect(trashBtn).toBeEnabled();

    // Click → dialog opens
    await trashBtn.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Dialog title should contain state name
    const title = dialog.locator("#delete-state-dialog-title");
    await expect(title).toContainText(STATE_EMPTY.name);

    // Confirm
    await dialog.getByTestId("confirm-delete-state-btn").click();

    // Dialog closes
    await expect(dialog).not.toBeVisible();

    // API was called
    expect(deleteCallCount).toBeGreaterThan(0);

    // Toast appears
    const toast = page.getByTestId("success-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText(STATE_EMPTY.name);
    await expect(toast).toContainText("deleted");
  });

  test("TC-2: state with tickets → trash button disabled + tooltip", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_TWO_STATES);
    mockTickets(page);
    await mockListWorkflows(page, [WF_TWO_STATES]);

    await gotoWorkflowTab(page);

    // busy-state has 2 tickets (from TICKETS_FIXTURE)
    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_WITH_TICKETS.name}`);
    await expect(trashBtn).toBeVisible();
    await expect(trashBtn).toBeDisabled();

    const titleAttr = await trashBtn.getAttribute("title");
    expect(titleAttr).toContain("Cannot delete: tickets exist in this state");
  });

  test("TC-3: last state → trash button disabled + tooltip", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_ONE_STATE);
    // No tickets
    page.route("**/api/tickets**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tickets: [], total: 0 }),
      });
    });
    await mockListWorkflows(page, [WF_ONE_STATE]);

    await gotoWorkflowTab(page);

    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_LAST.name}`);
    await expect(trashBtn).toBeVisible();
    await expect(trashBtn).toBeDisabled();

    const titleAttr = await trashBtn.getAttribute("title");
    expect(titleAttr).toContain("Cannot delete: last state in workflow");
  });

  test("TC-4: cancel button → dialog closes, no API call", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_TWO_STATES);
    mockTickets(page);
    await mockListWorkflows(page, [WF_TWO_STATES]);

    let deleteCallCount = 0;
    await page.route("**/mcp/call/delete_state", async (route) => {
      deleteCallCount++;
      await route.fulfill({ status: 200, body: "{}" });
    });

    await gotoWorkflowTab(page);

    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_EMPTY.name}`);
    await trashBtn.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Click İptal (cancel)
    await dialog.getByRole("button", { name: /^İptal$/i }).click();

    // Dialog closes
    await expect(dialog).not.toBeVisible();

    // No API call made
    expect(deleteCallCount).toBe(0);
  });

  test("TC-5: backend guard error → dialog stays open, inline error visible", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_TWO_STATES);
    mockTickets(page);
    await mockListWorkflows(page, [WF_TWO_STATES]);

    // Simulate backend 400 guard error (tickets_exist)
    await page.route("**/mcp/call/delete_state", async (route) => {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error: "state_deletion_blocked",
          reason: "tickets_exist",
          state_name: STATE_EMPTY.name,
          ticket_count: 3,
          message: "Cannot delete state: tickets exist",
        }),
      });
    });

    await gotoWorkflowTab(page);

    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_EMPTY.name}`);
    await expect(trashBtn).toBeEnabled();
    await trashBtn.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog.getByTestId("confirm-delete-state-btn").click();

    // Dialog must stay open after backend error
    await expect(dialog).toBeVisible();

    // Inline error with role="alert" must be visible
    const errorAlert = dialog.locator('[role="alert"]');
    await expect(errorAlert).toBeVisible();
  });

  test("TC-6: cascade toast shows removed transition count", async ({ page }) => {
    mockMe(page);
    mockBoard(page, WF_TWO_STATES);
    mockTickets(page);
    await mockListWorkflows(page, [WF_TWO_STATES]);

    // Backend returns 2 removed transitions (cascade)
    await page.route("**/mcp/call/delete_state", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tool: "delete_state",
          result: { deleted: true, state_name: STATE_EMPTY.name, removed_transitions: 2 },
        }),
      });
    });

    await gotoWorkflowTab(page);

    const trashBtn = page.getByTestId(`delete-state-btn-${STATE_EMPTY.name}`);
    await expect(trashBtn).toBeEnabled();
    await trashBtn.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog.getByTestId("confirm-delete-state-btn").click();
    await expect(dialog).not.toBeVisible();

    // Toast should mention the cascade count
    const toast = page.getByTestId("success-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("2 transitions removed");
  });
});
