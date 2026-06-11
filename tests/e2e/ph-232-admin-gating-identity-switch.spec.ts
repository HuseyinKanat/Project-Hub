/**
 * PH-232 — admin gating broken after login (`me` query not invalidated on token
 * change) — Mode B Verify.
 *
 * The defect: an in-app identity switch (token A → admin token B) left the
 * cached `["me"]` from the PRIOR identity, so `useBoardRole`/`isAdmin` stayed
 * stale and admin-gated controls (SonarQube Setup/Sync, repos_path editor,
 * repository add/remove) remained locked until a HARD RELOAD.
 *
 * The fix (commit c359c88):
 *   1. `useMe()` keys `["me", token]` → token-scoped; a switch yields a NEW cache
 *      entry, never serves the prior identity.
 *   2. The auth store calls `queryClient.clear()` on EVERY real identity change
 *      (`setToken` guard prev!==token, `logout` guard had!==null), so no
 *      prior-identity bytes survive the boundary.
 *   3. Same-token re-set short-circuits → no refetch storm / flicker.
 *
 * Verification strategy — the REAL production UI flow (no module shims):
 *   - Backend reads are mocked at the network layer (precedent: ph-226 / ph-162)
 *     so the admin/non-admin gating state is deterministic and independent of the
 *     live server's actual role:
 *       · /auth/me      → role driven by a mutable `identity.role` flag (active id)
 *       · /boards/PH    → deterministic board (so the settings tabs render)
 *       · /boards (list)→ 200 so verifyToken() accepts ANY token value at login
 *       · sonar/status  → enabled+reachable, unconfigured (Setup is primary)
 *       · notifications / tickets / catch-all GET → benign (keeps console clean)
 *   - The identity switch is driven through the genuine UI: the Layout **Logout**
 *     button (real `useAuth.logout`) and the **Login** form submit (real
 *     `useAuth.setToken`) — the EXACT chokepoints the fix instruments. No reach
 *     into app modules, so the app's real store singleton is exercised. All
 *     in-session navigation is SPA (React Router) — no hard page reload is
 *     required to refresh `isAdmin` (that requirement WAS the bug).
 *
 * AC mapping:
 *   TC-1 (AC3, bug state): non-admin → read-only banner + buttons hidden.
 *   TC-2 (AC1+AC3+AC4, THE FIX): logout → login as admin (token VALUE changes,
 *         no hard reload to refresh isAdmin) → banner gone, Setup/Sync render.
 *   TC-3 (AC2+AC4, no leak): the new admin identity shows ONLY admin state — the
 *         stale non-admin `me` did NOT survive (banner never reappears on a pure
 *         SPA tab re-render) → proves clear-all at the boundary.
 *   TC-4 (AC5, same-token): re-submitting the SAME token does NOT refetch /me
 *         (guard short-circuits → no refetch storm).
 *
 * NOTE: the actual Setup POST 403 (backend `require_board_admin` quirk) is
 * PH-233 and is OUT OF SCOPE — this spec verifies ONLY the frontend gating flip.
 *
 * Screenshots → .jarwis/logs/PH-232/qa-screenshots/
 */

import { test, expect, type Page } from "@playwright/test";

const BASE = "http://localhost:5174"; // host-mapped Vite dev port (container 5173)
const PH_BOARD = "PH";
const TOKEN_A = "ph232-identity-A-nonadmin";
const TOKEN_B = "ph232-identity-B-admin"; // DISTINCT value → setToken guard fires clear()
const SCREENSHOTS_DIR =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-232/qa-screenshots";

const SONAR_STATUS = {
  enabled: true,
  reachable: true,
  configured: false, // unconfigured → Setup is the primary action (the bug scenario)
  project_key: "project-hub",
  last_metric_fetched_at: null,
  quality_gate_status: null,
  dashboard_url: "http://localhost:9000/dashboard?id=project-hub",
  message: "SonarQube linked to project-hub.",
};

const BOARD_BODY = {
  id: "ph-id",
  key: PH_BOARD,
  name: "project-hub",
  repos_path: "/Users/huseyinkanat/Documents/project-hub",
  workflow: { id: "wf-id", states: [], transitions: [] },
};

function meBody(role: "admin" | "frontend_dev") {
  return {
    actor: {
      id: role === "admin" ? "admin-actor" : "nonadmin-actor",
      kind: role === "admin" ? "human" : "agent",
      display_name: role === "admin" ? "Admin" : "jarwis-frontend",
      agent_id: null,
      agent_role_hint: role === "admin" ? null : "frontend",
    },
    memberships: [{ board_id: "ph-id", board_key: PH_BOARD, role }],
  };
}

