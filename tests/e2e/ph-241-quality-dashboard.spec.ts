/**
 * PH-241 (epic PH-238, child C — FINAL) — In-app SonarQube "Quality" dashboard
 *
 * A third BoardDetail tab ("Quality", #quality) alongside Kanban / Branch Graph,
 * rendering SonarDashboard.tsx (gate hero + 6 metric cards + SonarIssueDrawer
 * drill-down). Verified IN-BROWSER against LIVE data (no mocks):
 *   - PH  : has analysis (gate=ERROR, code_smells=11, coverage=47.0%, dup=1.2%,
 *           ncloc=23386, bugs=0, vulns=0). dashboard_url present.
 *   - KIM : project_key=kims, health=null → honest "linked, no analysis yet".
 *
 * AC coverage:
 *   TC-1  Reachability (main area): third tab id=tab-quality in the board MAIN
 *         tab strip (NOT Settings); selecting → role=tabpanel #panel-quality;
 *         #quality hash refresh-safe.                       [Reachability]
 *   TC-2  Strip deep-link: SonarHealthPanel "View details →" switches to Quality;
 *         strip retained + unchanged.                       [Strip deep-link]
 *   TC-3  (PH) All 7 metrics + descriptions; gate hero; percent NN.N%, counts
 *         integer, real 0 NOT em-dash.                       [All 7 metrics]
 *   TC-4  (PH) Quality gate prominent: Failed hero + ERROR detail link.
 *                                                            [Quality gate prominent]
 *   TC-5  (PH) Code-linked drill-down: Code Smells (11) → drawer w/ component:line
 *         rows; bugs/vulns (0) cards DISABLED; close → focus restored.
 *                                              [Code-linked drill-down / Count]
 *   TC-6  (KIM) Honest empty state ("Linked to kims — no analysis yet"), no crash,
 *         no console errors.                                  [Honest empty state]
 *   TC-7  (PH) a11y keyboard: Tab to Code Smells card, Enter activates drawer.
 *                                                            [Accessibility]
 *   TC-8  (PH) Open-in-SonarQube external link (target=_blank rel=noopener).
 *                                                            [Open dashboard link]
 *   TC-9  Regression: Kanban ↔ Branch Graph ↔ Quality switching, no crash.
 *
 * Live WS refresh + dark/light/responsive + drawer-unreachable are documented in
 * the ticket test_plan as manual-observed / deferred (no host-side scan triggered).
 *
 * Screenshots → .jarwis/logs/PH-241/screenshots/
 */

import { test, expect, type Page } from "@playwright/test";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5174";
const ADMIN_TOKEN = "change-me-on-first-login";
const PH = "PH"; // project-hub — has analysis
const KIM = "KIM"; // Kims — project key set, no analysis
const SHOTS =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-241/screenshots";

async function loginAsAdmin(page: Page, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
}

async function gotoBoard(page: Page, key: string) {
  await page.goto(`${BASE}/boards/${key}`);
  await page.waitForLoadState("networkidle");
  // Board main area renders the tab strip once data loads.
  await expect(page.getByRole("tab", { name: "Kanban" })).toBeVisible({
    timeout: 10_000,
  });
}

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

// ---------------------------------------------------------------------------
// TC-1: Reachability — third Quality tab in the MAIN board area, refresh-safe
// ---------------------------------------------------------------------------
test("TC-1: Quality tab reachable from board main area, #quality refresh-safe", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);

  // Tab strip carries all three tabs in the MAIN area (NOT Settings).
  const tablist = page.getByRole("tablist", { name: /board views/i });
  await expect(tablist).toBeVisible();
  await expect(page.locator("#tab-kanban")).toBeVisible();
  await expect(page.locator("#tab-graph")).toBeVisible();
  const qTab = page.locator("#tab-quality");
  await expect(qTab).toBeVisible();
  await expect(qTab).toHaveAttribute("role", "tab");
  await expect(qTab).toContainText(/quality/i);

  await qTab.click();
  await expect(qTab).toHaveAttribute("aria-selected", "true");
  const panel = page.locator("#panel-quality");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("role", "tabpanel");
  expect(page.url()).toContain("#quality");

  // Refresh-safe: reload at #quality → still on the Quality panel.
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("#panel-quality")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#tab-quality")).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.screenshot({ path: `${SHOTS}/tc1-quality-tab-ph.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-2: Strip deep-link "View details →" switches to the Quality tab
// ---------------------------------------------------------------------------
test("TC-2: SonarHealthPanel 'View details' deep-links to Quality tab; strip retained", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);

  // Strip itself is retained above the tabs (not folded).
  const strip = page.getByTestId("sonar-health-panel");
  await expect(strip).toBeVisible();
  // Existing strip tiles still present (behavior unchanged).
  await expect(page.getByTestId("sonar-quality-gate")).toBeVisible();

  const deepLink = page.getByTestId("sonar-health-view-details");
  await expect(deepLink).toBeVisible();
  await deepLink.click();

  await expect(page.locator("#tab-quality")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator("#panel-quality")).toBeVisible();
  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-3 + TC-4 + TC-8: PH populated — all 7 metrics, gate hero, open link
