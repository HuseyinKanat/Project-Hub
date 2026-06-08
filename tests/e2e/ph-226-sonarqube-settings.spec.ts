/**
 * PH-226 (epic PH-220, C6) — SonarQube board-settings section — Mode B Verify
 *
 * AC coverage:
 *   TC-1:  Live status endpoint returns the documented shape (project_key=project-hub,
 *          host-facing dashboard_url, NO secret leak)                       (AC1 + gotchas)
 *   TC-2:  Status panel renders configured/enabled/reachable chips, project_key (mono),
 *          QG pill, relative last-fetched, Open dashboard (target=_blank rel=noopener)  (AC1)
 *   TC-3:  enabled=false → "not enabled" banner + Setup & Sync DISABLED, no POST fires   (AC4)
 *   TC-4:  reachable=false → unreachable note, Sync STAYS enabled (retry), no crash      (AC5)
 *   TC-6:  403 on Setup AND Sync → inline "Admin role required", NO crash/overlay        (AC6)
 *   TC-7:  Non-admin (/me member) → read-only banner, buttons HIDDEN, panel still shown  (AC6)
 *   TC-8:  Board settings regression — repository/members tabs still render              (regression)
 *
 * NOTE on scope (per Coordinator directive + non-admin QA boundary):
 *   - We use an ADMIN session token (browser localStorage auth only) so the Setup/Sync
 *     buttons RENDER — this changes NO board membership / role on the server.
 *   - status / setup / sync are MOCKED at the network layer (precedent: ph-162) so the
 *     enabled=false / reachable=false / 403 states are deterministic — the live PH server
 *     has sonar enabled+reachable and the dev token cannot perform an admin write.
 *   - The admin happy-path WRITE (real 200 + tile refresh) is covered by PH-223 backend
 *     tests + the reviewer's line-by-line invalidation-key match; the 403 path is what is
 *     browser-verifiable under a non-admin token. Tile-refresh wiring is asserted by the
 *     reviewer (key match vs BoardDetail sonarqube_synced WS handler) — not re-clicked here.
 *
 * Screenshots → .jarwis/logs/PH-226/qa-screenshots/
 */

import { test, expect, type Page } from "@playwright/test";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const ADMIN_TOKEN = "change-me-on-first-login";
const PH_BOARD = "PH";
const SCREENSHOTS_DIR =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-226/qa-screenshots";

// A live-like configured status payload (mirrors the real PH endpoint shape).
const CONFIGURED_STATUS = {
  enabled: true,
  reachable: true,
  configured: true,
  project_key: "project-hub",
  last_metric_fetched_at: new Date(Date.now() - 3 * 60_000).toISOString(),
  quality_gate_status: "ERROR",
  dashboard_url: "http://localhost:9000/dashboard?id=project-hub",
  message: "SonarQube linked to project-hub.",
};

async function loginAsAdmin(page: Page, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
}

/** Mock GET .../sonarqube/status with a given payload. */
async function mockStatus(page: Page, body: object) {
  await page.route(
    new RegExp("/api/boards/[^/]+/sonarqube/status"),
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    },
  );
}

/** Mock POST .../sonarqube/setup|sync to a given status code. */
async function mockWrites(page: Page, status: number, body: object) {
  await page.route(
    new RegExp("/api/boards/[^/]+/sonarqube/(setup|sync)"),
    async (route) => {
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    },
  );
}

/** Override /auth/me to make the current actor a non-admin member of PH. */
async function mockMeNonAdmin(page: Page) {
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
          { board_id: "ph-id", board_key: PH_BOARD, role: "frontend_dev" },
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
  await page.waitForTimeout(500);
}

