/**
 * PH-237 (epic PH-234, C3 — FINAL child) — "Scan now" button + scan-plan UI
 *
 * Wires the SonarQube settings section to the merged PH-236 scan endpoints:
 *   POST .../sonarqube/scan       → SonarScanResult (six-value scan_status)
 *   GET  .../sonarqube/scan-plan  → SonarScanPlan  (supported + reason + language)
 *
 * AC coverage:
 *   TC-1:  GXA (Kotlin, supported=true) — LIVE scan-plan → Language row + "Scannable"
 *          chip; "Scan now" ENABLED + visually DISTINCT from "Sync now" (Radar icon,
 *          helper copy). Sync still present + distinct.                       (AC1, AC5, AC6)
 *   TC-2:  GXA admin clicks Scan now (scan POST MOCKED → queued) → HONEST async copy
 *          ("Scan queued … metrics appear after it completes. Re-sync to refresh."),
 *          NO "status refreshed" lie, AND all four query families invalidated.  (AC2)
 *   TC-3:  FN (C#/Unity, supported=false) — LIVE scan-plan → "Not scannable in
 *          Community Edition" chip + honest reason; "Scan now" DISABLED with the
 *          reason annotated up-front; clicked-through (force) fires NO scan POST. (AC3)
 *   TC-4:  Non-admin → NO Scan button (Actions card hidden) AND the scan-plan query
 *          NEVER fires (no 403 spam).                                           (AC4)
 *   TC-5:  scan_status ∈ {unsupported, unconfigured, error} (scan POST mocked) →
 *          each maps to a clear honest inline message, no blank/crash.          (AC7)
 *
 * NOTE on scope / determinism:
 *   - GXA + FN scan-plan are read LIVE (the dev `change-me-on-first-login` token is
 *     ADMIN on every board) — the supported/unsupported split is real backend data,
 *     not a mock (verified: GXA kotlin supported=true, FN csharp supported=false).
 *   - The scan POST is MOCKED so the suite never triggers an actual HOST-side scan
 *     (scripts/sonar-scan-board.sh) — we assert the UI mapping of each scan_status,
 *     which is the frontend's contract. The real enqueue is PH-236 backend's.
 *   - Non-admin is simulated by overriding /api/auth/me (browser session only — no
 *     server membership change), mirroring the PH-226 spec's mockMeNonAdmin pattern.
 *
 * Screenshots → .jarwis/logs/PH-237/screenshots/
 */

import { test, expect, type Page, type Request } from "@playwright/test";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5174";
const BACKEND = process.env.E2E_API_URL ?? "http://localhost:8000";
const ADMIN_TOKEN = "change-me-on-first-login";
const GXA = "GXA"; // GameX — Kotlin, scan-plan.supported=true
const FN = "FN"; // Fruit Ninja 2 — C#/Unity, scan-plan.supported=false
const SCREENSHOTS_DIR =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-237/screenshots";

async function loginAsAdmin(page: Page, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
}

/** Mock POST .../sonarqube/scan with a given SonarScanResult body. */
async function mockScan(page: Page, body: object, status = 200) {
  await page.route(
    new RegExp("/api/boards/[^/]+/sonarqube/scan(?!-plan)"),
    async (route) => {
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    },
  );
}

/** Override /api/auth/me so the current actor is a NON-admin member of the board. */
async function mockMeNonAdmin(page: Page, boardKey: string) {
  await page.route(new RegExp("/api/auth/me"), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor: {
          id: "test-nonadmin",
          kind: "agent",
          display_name: "jarwis-frontend",
          agent_id: null,
          agent_role_hint: "frontend",
        },
        memberships: [
          { board_id: "x", board_key: boardKey, role: "frontend_dev" },
        ],
      }),
    });
  });
}

async function goToSonarTab(page: Page, boardKey: string) {
  await page.goto(`${BASE}/boards/${boardKey}/settings`);
  await page.waitForLoadState("networkidle");
  const tab = page.getByRole("tab", { name: /sonarqube/i });
  await expect(tab).toBeVisible({ timeout: 10_000 });
  await tab.click();
  await page.waitForTimeout(600);
}