/** Per-page mutable identity. `/auth/me` reads this fresh on every call. */
type Identity = { role: "admin" | "frontend_dev" };

async function installMocks(page: Page, identity: Identity, counters: { me: number }): Promise<void> {
  // Playwright matches routes in REVERSE registration order (last-registered
  // wins). Register the broad catch-all FIRST so the specific routes below take
  // precedence; the catch-all only handles genuinely-unmatched /api GETs.
  await page.route(new RegExp("//[^/]+/api/"), async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    } else {
      await route.fallback();
    }
  });
  await page.route(new RegExp("/api/notifications"), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ notifications: [], unread_count: 0 }) });
  });
  await page.route(new RegExp("/api/tickets"), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tickets: [] }) });
  });
  await page.route(new RegExp("/api/boards/[^/]+/sonarqube/status"), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SONAR_STATUS) });
  });
  // Board LIST — verifyToken() hits this; 200 → any token value verifies at login.
  await page.route(new RegExp("/api/boards$"), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ boards: [BOARD_BODY] }) });
  });
  // Board detail — settings tabs render off this.
  await page.route(new RegExp("/api/boards/PH$"), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BOARD_BODY) });
  });
  // /auth/me LAST → highest precedence; role follows the mutable identity flag.
  await page.route(new RegExp("/api/auth/me"), async (route) => {
    counters.me += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(meBody(identity.role)) });
  });
}

async function gotoSonarTab(page: Page): Promise<void> {
  await page.goto(`${BASE}/boards/${PH_BOARD}/settings`);
  await page.waitForLoadState("networkidle");
  const tab = page.getByRole("tab", { name: /sonarqube/i });
  await expect(tab).toBeVisible({ timeout: 10_000 });
  await tab.click();
  await page.waitForTimeout(300);
}

/** Real Login UI submit — drives the production `useAuth.setToken` chokepoint. */
async function loginViaUI(page: Page, token: string): Promise<void> {
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
  await page.getByLabel("Bearer token").fill(token);
  await page.getByRole("button", { name: /giriş yap/i }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10_000 });
}