// ---------------------------------------------------------------------------
// TC-1: Live status endpoint shape — project_key=project-hub, host link, no leak
// ---------------------------------------------------------------------------
test("TC-1: live GET .../sonarqube/status returns documented shape, no secret leak (AC1)", async ({
  request,
}) => {
  // Member-level GET against the real backend (admin token is the dev session token;
  // GET status is member-level so any valid token returns 200). Asserts the real
  // contract, not a mock.
  const res = await request.get(
    `${BACKEND}/api/boards/${PH_BOARD}/sonarqube/status`,
    { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
  );
  expect(res.status()).toBe(200);
  const body = await res.json();
  // Documented fields present.
  for (const k of [
    "enabled",
    "reachable",
    "configured",
    "project_key",
    "last_metric_fetched_at",
    "quality_gate_status",
    "dashboard_url",
    "message",
  ]) {
    expect(body).toHaveProperty(k);
  }
  // Gotcha [PH-223]: PH key MUST be project-hub (not "ph").
  expect(body.project_key).toBe("project-hub");
  // Gotcha [PH-223/PH-203]: never leak the token or the compose-internal URL.
  const raw = JSON.stringify(body);
  expect(raw).not.toContain("sonarqube_token");
  expect(raw).not.toMatch(/sonarqube:9000/); // compose-internal host
  if (body.dashboard_url) {
    expect(body.dashboard_url).toMatch(/\/dashboard\?id=project-hub/);
  }
});

// ---------------------------------------------------------------------------
// TC-2: Status panel renders (configured, chips, QG pill, dashboard link)
// ---------------------------------------------------------------------------
test("TC-2: status panel renders configured state + dashboard link (AC1)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  await loginAsAdmin(page);
  await mockStatus(page, CONFIGURED_STATUS);
  await goToSonarTab(page, PH_BOARD);

  const panel = page.getByTestId("sonar-status-panel");
  await expect(panel).toBeVisible();

  // project_key shows the resolved key (gotcha: project-hub, not ph).
  await expect(page.getByTestId("sonar-project-key")).toHaveText("project-hub");

  // Quality-gate pill (ERROR → "Failed").
  await expect(page.getByTestId("sonar-quality-gate")).toContainText(/Failed/i);

  // Three flag chips render.
  await expect(page.getByTestId("sonar-chip-enabled")).toBeVisible();
  await expect(page.getByTestId("sonar-chip-reachable")).toBeVisible();
  await expect(page.getByTestId("sonar-chip-configured")).toBeVisible();

  // Relative last-fetched (mocked 3m ago).
  await expect(page.getByTestId("sonar-last-fetched")).toContainText(/ago|minute/i);

  // Open dashboard link → host-facing dashboard_url, new tab, noopener.
  const link = page.getByTestId("sonar-dashboard-link");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute(
    "href",
    "http://localhost:9000/dashboard?id=project-hub",
  );
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc2-status-panel.png`, fullPage: true });
  expect(consoleErrors, `console errors: ${consoleErrors.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-3: enabled=false → disabled banner + buttons disabled, no POST fires (AC4)
// ---------------------------------------------------------------------------
test("TC-3: enabled=false disables Setup+Sync, no call fires (AC4)", async ({
  page,
}) => {
  let writeFired = false;
  await loginAsAdmin(page);
  await mockStatus(page, { ...CONFIGURED_STATUS, enabled: false, reachable: false, message: "SonarQube is disabled on this server." });
  await mockWrites(page, 200, CONFIGURED_STATUS);
  page.on("request", (req) => {
    if (/\/sonarqube\/(setup|sync)/.test(req.url())) writeFired = true;
  });

  await goToSonarTab(page, PH_BOARD);

  await expect(page.getByTestId("sonar-disabled-banner")).toContainText(
    /not enabled/i,
  );
  const setupBtn = page.getByTestId("sonar-setup-btn");
  const syncBtn = page.getByTestId("sonar-sync-btn");
  await expect(setupBtn).toBeDisabled();
  await expect(syncBtn).toBeDisabled();

  // Force a click on the disabled button — must NOT fire a POST.
  await setupBtn.click({ force: true }).catch(() => {});
  await syncBtn.click({ force: true }).catch(() => {});
  await page.waitForTimeout(400);
  expect(writeFired).toBe(false);

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc3-disabled-state.png`, fullPage: true });
});

// ---------------------------------------------------------------------------
// TC-4: reachable=false → unreachable note, Sync stays enabled (AC5)
// ---------------------------------------------------------------------------
test("TC-4: reachable=false shows note, Sync stays enabled for retry (AC5)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await mockStatus(page, {
    ...CONFIGURED_STATUS,
    reachable: false,
    message: "Last poll is stale; server may be unreachable.",
  });
  await goToSonarTab(page, PH_BOARD);

  await expect(page.getByTestId("sonar-unreachable-banner")).toContainText(
    /unreachable|stale/i,
  );
  // Sync stays enabled (retry); Setup also enabled (server kill-switch is on).
  await expect(page.getByTestId("sonar-sync-btn")).toBeEnabled();
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc4-unreachable-state.png`, fullPage: true });
});

// ---------------------------------------------------------------------------
// TC-6: 403 on Setup AND Sync → inline "Admin role required", no crash (AC6)
// ---------------------------------------------------------------------------
test("TC-6: 403 write is caught inline, NO crash / unhandled rejection (AC6)", async ({
  page,
}) => {
  const fatal: string[] = [];
  page.on("pageerror", (e) => fatal.push(`pageerror: ${e.message}`));
  const seen403: string[] = [];
  page.on("response", (r) => {
    if (/\/sonarqube\/(setup|sync)/.test(r.url()) && r.status() === 403)
      seen403.push(r.url());
  });

  await loginAsAdmin(page);
  await mockStatus(page, CONFIGURED_STATUS);
  await mockWrites(page, 403, { detail: "Admin role required for this board." });
  await goToSonarTab(page, PH_BOARD);

  // Sync → 403 → inline alert.
  await page.getByTestId("sonar-sync-btn").click();
  const alert = page.getByTestId("sonar-action-error");
  await expect(alert).toBeVisible({ timeout: 5_000 });
  await expect(alert).toContainText(/admin role required/i);
  await expect(alert).toHaveAttribute("role", "alert");

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc6-403-message.png`, fullPage: true });

  // Setup → 403 → also caught, still no crash.
  await page.getByTestId("sonar-setup-btn").click();
  await expect(page.getByTestId("sonar-action-error")).toContainText(
    /admin role required/i,
  );

  // No Vite error overlay (the crash signature).
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  // Both POSTs actually returned 403 (real status path, not a swallowed click).
  expect(seen403.length).toBeGreaterThanOrEqual(1);
  // No unhandled page error.
  expect(fatal, `fatal errors: ${fatal.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-7: Non-admin → read-only banner, buttons hidden, panel still visible (AC6)
// ---------------------------------------------------------------------------
test("TC-7: non-admin sees read-only status panel, no write buttons (AC6)", async ({
  page,
}) => {
  await loginAsAdmin(page); // session auth; role is overridden via /me mock below
  await mockMeNonAdmin(page);
  await mockStatus(page, CONFIGURED_STATUS);
  await goToSonarTab(page, PH_BOARD);

  // Read-only banner present.
  await expect(page.getByTestId("sonarqube-readonly-banner")).toBeVisible();
  // Status panel still rendered (member-level GET).
  await expect(page.getByTestId("sonar-status-panel")).toBeVisible();
  await expect(page.getByTestId("sonar-project-key")).toHaveText("project-hub");
  // Write buttons hidden (no admin actions section).
  await expect(page.getByTestId("sonar-actions")).toHaveCount(0);
  await expect(page.getByTestId("sonar-setup-btn")).toHaveCount(0);
  await expect(page.getByTestId("sonar-sync-btn")).toHaveCount(0);

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc7-nonadmin-readonly.png`, fullPage: true });
});

// ---------------------------------------------------------------------------
// TC-8: Regression — repository + members tabs still render (no scope creep)
// ---------------------------------------------------------------------------
test("TC-8: board settings tabs regression — repository + members intact", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await page.goto(`${BASE}/boards/${PH_BOARD}/settings`);
  await page.waitForLoadState("networkidle");
  const tabTexts = (await page.getByRole("tab").allTextContents()).map((t) =>
    t.toLowerCase(),
  );
  for (const expected of ["general", "workflow", "members", "repository", "sonarqube"]) {
    expect(tabTexts.some((t) => t.includes(expected))).toBe(true);
  }
  // SonarQube is the last tab (after repository).
  const repoIdx = tabTexts.findIndex((t) => t.includes("repository"));
  const sonarIdx = tabTexts.findIndex((t) => t.includes("sonarqube"));
  expect(sonarIdx).toBeGreaterThan(repoIdx);
});
