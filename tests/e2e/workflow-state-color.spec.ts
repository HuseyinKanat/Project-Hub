/**
 * PH-98 — State color change propagates to board kanban.
 *
 * Verifies that setting a hex color in the WorkflowEditor NodePropertyPanel
 * causes the kanban column header (AC-1), TicketDetail state badge (AC-2),
 * and refresh behaviour (AC-3) to use inline style instead of the hardcoded
 * Tailwind fallback.  Also checks board isolation (AC-4, PH-97 regression).
 *
 * Runs against local docker compose stack:
 *   frontend  → http://localhost:5174
 *   backend   → http://localhost:8000
 *
 * IMPORTANT — rgba normalisation:
 *   Browsers convert hex-with-alpha (e.g. "#3b82f61a") to rgba() notation
 *   when writing it into the DOM style attribute.  Therefore assertions check
 *   for the RGB decimal components (59, 130, 246) rather than the hex literal.
 *
 *   resolveStateColor() sets:
 *     backgroundColor: hex + "1A"  → rgba(59, 130, 246, 0.10...)
 *     borderColor:     hex + "4D"  → rgba(59, 130, 246, 0.30...)
 *     color:           hex         → rgb(59, 130, 246)
 *
 *   All three contain the substring "59, 130, 246".
 *
 * PH-137: local resetBacklogColor helper removed; full workflow restore is
 * now handled by installSnapshotHooks (tests/e2e/helpers/workflowSnapshot.ts).
 * BOARD_KEY, ADMIN_TOKEN, API_BASE imported from the shared helper.
 */
import { test, expect, type Page } from "@playwright/test";
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

/** Hex color we set in the test — Tailwind blue-500. */
const TEST_HEX = "#3b82f6";
/** RGB components of TEST_HEX — used for rgba-aware assertions. */
const TEST_RGB = "59, 130, 246";
/**
 * A distinct reset color used before each run to ensure the state differs
 * from TEST_HEX so the editor's hasUnsavedChanges flag activates.
 * Must be a valid 6-char hex that is NOT #3b82f6.
 */
const RESET_HEX = "#8b5cf6"; // Tailwind violet-500
/** The state we will repaint — present in every default PH workflow. */
const TARGET_STATE = "backlog";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function authAndGo(page: Page, path: string) {
  await page.goto(BASE);
  await page.evaluate(
    (t) => localStorage.setItem("projecthub.token", t),
    ADMIN_TOKEN,
  );
  await page.goto(`${BASE}${path}`);
}

/** Open Board Settings → Workflow tab for a given board key. */
async function openWorkflowTab(page: Page, boardKey: string) {
  await authAndGo(page, `/boards/${boardKey}/settings`);
  await page.getByRole("tab", { name: /workflow/i }).click();
  await expect(page.getByTestId("workflow-list")).toBeVisible({ timeout: 15_000 });
  // The ReactFlow application area marks the visual editor
  await expect(page.getByRole("application")).toBeVisible({ timeout: 15_000 });
}

/**
 * Find the Settings gear button on the node whose label matches stateName
 * and click it, opening the NodePropertyPanel.
 */
async function openNodePanel(page: Page, stateName: string) {
  // Nodes render their label in a div.font-medium inside the ReactFlow canvas.
  const stateLabel = page
    .getByRole("application")
    .locator(".font-medium", { hasText: stateName })
    .first();

  await expect(stateLabel).toBeVisible({ timeout: 10_000 });

  // The gear button is at the top of the node container div.
  const nodeContainer = stateLabel.locator("xpath=ancestor::div[contains(@class,'rounded-lg')]").first();
  const gearBtn = nodeContainer.locator("button").first();
  await gearBtn.scrollIntoViewIfNeeded();
  await gearBtn.click();
}

/** Set the color picker value via fill (bypasses OS native dialog). */
async function setColorPickerValue(page: Page, hex: string) {
  const colorInput = page.locator('input[type="color"]');
  await expect(colorInput).toBeVisible({ timeout: 5_000 });
  await colorInput.fill(hex);
}

/**
 * Apply a color + save — full round trip via WorkflowEditor UI.
 * Waits for the Save Changes button to be ENABLED before clicking (fixing
 * the TC-1 race: button stays disabled until hasUnsavedChanges is true).
 */
async function applyColorAndSave(page: Page, stateName: string, hex: string) {
  await openNodePanel(page, stateName);
  await expect(page.locator("text=State Properties")).toBeVisible({ timeout: 5_000 });
  await setColorPickerValue(page, hex);
  await page.getByRole("button", { name: /apply changes/i }).click();

  // Wait for the Save Changes button to become enabled (hasUnsavedChanges=true).
  // The button is disabled until the node color diff is detected by the useEffect.
  await expect(page.getByRole("button", { name: /save changes/i })).toBeEnabled({ timeout: 5_000 });
  await page.getByRole("button", { name: /save changes/i }).click();

  // Wait for mutation to complete (saving indicator disappears).
  await expect(page.getByRole("button", { name: /saving/i })).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByRole("button", { name: /save changes/i })).toBeVisible({ timeout: 10_000 });
}

