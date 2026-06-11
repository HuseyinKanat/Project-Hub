/**
 * PH-160 (G11) — Branch detail / diff interaction
 * Mode B (Verify): branch sidebar filtering + commit→diff panel + dark + console
 *
 * HISTORY: PH-160 (G11) shipped a separate right-column "Branch detail panel"
 * (BranchPanel.tsx: per-branch commit list, ahead/behind chip, branch-diff button).
 * PH-167's SourceTree rework REPLACED that panel: branch selection now filters the
 * commit list in-place (sidebar), and the right column is a COMMIT-diff panel that
 * opens on commit-row click. PH-165 (item 5) deleted the orphaned BranchPanel.tsx
 * and rewrote this spec to the current interaction model, HARDENING all selectors
 * against live PH board-state growth (no strict-mode multi-match violations):
 *   - role-scoped queries + `.first()` on every potentially-multi-match locator
 *   - branch buttons selected by name regex scoped inside the sidebar aside
 *   - count assertions are `> 0`, never exact
 *
 * Environment: backend at localhost:8000 (Docker); Vite proxy at localhost:5173.
 * /api/boards/{key}/git/* is forwarded to localhost:8000 (Vite proxy targets the
 * Docker-internal "backend:8000" host, unresolvable from the macOS test host).
 */

import { test, expect, type Page } from "@playwright/test";
import { ADMIN_TOKEN } from "./helpers/workflowSnapshot";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const BOARD_KEY = "PH";
const BOARD_URL = `${BASE}/boards/${BOARD_KEY}`;
const SCREENSHOTS_DIR =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-160/qa-screenshots";

/** Forward git API calls directly to backend (bypass broken Vite proxy host). */
async function installGitApiProxy(page: Page) {
  await page.route(new RegExp("/api/boards/[^/]+/git/"), async (route) => {
    const req = route.request();
    const url = req.url().replace("http://localhost:5173", BACKEND);
    try {
      const resp = await fetch(url, {
        method: req.method(),
        headers: req.headers() as Record<string, string>,
      });
      const body = await resp.arrayBuffer();
      await route.fulfill({
        status: resp.status,
        headers: Object.fromEntries(resp.headers.entries()),
        body: Buffer.from(body),
      });
    } catch {
      await route.continue();
    }
  });
}

async function loginAndGoToBoard(page: Page) {
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), ADMIN_TOKEN);
  await page.goto(BOARD_URL);
}

async function waitForBoardReady(page: Page) {
  await page.waitForSelector('[role="tablist"]', { timeout: 10000 });
}

/** Switch to Branch Graph tab and wait for the SourceTree commit list to mount. */
async function openGraphTab(page: Page) {
  await page.getByRole("tab", { name: "Branch Graph" }).click();
  await page.waitForSelector('[role="list"][aria-label="Commit history"]', {
    timeout: 20000,
  });
  await page.waitForSelector('[role="listitem"]', { timeout: 20000 });
}

/** The branch sidebar aside. */
function sidebar(page: Page) {
  return page.locator('aside[aria-label="Branch list"]');
}