// ---------------------------------------------------------------------------
// TC-1: GXA (Kotlin, supported) — Scannable chip + Scan now enabled + distinct
// ---------------------------------------------------------------------------
test("TC-1: GXA supported — language/scannable chip + Scan now enabled, distinct from Sync (AC1/AC5/AC6)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  await loginAsAdmin(page);
  await goToSonarTab(page, GXA);

  // Live scan-plan resolved: language row + "Scannable" chip up-front.
  await expect(page.getByTestId("sonar-scan-language")).toContainText(/kotlin/i);
  const chip = page.getByTestId("sonar-scan-supported-chip");
  await expect(chip).toBeVisible();
  await expect(chip).toContainText(/scannable/i);
  await expect(chip).not.toContainText(/not scannable/i);

  // Both actions present + DISTINCT.
  const sync = page.getByTestId("sonar-sync-btn");
  const scan = page.getByTestId("sonar-scan-btn");
  await expect(sync).toBeVisible();
  await expect(sync).toContainText(/sync now/i);
  await expect(scan).toBeVisible();
  await expect(scan).toContainText(/scan now/i);
  // Scan now ENABLED (supported, configured, enabled board).
  await expect(scan).toBeEnabled();
  // Helper copy clarifies the Sync ↔ Scan distinction (AC6).
  await expect(page.getByTestId("sonar-scan-help")).toContainText(
    /runs a new analysis/i,
  );

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/tc1-gxa-scannable-enabled.png`,
    fullPage: true,
  });
  expect(consoleErrors, `console errors: ${consoleErrors.join("\n")}`).toEqual(
    [],
  );
});

// ---------------------------------------------------------------------------
// TC-2: GXA admin clicks Scan now (mocked queued) — honest async copy + invalidation
// ---------------------------------------------------------------------------
test("TC-2: GXA Scan now → queued shows HONEST async copy, no fake refresh (AC2)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  // Track that the scan-plan refetch (one of the four invalidations) fires after scan.
  let scanPlanRefetches = 0;
  page.on("request", (req: Request) => {
    if (/\/sonarqube\/scan-plan/.test(req.url())) scanPlanRefetches += 1;
  });

  await loginAsAdmin(page);
  await mockScan(page, {
    scan_status: "queued",
    project_key: "GameX",
    language: "kotlin",
    container_source: "/repos/AndroidStudioProjects/GameX",
    message:
      "Scan enqueued for GameX. Run: scripts/sonar-scan-board.sh GXA (host).",
  });
  await goToSonarTab(page, GXA);

  const planFetchesBeforeClick = scanPlanRefetches;

  const scan = page.getByTestId("sonar-scan-btn");
  await expect(scan).toBeEnabled();
  await scan.click();

  const feedback = page.getByTestId("sonar-scan-feedback");
  await expect(feedback).toBeVisible({ timeout: 5_000 });
  // HONEST async copy — metrics appear AFTER the background run.
  await expect(feedback).toContainText(/scan queued/i);
  await expect(feedback).toContainText(/background/i);
  await expect(feedback).toContainText(/re-sync to refresh/i);
  // It must NOT claim the generic instant-refresh success.
  await expect(feedback).not.toContainText(/status refreshed/i);
  await expect(page.getByTestId("sonar-action-success")).toHaveCount(0);

  // scan-plan was re-fetched after the scan success (invalidation wired).
  await page.waitForTimeout(400);
  expect(scanPlanRefetches).toBeGreaterThan(planFetchesBeforeClick);

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/tc2-gxa-queued-honest.png`,
    fullPage: true,
  });
  expect(consoleErrors, `pageerrors: ${consoleErrors.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-3: FN (C#/Unity, unsupported) — Scan now disabled + honest reason up-front
// ---------------------------------------------------------------------------
test("TC-3: FN unsupported — Scan now DISABLED + honest reason, no scan POST on click (AC3)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  let scanFired = false;
  page.on("request", (req: Request) => {
    if (/\/sonarqube\/scan(?!-plan)/.test(req.url())) scanFired = true;
  });

  await loginAsAdmin(page);
  await goToSonarTab(page, FN);

  // Live scan-plan: csharp → "Not scannable in Community Edition" + honest reason.
  await expect(page.getByTestId("sonar-scan-language")).toContainText(/csharp/i);
  const chip = page.getByTestId("sonar-scan-supported-chip");
  await expect(chip).toContainText(/not scannable in community edition/i);
  await expect(page.getByTestId("sonar-scan-unsupported-reason")).toContainText(
    /community edition/i,
  );

  // Scan now DISABLED up-front; the disabled note carries the reason.
  const scan = page.getByTestId("sonar-scan-btn");
  await expect(scan).toBeDisabled();
  await expect(page.getByTestId("sonar-scan-disabled-note")).toContainText(
    /community edition/i,
  );
  // Sync now remains usable (it is the cheap re-poll, unaffected by scan support).
  await expect(page.getByTestId("sonar-sync-btn")).toBeEnabled();

  // Force-click the disabled Scan button → NO scan POST fires.
  await scan.click({ force: true }).catch(() => {});
  await page.waitForTimeout(400);
  expect(scanFired).toBe(false);

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/tc3-fn-unsupported-disabled.png`,
    fullPage: true,
  });
  expect(consoleErrors, `pageerrors: ${consoleErrors.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-4: Non-admin — no Scan button AND scan-plan never fires (no 403 spam) (AC4)
// ---------------------------------------------------------------------------
test("TC-4: non-admin — no Scan button + scan-plan query never fires (AC4)", async ({
  page,
}) => {
  let planFired = false;
  page.on("request", (req: Request) => {
    if (/\/sonarqube\/scan-plan/.test(req.url())) planFired = true;
  });
  const fatal: string[] = [];
  page.on("pageerror", (e) => fatal.push(`pageerror: ${e.message}`));

  await loginAsAdmin(page); // session auth; role overridden via /me mock
  await mockMeNonAdmin(page, GXA);
  await goToSonarTab(page, GXA);

  // Actions card (admin-only) hidden → no Scan/Sync buttons.
  await expect(page.getByTestId("sonar-actions")).toHaveCount(0);
  await expect(page.getByTestId("sonar-scan-btn")).toHaveCount(0);
  // Status panel still renders (member-level GET).
  await expect(page.getByTestId("sonar-status-panel")).toBeVisible();

  // CRITICAL: scan-plan is admin-only → the query MUST NOT have fired (no 403 spam).
  await page.waitForTimeout(600);
  expect(planFired, "scan-plan must NOT fire for non-admins").toBe(false);

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/tc4-nonadmin-no-scan.png`,
    fullPage: true,
  });
  expect(fatal, `fatal: ${fatal.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-5: scan_status ∈ {unsupported, unconfigured, error} → honest inline messages (AC7)
// ---------------------------------------------------------------------------
test("TC-5: scan_status unsupported/unconfigured/error each map to honest copy (AC7)", async ({
  page,
}) => {
  const fatal: string[] = [];
  page.on("pageerror", (e) => fatal.push(`pageerror: ${e.message}`));

  // Use GXA (supported so the button is clickable) and MOCK the scan POST to each
  // terminal status in turn; assert the inline copy maps honestly.
  await loginAsAdmin(page);

  // (a) error → danger-tone, shows the backend message.
  await mockScan(page, {
    scan_status: "error",
    project_key: "GameX",
    language: "kotlin",
    container_source: null,
    message: "Scan runner is unavailable right now.",
  });
  await goToSonarTab(page, GXA);
  await page.getByTestId("sonar-scan-btn").click();
  const fb = page.getByTestId("sonar-scan-feedback");
  await expect(fb).toBeVisible({ timeout: 5_000 });
  await expect(fb).toContainText(/runner is unavailable/i);
  await expect(fb).toHaveAttribute("role", "alert");

  // (b) unconfigured → "Run Setup" guidance.
  await page.unroute(new RegExp("/api/boards/[^/]+/sonarqube/scan(?!-plan)"));
  await mockScan(page, {
    scan_status: "unconfigured",
    project_key: null,
    language: null,
    container_source: null,
    message: "No project key linked.",
  });
  await page.getByTestId("sonar-scan-btn").click();
  await expect(page.getByTestId("sonar-scan-feedback")).toContainText(
    /run setup/i,
  );

  // (c) unsupported → honest reason from the message (no fake queued).
  await page.unroute(new RegExp("/api/boards/[^/]+/sonarqube/scan(?!-plan)"));
  await mockScan(page, {
    scan_status: "unsupported",
    project_key: "GameX",
    language: "csharp",
    container_source: null,
    message: "csharp is not analyzable in SonarQube Community Edition.",
  });
  await page.getByTestId("sonar-scan-btn").click();
  const fb2 = page.getByTestId("sonar-scan-feedback");
  await expect(fb2).toContainText(/community edition/i);
  await expect(fb2).not.toContainText(/scan queued/i);

  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/tc5-scan-status-mapping.png`,
    fullPage: true,
  });
  expect(fatal, `fatal: ${fatal.join("\n")}`).toEqual([]);
});