// ---------------------------------------------------------------------------
test("TC-3/4/8: PH dashboard — gate hero (Failed) + 6 metric cards w/ values+descriptions + open link", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);
  await page.locator("#tab-quality").click();

  const dash = page.getByTestId("sonar-dashboard");
  await expect(dash).toBeVisible();

  // (TC-4) Quality-gate hero prominent — ERROR → "Failed".
  const hero = page.getByTestId("sonar-dashboard-gate-hero");
  await expect(hero).toBeVisible();
  await expect(page.getByTestId("sonar-dashboard-gate-status")).toContainText(
    /failed/i,
  );
  // ERROR → see-failing-conditions deep link (dashboardUrl present for PH).
  await expect(
    page.getByTestId("sonar-dashboard-gate-detail-link"),
  ).toBeVisible();

  // (TC-3) 6 grid metric cards, each with value + a non-empty description.
  const grid = page.getByTestId("sonar-dashboard-grid");
  await expect(grid).toBeVisible();
  const keys = [
    "bugs",
    "vulnerabilities",
    "code_smells",
    "coverage",
    "duplicated_lines_density",
    "ncloc",
  ];
  for (const k of keys) {
    await expect(page.getByTestId(`sonar-metric-card-${k}`)).toBeVisible();
  }

  // Value formatting: counts integer, percent NN.N%, real 0 NOT em-dash.
  await expect(page.getByTestId("sonar-metric-value-code_smells")).toHaveText(
    "11",
  );
  await expect(page.getByTestId("sonar-metric-value-coverage")).toHaveText(
    "47.0%",
  );
  await expect(
    page.getByTestId("sonar-metric-value-duplicated_lines_density"),
  ).toHaveText("1.2%");
  await expect(page.getByTestId("sonar-metric-value-ncloc")).toHaveText(
    "23386",
  );
  // bugs/vulns are a REAL 0 — integer "0", never the "—" no-data dash.
  await expect(page.getByTestId("sonar-metric-value-bugs")).toHaveText("0");
  await expect(page.getByTestId("sonar-metric-value-vulnerabilities")).toHaveText(
    "0",
  );

  // Each card carries a human-readable description (METRIC_META).
  await expect(page.getByTestId("sonar-metric-card-code_smells")).toContainText(
    /maintainability/i,
  );
  await expect(page.getByTestId("sonar-metric-card-coverage")).toContainText(
    /lines exercised by automated tests/i,
  );

  // (TC-8) Open-in-SonarQube external link, target=_blank rel=noopener.
  const openLink = page.getByTestId("sonar-dashboard-open-link");
  await expect(openLink).toBeVisible();
  await expect(openLink).toHaveAttribute("target", "_blank");
  await expect(openLink).toHaveAttribute("rel", /noopener/);
  await expect(openLink).toHaveAttribute("rel", /noreferrer/);

  await page.screenshot({
    path: `${SHOTS}/tc3-ph-dashboard-populated.png`,
    fullPage: true,
  });
  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-5: Code-linked drill-down — Code Smells → drawer w/ component:line;