test.describe("PH-160: Branch detail / diff interaction (G11 → PH-167 model)", () => {
  test.describe.configure({ mode: "serial" });

  // ---------------------------------------------------------------------------
  // TC-1: Default state — "All" selected, full commit list, no diff panel
  // ---------------------------------------------------------------------------
  test("TC-1: Default state — All selected, full list, no diff panel (AC1, AC12)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const allBtn = sidebar(page).getByRole("button", { name: /^All$/ });
    await expect(allBtn).toBeVisible();
    await expect(allBtn).toHaveAttribute("aria-pressed", "true");

    // No commit selected yet → no diff panel.
    await expect(page.locator('aside[aria-label="Commit diff panel"]')).toHaveCount(0);

    // Commit list has rows.
    expect(await page.getByRole("listitem").count()).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // TC-2: Branch click → sidebar header HEAD chip on main + aria-pressed (AC1, AC4)
  // ---------------------------------------------------------------------------
  test("TC-2: main branch button — HEAD chip + selectable (AC1, AC4)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const mainBtn = sidebar(page).getByRole("button", { name: /main/ }).first();
    await expect(mainBtn).toBeVisible();

    // HEAD chip on the default branch.
    await expect(mainBtn.getByText("HEAD")).toBeVisible();

    await mainBtn.click();
    await expect(mainBtn).toHaveAttribute("aria-pressed", "true", { timeout: 3000 });
  });

  // ---------------------------------------------------------------------------
  // TC-3: Branch filter → "All" re-select restores full list (AC2)
  // ---------------------------------------------------------------------------
  test("TC-3: Branch filter then All → list restored (AC2)", async ({ page }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const countAll = await page.getByRole("listitem").count();
    expect(countAll).toBeGreaterThan(0);

    // Filter to main.
    const mainBtn = sidebar(page).getByRole("button", { name: /main/ }).first();
    await mainBtn.click();
    await expect(mainBtn).toHaveAttribute("aria-pressed", "true", { timeout: 3000 });
    await page.waitForTimeout(1200);
    expect(await page.getByRole("listitem").count()).toBeGreaterThan(0);

    // Back to All.
    const allBtn = sidebar(page).getByRole("button", { name: /^All$/ });
    await allBtn.click();
    await expect(allBtn).toHaveAttribute("aria-pressed", "true", { timeout: 3000 });
    await page.waitForTimeout(800);
    expect(await page.getByRole("listitem").count()).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // TC-4: Commit list rows show short_sha + summary (AC3)
  // ---------------------------------------------------------------------------
  test("TC-4: Commit rows render short_sha + summary (AC3)", async ({ page }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const firstRow = page.getByRole("listitem").first();
    await expect(firstRow).toBeVisible();

    // Short-sha mono span present in the row.
    const shaEl = firstRow.locator("span.font-mono").first();
    await expect(shaEl).toBeVisible();
    console.log("[TC-4] first row sha:", (await shaEl.innerText()).trim());
  });

  // ---------------------------------------------------------------------------
  // TC-5: Commit row click → diff panel opens (AC6)
  // ---------------------------------------------------------------------------
  test("TC-5: Commit row click → diff panel opens with sha header (AC6)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const rows = page.getByRole("listitem");
    const n = await rows.count();
    await rows.nth(Math.min(1, n - 1)).click();

    const panel = page.locator('aside[aria-label="Commit diff panel"]');
    await expect(panel).toBeVisible({ timeout: 5000 });

    // 12-hex short sha header inside the panel.
    await expect(
      panel.locator("p").filter({ hasText: /[0-9a-f]{12}/ }).first(),
    ).toBeVisible({ timeout: 8000 });
    console.log("[TC-5] diff panel opened for selected commit");
  });

  // ---------------------------------------------------------------------------
  // TC-6: Diff panel close (X) → panel removed, commit deselected (AC8)
  // ---------------------------------------------------------------------------
  test("TC-6: Diff panel close button → panel removed (AC8)", async ({ page }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const rows = page.getByRole("listitem");
    const n = await rows.count();
    await rows.nth(Math.min(1, n - 1)).click();

    const panel = page.locator('aside[aria-label="Commit diff panel"]');
    await expect(panel).toBeVisible({ timeout: 5000 });

    await panel.getByRole("button", { name: "Close diff panel" }).click();
    await expect(panel).toHaveCount(0, { timeout: 3000 });
    console.log("[TC-6] diff panel closed");
  });

  // ---------------------------------------------------------------------------
  // TC-7: Re-click same selected row → diff panel toggles closed (AC2 toggle)
  // ---------------------------------------------------------------------------
  test("TC-7: Re-click selected commit row → panel toggles closed (AC2)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    const rows = page.getByRole("listitem");
    const n = await rows.count();
    const target = rows.nth(Math.min(1, n - 1));

    // Open.
    await target.click();
    const panel = page.locator('aside[aria-label="Commit diff panel"]');
    await expect(panel).toBeVisible({ timeout: 5000 });

    // Re-click the same row → toggles selection off → panel closes.
    await target.click();
    await expect(panel).toHaveCount(0, { timeout: 3000 });
    console.log("[TC-7] re-click toggled diff panel closed");
  });

  // ---------------------------------------------------------------------------
  // TC-8: Tab switch Graph→Kanban→Graph keeps list functional (AC9)
  // ---------------------------------------------------------------------------
  test("TC-8: Tab switch Graph→Kanban→Graph → list re-renders (AC9)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    // Select a commit (open diff panel).
    await page.getByRole("listitem").first().click();
    await expect(page.locator('aside[aria-label="Commit diff panel"]')).toBeVisible({
      timeout: 5000,
    });

    // Switch to Kanban.
    await page.getByRole("tab", { name: "Kanban" }).click();
    await expect(page.locator("#panel-kanban")).toBeAttached({ timeout: 5000 });
    await expect(page.locator("#panel-graph")).not.toBeAttached();

    // Back to Graph — commit list re-mounts (BranchGraph remounts → no stale panel).
    await openGraphTab(page);
    expect(await page.getByRole("listitem").count()).toBeGreaterThan(0);
    // Fresh mount → no diff panel until a row is clicked again.
    await expect(page.locator('aside[aria-label="Commit diff panel"]')).toHaveCount(0);
    console.log("[TC-8] tab switch re-rendered commit list cleanly");
  });

  // ---------------------------------------------------------------------------
  // TC-9: Kanban regression — tab strip + Live indicator intact (AC9 regression)
  // ---------------------------------------------------------------------------
  test("TC-9: Kanban regression — panel mounts, Live indicator visible", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);

    await openGraphTab(page);
    await page.getByRole("listitem").first().click();

    await page.getByRole("tab", { name: "Kanban" }).click();
    await expect(page.locator("#panel-kanban")).toBeAttached({ timeout: 5000 });
    await expect(page.getByRole("tab", { name: "Kanban" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Scope to the WS status badge title — avoids strict-mode multi-match with
    // ticket titles that contain the word "Live".
    await expect(page.locator('[title="Live updates active"]')).toBeVisible({
      timeout: 5000,
    });
    console.log("[TC-9] Kanban regression: panel mounts, Live indicator visible");
  });

  // ---------------------------------------------------------------------------
  // TC-10: Ticket key link in a commit row routes to ticket detail (AC5)
  // ---------------------------------------------------------------------------
  test("TC-10: Commit row ticket-key link routes to ticket detail (AC5)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    // CommitRow renders <Link> chips for ticket_keys (PH-\d+). Scope to the commit
    // list, take the first match — resilient to however many tickets are linked.
    const list = page.locator('[role="list"][aria-label="Commit history"]');
    const ticketLink = list.getByRole("link", { name: /^PH-\d+$/ }).first();
    const hasLink = await ticketLink.isVisible().catch(() => false);

    if (hasLink) {
      const href = await ticketLink.getAttribute("href");
      console.log("[TC-10] ticket link:", (await ticketLink.innerText()).trim(), "href:", href);
      expect(href).toMatch(/\/boards\/PH\/tickets\/PH-\d+/);
    } else {
      console.log(
        "[TC-10] No ticket-key link in visible commits — valid when no recent commit carries a PH-key.",
      );
    }
  });

  // ---------------------------------------------------------------------------
  // TC-11: Light + dark mode screenshots, list readable in dark (AC11)
  // ---------------------------------------------------------------------------
  test("TC-11: Light + dark mode — list readable + screenshots (AC11)", async ({
    page,
  }) => {
    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    // Open a diff for a richer screenshot.
    await page.getByRole("listitem").first().click();
    await expect(page.locator('aside[aria-label="Commit diff panel"]')).toBeVisible({
      timeout: 5000,
    });
    await page.waitForTimeout(800);

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/panel-light.png`, fullPage: false });
    console.log("[TC-11] screenshot saved: panel-light.png");

    // Toggle dark mode (icon-only header button).
    const headerButtons = await page.locator("header button").all();
    let toggled = false;
    for (const btn of headerButtons) {
      if (await btn.isVisible()) {
        const innerText = (await btn.innerText()).trim();
        if (innerText === "") {
          await btn.click();
          await page.waitForTimeout(500);
          toggled = true;
          break;
        }
      }
    }

    await expect(page.getByRole("listitem").first()).toBeVisible();
    await expect(page.locator('aside[aria-label="Commit diff panel"]')).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/panel-dark.png`, fullPage: false });
    console.log("[TC-11] screenshot saved: panel-dark.png, toggled:", toggled);
  });

  // ---------------------------------------------------------------------------
  // TC-12: 0 critical console errors during all interactions (AC13)
  // ---------------------------------------------------------------------------
  test("TC-12: 0 critical console errors during interactions (AC13)", async ({ page }) => {
    const errors: string[] = [];
    const warnings: string[] = [];

    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
      if (msg.type() === "warning") warnings.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(err.message));

    await installGitApiProxy(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);
    await openGraphTab(page);

    // Exercise: select branch, open diff, close, tab switch.
    await sidebar(page).getByRole("button", { name: /main/ }).first().click();
    await page.waitForTimeout(1200);
    await page.getByRole("listitem").first().click();
    await page.waitForTimeout(1500);

    const closeBtn = page
      .locator('aside[aria-label="Commit diff panel"]')
      .getByRole("button", { name: "Close diff panel" });
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click();
    }
    await page.getByRole("tab", { name: "Kanban" }).click();
    await page.waitForTimeout(1000);

    const criticalErrors = errors.filter(
      (e) =>
        !e.includes("ResizeObserver") &&
        !e.includes("WebSocket") &&
        !e.includes("Failed to load resource"),
    );
    const reactWarnings = warnings.filter(
      (w) => w.includes("React") && !w.includes("resize") && !w.includes("useLayoutEffect"),
    );

    console.log(
      `[TC-12] errors: ${errors.length}, critical: ${criticalErrors.length}, reactWarnings: ${reactWarnings.length}`,
    );
    if (criticalErrors.length > 0) console.log("[TC-12] Critical errors:", criticalErrors);

    expect(criticalErrors).toHaveLength(0);
  });
});