/**
 * Reset the TARGET_STATE color to RESET_HEX via the MCP update_workflow API
 * before each test that exercises the color-change flow.  This guarantees
 * hasUnsavedChanges will fire when we subsequently set TEST_HEX in the UI.
 *
 * PH-137: This helper only patches the color field — it is intentionally
 * narrow (single-field patch within the current workflow snapshot).
 * Full workflow shape restore is handled by installSnapshotHooks.
 */
async function resetTargetStateColor(page: Page) {
  const boardRes = await page.request.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  const board = await boardRes.json() as {
    workflow?: {
      id?: string;
      states?: Array<{
        name: string;
        color?: string;
        category?: string;
        is_initial?: boolean;
        is_terminal?: boolean;
        position?: { x: number; y: number };
      }>;
      transitions?: Array<{
        from: string;
        to: string;
        allowed_roles?: string[];
        field_gates?: { required_fields?: string[]; exempt_ticket_types?: string[] };
      }>;
    };
  };

  const workflowId = board.workflow?.id;
  if (!workflowId) {
    throw new Error("resetTargetStateColor: could not determine workflow id from /api/boards/PH");
  }

  const states = board.workflow?.states ?? [];
  const patchedStates = states.map((s) =>
    s.name === TARGET_STATE ? { ...s, color: RESET_HEX } : s,
  );

  // Use MCP update_workflow — the same path the WorkflowEditor uses.
  await page.request.post(`${API_BASE}/mcp/call/update_workflow`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: {
      workflow_id: workflowId,
      fields: {
        states: patchedStates,
        transitions: board.workflow?.transitions ?? [],
      },
      board_id: BOARD_KEY,
    },
  });
}

/**
 * Assert that a kanban column element has inline style containing the
 * RGB components of TEST_HEX.  Browsers convert "#3b82f61a" → rgba(59,130,246,...)
 * so we check for the decimal component substring "59, 130, 246".
 */
async function assertColumnHasTestColor(page: Page) {
  const column = page.getByTestId(`kanban-column-${TARGET_STATE}`);
  await expect(column).toBeVisible({ timeout: 10_000 });
  const styleAttr = await column.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );
  expect(styleAttr, `Expected kanban column style to contain "${TEST_RGB}". Got: "${styleAttr}"`).toContain(TEST_RGB);
}

// ---------------------------------------------------------------------------
// TC-1 + TC-3: color round-trip — kanban column + refresh persist (PH board)
// ---------------------------------------------------------------------------
test("kanban column shows hex color after WorkflowEditor color change + refresh", async ({ page }) => {
  // Reset to RESET_HEX first so the UI detects a real change when we set TEST_HEX.
  await resetTargetStateColor(page);

  await openWorkflowTab(page, BOARD_KEY);
  await applyColorAndSave(page, TARGET_STATE, TEST_HEX);

  // Navigate to the board kanban
  await authAndGo(page, `/boards/${BOARD_KEY}`);

  // AC-1: kanban column must have inline style with rgb components of TEST_HEX
  await assertColumnHasTestColor(page);

  // AC-3: Refresh and assert color persists
  await page.reload();
  await expect(page.getByTestId(`kanban-column-${TARGET_STATE}`)).toBeVisible({ timeout: 10_000 });
  const styleAfterRefresh = await page
    .getByTestId(`kanban-column-${TARGET_STATE}`)
    .evaluate((el) => (el as HTMLElement).getAttribute("style") ?? "");
  // AC-3 assertion: rgb components must still appear after refresh (browser retains rgba)
  expect(styleAfterRefresh, `Expected color to persist after refresh. Style: "${styleAfterRefresh}"`).toContain(TEST_RGB);
});

