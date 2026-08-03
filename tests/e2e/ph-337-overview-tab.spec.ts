/**
 * PH-337 — "Genel Bakış" (Overview) board tab + epic-progress panel move
 *
 * PH-337 adds a leading "Genel Bakış" tab to the BoardDetail tab strip and moves
 * the derived epic-progress rollup (PH-335 EpicProgressPanel) OFF its old
 * strip-top mount INTO that tab (via the thin OverviewTab container). This spec
 * is the regression net for the skeleton + move (PH-339 will fill the tab with
 * the Turkish summary + milestone timeline + editor — out of scope here).
 *
 * AC coverage:
 *   TC-1 (AC1) "Genel Bakış" tab is FIRST in the strip; click → #panel-overview
 *              active + location.hash === "#overview"; reload at #overview →
 *              overview tab re-opens ACTIVE (deep-link A1, hash-persist pattern).
 *   TC-2 (AC2) Default-active tab is unchanged (Kanban): hash-less load → Kanban
 *              aria-selected, #panel-kanban mounted, #panel-overview NOT mounted.
 *   TC-3 (AC3) The epic-progress panel now renders ONLY inside #panel-overview —
 *              it is NOT mounted above the strip / on the default Kanban view
 *              (old strip-top mount removed).
 *   TC-4 (AC4) Other tabs + hashes are regression-free (graph/quality/space/kanban).
 *
 * Environment: same live Vite dev server (localhost:5173) + PH board the sibling
 * board-tab specs (ph-159, ph-167, ph-241) target. Name/id-based selectors are
 * position-independent, so adding "overview" as the FIRST tab does not perturb
 * the others.
 */
import { test, expect, type Page } from "@playwright/test";
import { ADMIN_TOKEN } from "./helpers/workflowSnapshot";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5173";
// Board-access token — defaults to the shared e2e admin token (a PH member in
// the QA environment). Overridable via E2E_TOKEN for local runs whose live
// backend expects a different PH-member token.
const TOKEN = process.env.E2E_TOKEN ?? ADMIN_TOKEN;
const BOARD_KEY = "PH";
const BOARD_URL = `${BASE}/boards/${BOARD_KEY}`;

// The EpicProgressPanel renders one of these testids depending on its query
// state (loading / error / empty / populated). Any of them proves the panel is
// mounted; the shared prefix makes the check board-state-resilient.
const EPIC_PROGRESS = '[data-testid^="epic-progress"]';

/** Inject the admin token and navigate to the PH board (sibling-spec pattern). */
async function loginAndGoToBoard(page: Page, hash = "") {
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);
  await page.goto(`${BOARD_URL}${hash}`);
}

async function waitForBoardReady(page: Page) {
  await page.waitForSelector('[role="tablist"]', { timeout: 10000 });
}

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

test.describe("PH-337: Genel Bakış overview tab + progress move", () => {
  test.describe.configure({ mode: "serial" });

  test("TC-1: 'Genel Bakış' tab is FIRST; click → panel + #overview; reload persists (AC1)", async ({
    page,
  }) => {
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);

    // FIRST tab in the strip is the overview tab (id + accessible name).
    const firstTab = page.getByRole("tab").first();
    await expect(firstTab).toHaveAttribute("id", "tab-overview");
    await expect(firstTab).toHaveText("Genel Bakış");
    await expect(firstTab).toHaveAttribute("role", "tab");
    await expect(firstTab).toHaveAttribute("aria-controls", "panel-overview");

    // Click → overview panel active + hash written.
    await firstTab.click();
    await expect(firstTab).toHaveAttribute("aria-selected", "true");
    const panel = page.locator("#panel-overview");
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute("role", "tabpanel");
    await expect(panel).toHaveAttribute("aria-labelledby", "tab-overview");
    expect(page.url()).toContain("#overview");

    // Deep-link A1: reload at #overview → overview tab re-opens ACTIVE.
    await page.reload();
    await waitForBoardReady(page);
    await expect(page.locator("#tab-overview")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.locator("#panel-overview")).toBeVisible();
  });

  test("TC-2: default-active tab is unchanged (Kanban) — overview not auto-selected (AC2)", async ({
    page,
  }) => {
    await loginAndGoToBoard(page); // no hash → landing default
    await waitForBoardReady(page);

    // Kanban is the default-active tab even though overview is FIRST in the strip.
    await expect(page.locator("#tab-kanban")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.locator("#panel-kanban")).toBeAttached();
    // Overview is present in the strip but NOT auto-selected / mounted.
    await expect(page.locator("#tab-overview")).toHaveAttribute(
      "aria-selected",
      "false",
    );
    await expect(page.locator("#panel-overview")).not.toBeAttached();
  });

  test("TC-3: epic-progress panel renders ONLY inside the overview tab (AC3)", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);

    // Default (Kanban) view: the old strip-top mount is gone → NO epic-progress
    // element anywhere on the board.
    await expect(page.locator(EPIC_PROGRESS)).toHaveCount(0);

    // Open overview → the panel now mounts, and it lives INSIDE #panel-overview.
    await page.locator("#tab-overview").click();
    await expect(page.locator("#panel-overview")).toBeVisible();
    await expect(
      page.locator(`#panel-overview ${EPIC_PROGRESS}`),
    ).toHaveCount(1);
    // No stray epic-progress mount outside the overview panel.
    await expect(page.locator(EPIC_PROGRESS)).toHaveCount(1);

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("TC-4: other tabs + hashes regression-free (AC4)", async ({ page }) => {
    await loginAndGoToBoard(page);
    await waitForBoardReady(page);

    await page.locator("#tab-graph").click();
    await expect(page.locator("#panel-graph")).toBeVisible();
    expect(page.url()).toContain("#graph");

    await page.locator("#tab-quality").click();
    await expect(page.locator("#panel-quality")).toBeVisible();
    expect(page.url()).toContain("#quality");

    await page.locator("#tab-space").click();
    await expect(page.locator("#panel-space")).toBeVisible();
    expect(page.url()).toContain("#space");

    await page.locator("#tab-kanban").click();
    await expect(page.locator("#panel-kanban")).toBeVisible();
    expect(page.url()).toContain("#kanban");

    // And back to overview once more — full round-trip stays wired.
    await page.locator("#tab-overview").click();
    await expect(page.locator("#panel-overview")).toBeVisible();
    expect(page.url()).toContain("#overview");
  });
});