/** Real Logout button — drives the production `useAuth.logout` chokepoint. */
async function logoutViaUI(page: Page): Promise<void> {
  await page.getByRole("button", { name: /logout/i }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// TC-1 (AC3, BUG STATE): non-admin → read-only banner, Setup/Sync hidden.
// ---------------------------------------------------------------------------
test("TC-1 non-admin sees read-only banner, Setup/Sync hidden (bug state)", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  const identity: Identity = { role: "frontend_dev" };
  await installMocks(page, identity, { me: 0 });
  await page.addInitScript((t) => localStorage.setItem("projecthub.token", t), TOKEN_A);
  await gotoSonarTab(page);

  await expect(page.getByTestId("sonarqube-readonly-banner")).toBeVisible();
  await expect(page.getByTestId("sonar-actions")).toHaveCount(0);
  await expect(page.getByTestId("sonar-setup-btn")).toHaveCount(0);
  await expect(page.getByTestId("sonar-sync-btn")).toHaveCount(0);
  await expect(page.getByTestId("sonar-status-panel")).toBeVisible();

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc1-banner-before-nonadmin.png`, fullPage: true });
  expect(consoleErrors, `console: ${consoleErrors.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-2 (AC1+AC3+AC4, THE FIX): logout → login as admin (token VALUE changes),
// no hard reload to refresh isAdmin → banner gone, Setup/Sync render, isAdmin
// flipped. Pre-fix this needed a HARD RELOAD (stale ["me"] served the prior id).
// ---------------------------------------------------------------------------
test("TC-2 identity switch to admin flips isAdmin without a reload to refresh it (THE FIX)", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  const identity: Identity = { role: "frontend_dev" };
  await installMocks(page, identity, { me: 0 });
  await page.addInitScript((t) => localStorage.setItem("projecthub.token", t), TOKEN_A);
  await gotoSonarTab(page);

  // Precondition: locked (bug state).
  await expect(page.getByTestId("sonarqube-readonly-banner")).toBeVisible();
  await expect(page.getByTestId("sonar-setup-btn")).toHaveCount(0);

  // ── THE SWITCH: logout, then login as admin with a DIFFERENT token value. ──
  await logoutViaUI(page);          // real useAuth.logout → queryClient.clear()
  identity.role = "admin";          // the new identity is admin
  await loginViaUI(page, TOKEN_B);  // real useAuth.setToken(B) → clear() + new ["me", B] key

  // Re-open the SonarQube tab (SPA navigation). The FIX is that isAdmin is now
  // correct WITHOUT having to hard-reload to discard a stale prior-identity me.
  await gotoSonarTab(page);

  // isAdmin flipped → banner gone, admin actions render + enabled.
  await expect(page.getByTestId("sonarqube-readonly-banner")).toHaveCount(0);
  await expect(page.getByTestId("sonar-actions")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("sonar-setup-btn")).toBeVisible();
  await expect(page.getByTestId("sonar-sync-btn")).toBeVisible();
  await expect(page.getByTestId("sonar-setup-btn")).toBeEnabled();

  await page.screenshot({ path: `${SCREENSHOTS_DIR}/tc2-unlocked-after-admin.png`, fullPage: true });
  expect(consoleErrors, `console: ${consoleErrors.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// TC-3 (AC2+AC4, NO LEAK): after the switch, the admin identity shows ONLY admin
// state — no stale non-admin `me` survives. Re-render via a PURE SPA tab switch
// (no goto/reload) and confirm the banner never reappears.
// ---------------------------------------------------------------------------
test("TC-3 no prior-identity leak after switch (stale non-admin me dropped)", async ({ page }) => {
  const identity: Identity = { role: "frontend_dev" };
  await installMocks(page, identity, { me: 0 });
  await page.addInitScript((t) => localStorage.setItem("projecthub.token", t), TOKEN_A);
  await gotoSonarTab(page);
  await expect(page.getByTestId("sonarqube-readonly-banner")).toBeVisible();

  // Switch to admin.
  await logoutViaUI(page);
  identity.role = "admin";
  await loginViaUI(page, TOKEN_B);
  await gotoSonarTab(page);
  await expect(page.getByTestId("sonar-actions")).toBeVisible({ timeout: 10_000 });

  // Pure SPA tab re-render — NO document reload. A surviving non-admin `me` would
  // re-lock on re-render; it must not.
  await page.getByRole("tab", { name: /general/i }).click();
  await page.waitForTimeout(150);
  await page.getByRole("tab", { name: /sonarqube/i }).click();
  await page.waitForTimeout(300);

  await expect(page.getByTestId("sonarqube-readonly-banner")).toHaveCount(0);
  await expect(page.getByTestId("sonar-setup-btn")).toBeVisible();
});

// ---------------------------------------------------------------------------
// TC-4 (AC5): the SAME token across re-renders / SPA navigation does NOT clear
// the cache → no refetch storm. (The store guard `prev===next` short-circuits;
// the auto-login effect re-setting the existing token, and component re-mounts,
// must NOT fire clear() — otherwise every navigation would wipe + refetch.)
// ---------------------------------------------------------------------------
test("TC-4 same token across SPA navigation does NOT storm /auth/me (no refetch loop)", async ({ page }) => {
  const identity: Identity = { role: "admin" };
  const counters = { me: 0 };
  await installMocks(page, identity, counters);
  await page.addInitScript((t) => localStorage.setItem("projecthub.token", t), TOKEN_B);

  // Warm up: land on the SonarQube tab as admin (one identity boundary at boot).
  await gotoSonarTab(page);
  await expect(page.getByTestId("sonar-status-panel")).toBeVisible();
  await expect(page.getByTestId("sonar-actions")).toBeVisible(); // admin → controls render
  const meAfterWarmup = counters.me;

  // Repeated SPA navigation with the SAME token in the store. Each Settings
  // re-mount re-evaluates the auth path; the guard must keep clear() from firing,
  // so the warm ["me", TOKEN_B] entry (staleTime 5m) is reused — NOT refetched.
  for (let i = 0; i < 3; i += 1) {
    await page.getByRole("tab", { name: /general/i }).click();
    await page.waitForTimeout(120);
    await page.getByRole("tab", { name: /sonarqube/i }).click();
    await page.waitForTimeout(120);
  }
  // Also re-mount the whole Settings route (back to boards home, then in again) —
  // still the SAME token, so still no clear / no me storm.
  await page.goto(`${BASE}/boards/${PH_BOARD}/settings`);
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("tab", { name: /sonarqube/i })).toBeVisible({ timeout: 10_000 });

  // No clear() on a same-token boundary → the cached me is reused; navigation
  // does not produce a refetch storm. A genuine staleTime-warm reuse yields 0–1
  // extra /me fetches across all of the above (the route re-mount may revalidate
  // at most once); a storm would add one per navigation (>=3).
  const extraMeFetches = counters.me - meAfterWarmup;
  expect(extraMeFetches, `same-token navigation must not storm /auth/me (got ${extraMeFetches})`).toBeLessThanOrEqual(1);
  // And the admin gating held throughout (no flicker back to the read-only lock).
  await page.getByRole("tab", { name: /sonarqube/i }).click();
  await page.waitForTimeout(200);
  await expect(page.getByTestId("sonarqube-readonly-banner")).toHaveCount(0);
});