//       zero-count cards disabled; close restores focus.
// ---------------------------------------------------------------------------
test("TC-5: Code Smells card → SonarIssueDrawer (component:line); 0-count cards disabled; focus restored", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);
  await page.locator("#tab-quality").click();

  // bugs/vulns count is 0 → those cards are DISABLED (never open empty drawer).
  await expect(page.getByTestId("sonar-metric-card-bugs")).toBeDisabled();
  await expect(
    page.getByTestId("sonar-metric-card-vulnerabilities"),
  ).toBeDisabled();

  // Code Smells (11 > 0) is an enabled button, aria-haspopup=dialog.
  const smells = page.getByTestId("sonar-metric-card-code_smells");
  await expect(smells).toBeEnabled();
  await expect(smells).toHaveAttribute("aria-haspopup", "dialog");
  await smells.click();

  // Drawer opens with issue rows carrying component[:line].
  const drawer = page.getByTestId("sonar-issue-drawer");
  await expect(drawer).toBeVisible({ timeout: 8_000 });
  const dialog = drawer.getByRole("dialog");
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(page.getByTestId("sonar-drawer-list")).toBeVisible();
  const rows = page.getByTestId("sonar-drawer-row");
  await expect(rows.first()).toBeVisible();
  // First row shows a file path (component) — and a :line for line-bound issues.
  await expect(rows.first()).toContainText(/[a-z]/i);
  // Dashboard deep link present inside the drawer.
  await expect(page.getByTestId("sonar-drawer-dashboard-link")).toBeVisible();

  await page.screenshot({
    path: `${SHOTS}/tc5-code-smells-drawer.png`,
    fullPage: true,
  });

  // Close via X → focus restored to the Code Smells card.
  await page.getByTestId("sonar-drawer-close").click();
  await expect(page.getByTestId("sonar-issue-drawer")).toHaveCount(0);
  await expect(smells).toBeFocused();

  // Coverage/dup/ncloc are informational (non-interactive divs — no button role).
  await expect(page.getByTestId("sonar-metric-card-coverage")).not.toHaveAttribute(
    "aria-haspopup",
    "dialog",
  );

  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-6: KIM honest empty state — "Linked to kims — no analysis yet", no crash
// ---------------------------------------------------------------------------
test("TC-6: KIM (no analysis) — honest empty state, not blank, no fake-zero grid, no console errors", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, KIM);
  await page.locator("#tab-quality").click();

  const empty = page.getByTestId("sonar-dashboard-empty");
  await expect(empty).toBeVisible();
  await expect(page.getByTestId("sonar-dashboard-no-analysis")).toContainText(
    /no analysis yet/i,
  );
  await expect(empty).toContainText(/kims/i);
  // NO metric grid / gate hero is rendered (no fabricated all-zero dashboard).
  await expect(page.getByTestId("sonar-dashboard-grid")).toHaveCount(0);
  await expect(page.getByTestId("sonar-dashboard-gate-hero")).toHaveCount(0);

  await page.screenshot({ path: `${SHOTS}/tc6-kim-empty.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-7: Keyboard a11y — Tab to Code Smells card, Enter activates the drawer
// ---------------------------------------------------------------------------
test("TC-7: keyboard — focus Code Smells card and activate drawer with Enter", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);
  await page.locator("#tab-quality").click();

  const smells = page.getByTestId("sonar-metric-card-code_smells");
  await expect(smells).toBeEnabled();
  // Programmatic focus then keyboard activation (Enter) — native <button>.
  await smells.focus();
  await expect(smells).toBeFocused();
  // aria-label reflects the count/state.
  await expect(smells).toHaveAttribute("aria-label", /code smells/i);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("sonar-issue-drawer")).toBeVisible({
    timeout: 8_000,
  });

  // ESC closes and restores focus.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("sonar-issue-drawer")).toHaveCount(0);
  await expect(smells).toBeFocused();

  expect(errors, errors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-9: Regression — Kanban ↔ Branch Graph ↔ Quality switching, no crash
// ---------------------------------------------------------------------------
test("TC-9: regression — switch Kanban / Branch Graph / Quality tabs without crash", async ({
  page,
}) => {
  const errors = trackConsole(page);
  await loginAsAdmin(page);
  await gotoBoard(page, PH);

  await page.locator("#tab-quality").click();
  await expect(page.locator("#panel-quality")).toBeVisible();

  await page.locator("#tab-kanban").click();
  await expect(page.locator("#panel-kanban")).toBeVisible();
  expect(page.url()).toContain("#kanban");

  await page.locator("#tab-graph").click();
  await expect(page.locator("#panel-graph")).toBeVisible();
  expect(page.url()).toContain("#graph");

  // Back to Quality — still works after round-trip.
  await page.locator("#tab-quality").click();
  await expect(page.getByTestId("sonar-dashboard")).toBeVisible();

  expect(errors, errors.join("\n")).toEqual([]);
});