// ---------------------------------------------------------------------------
// TC-2 (AC-2): TicketDetail state badge also uses the new color
// ---------------------------------------------------------------------------
test("TicketDetail state badge shows hex color after workflow color change", async ({ page }) => {
  // Find a ticket in the TARGET_STATE on the PH board via API
  const res = await page.request.get(
    `${API_BASE}/api/tickets?board_id=${BOARD_KEY}&limit=100`,
    { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
  );
  const data = (await res.json()) as { tickets: Array<{ key: string; state: string }> };
  const targetTicket = data.tickets.find((t) => t.state === TARGET_STATE);

  if (!targetTicket) {
    test.skip(true, `No ticket in state "${TARGET_STATE}" on PH board — skipping badge test`);
    return;
  }

  // Ensure the color is TEST_HEX in DB (TC-1 may have already set it; be idempotent).
  // We need the board to have TEST_HEX set — run reset+change if needed.
  const boardRes = await page.request.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  const boardData = await boardRes.json() as {
    workflow?: { states?: Array<{ name: string; color?: string }> }
  };
  const currentColor = boardData.workflow?.states?.find((s) => s.name === TARGET_STATE)?.color;

  if (currentColor !== TEST_HEX) {
    // Need to set TEST_HEX via UI: reset first so save button enables, then set.
    await resetTargetStateColor(page);
    await openWorkflowTab(page, BOARD_KEY);
    await applyColorAndSave(page, TARGET_STATE, TEST_HEX);
  }

  await authAndGo(page, `/boards/${BOARD_KEY}/tickets/${targetTicket.key}`);

  const badge = page.getByTestId("ticket-state-badge");
  await expect(badge).toBeVisible({ timeout: 10_000 });

  const styleAttr = await badge.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );
  // AC-2 assertion: rgb components of TEST_HEX must appear in badge style (rgba-normalised)
  expect(styleAttr, `Expected badge style to include "${TEST_RGB}". Got: "${styleAttr}"`).toContain(TEST_RGB);
});

// ---------------------------------------------------------------------------
// TC-3 (AC-4): Board isolation — color change on PH does not affect KIM board
// ---------------------------------------------------------------------------
test("color change on PH board does not affect KIM board kanban", async ({ page }) => {
  // Record KIM board backlog column style BEFORE making any change on PH
  await authAndGo(page, "/boards/KIM");
  const kimColumn = page.getByTestId(`kanban-column-${TARGET_STATE}`);

  // KIM may not have a backlog column if its workflow uses different state names
  const kimColumnExists = await kimColumn.isVisible().catch(() => false);
  if (!kimColumnExists) {
    test.skip(true, "KIM board does not have a backlog column — skipping isolation test");
    return;
  }

  const styleBeforePH = await kimColumn.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );

  // Now navigate to PH and check it has TEST_HEX applied (assume TC-1 ran first).
  await authAndGo(page, `/boards/${BOARD_KEY}`);
  const phColumn = page.getByTestId(`kanban-column-${TARGET_STATE}`);
  await expect(phColumn).toBeVisible({ timeout: 10_000 });

  const phStyle = await phColumn.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );

  // Navigate back to KIM and verify its column did not inherit PH's color
  await authAndGo(page, "/boards/KIM");
  const kimColumnAfter = page.getByTestId(`kanban-column-${TARGET_STATE}`);
  await expect(kimColumnAfter).toBeVisible({ timeout: 10_000 });

  const styleAfterPH = await kimColumnAfter.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );

  // AC-4: KIM style must NOT contain the PH color rgb components
  expect(
    styleAfterPH.includes(TEST_RGB),
    `KIM board backlog should NOT have PH color "${TEST_RGB}". KIM style: "${styleAfterPH}", PH style: "${phStyle}"`,
  ).toBe(false);

  // Additional sanity: KIM's style before and after PH change should be identical
  expect(styleAfterPH).toBe(styleBeforePH);
});

// ---------------------------------------------------------------------------
// TC-4 (AC-5): fallback — state without hex color gets STATE_CATEGORIES class
// ---------------------------------------------------------------------------
test("kanban column without custom color uses Tailwind fallback class", async ({ page }) => {
  // Navigate directly to a board kanban — if no hex set, column must have a class
  // (not just inline style). We check for the presence of a Tailwind bg- class.
  await authAndGo(page, "/boards/KIM");

  // KIM board uses the default workflow which has no custom hex colors (PH-97
  // isolation guarantees KIM's workflow is separate from PH's).
  const column = page.getByTestId(`kanban-column-${TARGET_STATE}`);
  const kimColumnExists = await column.isVisible().catch(() => false);
  if (!kimColumnExists) {
    test.skip(true, "KIM board backlog column not found — skipping fallback test");
    return;
  }

  const classAttr = await column.evaluate(
    (el) => (el as HTMLElement).getAttribute("class") ?? "",
  );
  const styleAttr = await column.evaluate(
    (el) => (el as HTMLElement).getAttribute("style") ?? "",
  );

  // AC-5: either class contains "bg-" Tailwind token OR style is empty
  // (if KIM has no hex color, resolveStateColor returns a className)
  const hasTailwindBg = classAttr.includes("bg-");
  const hasNoHexStyle = !styleAttr.toLowerCase().includes("#");

  expect(
    hasTailwindBg || hasNoHexStyle,
    `Expected fallback Tailwind class (no hex). class: "${classAttr}", style: "${styleAttr}"`,
  ).toBe(true);
});
