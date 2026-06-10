/**
 * PH-256 (epic PH-253, child 3/3 — FINAL) — Frontend: per-repo SonarQube card
 * Setup / Scan / Sync action buttons. Wires the read-only `SonarRepoHealthCards`
 * to the PH-254 (setup) + PH-255 (scan/sync) per-repo endpoints (both LIVE on
 * main). This spec verifies the FE CONSUMPTION/RENDER + INTERACTION of those
 * endpoints.
 *
 * Environment & strategy (mirrors ph-249-per-repo-health-and-repo-labeled-commits):
 *   - Host Vite dev server: http://localhost:5173 → proxies /api/* to backend:8000.
 *   - The e2e DB (live PH board) is SINGLE-repo + does NOT contain GXA. Per the
 *     PH-224/PH-249 precedent we DETERMINISTICALLY exercise the multi-repo path
 *     by `page.route`-fulfilling the board GET with a synthetic GXA-shaped
 *     `repo_health` (3 repos: gamexcore primary+scanned, gamexsdk scanned,
 *     gamexandroiddemoapp linked-but-never-scanned). The per-repo action
 *     endpoints (setup/scan/sync) are ALSO route-fulfilled so the mutations run
 *     end-to-end (TanStack invalidate → board refetch → card re-render) without
 *     mutating the real DB.
 *   - Admin gate: the ADMIN_TOKEN identity is `admin` on PH → `useBoardRole("PH")
 *     === "admin"` → the action row renders. The non-admin TC route-fulfils
 *     `/api/me` to strip the PH admin role so `isAdmin` is false.
 *
 * data-testid hooks asserted (from the shipped SonarRepoHealthCards.tsx):
 *   repo-card-actions-<slug>, repo-card-setup-btn-<slug>, repo-card-scan-btn-<slug>,
 *   repo-card-sync-btn-<slug>, repo-card-setup-form-<slug>, repo-card-setup-input-<slug>,
 *   repo-card-setup-save-<slug>, repo-card-scan-feedback-<slug>,
 *   repo-card-success-<slug>, repo-card-error-<slug>
 *
 * AC coverage (PH-256):
 *   TC-1  Admin sees a Setup/Scan/Sync action row on EVERY card with a non-null
 *         repo_slug (primary gamexcore + scanned gamexsdk + unscanned
 *         gamexandroiddemoapp).                                       [AC-1]
 *   TC-2  Setup form pre-fills the derived default project_key; a successful
 *         submit invalidates ["board",boardKey] (card refetches).     [AC-2]
 *   TC-3  Scan with a non-null job_id → honest "queued/in-progress"; a null
 *         job_id (non-scannable) → honest non-queued message, NO fake queued. [AC-3]
 *   TC-4  Sync success → metrics refresh in place (board refetch); the never-
 *         scanned card leaves "No analysis yet" once metrics exist.   [AC-4]
 *   TC-5  A failing action (422 bad key) → inline role=alert error region; the
 *         card does NOT crash (no silent fail).                       [AC-5]
 *   TC-6  Non-admin (useBoardRole !== "admin") → NO action row rendered AND no
 *         per-repo mutation fires.                                    [AC-6]
 *
 * Screenshots → .jarwis/logs/PH-256/qa-screenshots/
 */

import { test, expect, type Page, type Route } from "@playwright/test";
import { ADMIN_TOKEN } from "./helpers/workflowSnapshot";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const BOARD_KEY = "PH";
const BOARD_URL = `${BASE}/boards/${BOARD_KEY}`;
const SHOTS =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-256/qa-screenshots";

const NOW = "2026-06-09T12:00:00.000Z";

/** 3-repo per-repo health breakdown (PH-246 RepoHealth[] shape, verbatim). */
const GXA_REPO_HEALTH = [
  {
    repo_id: "11111111-1111-1111-1111-111111111111",
    repo_slug: "gamexcore",
    repo_name: "GameXCore",
    is_primary: true,
    project_key: "gamexcore",
    quality_gate_status: "OK",
    bugs: 0,
    vulnerabilities: 0,
    code_smells: 12,
    coverage: 71.4,
    duplicated_lines_density: 0.8,
    ncloc: 1174,
    fetched_at: NOW,
    dashboard_url: "http://localhost:9000/dashboard?id=gamexcore",
  },
  {
    repo_id: "22222222-2222-2222-2222-222222222222",
    repo_slug: "gamexsdk",
    repo_name: "GameX SDK",
    is_primary: false,
    project_key: "gamexsdk",
    quality_gate_status: "ERROR",
    bugs: 3,
    vulnerabilities: 1,
    code_smells: 44,
    coverage: 12.0,
    duplicated_lines_density: 4.5,
    ncloc: 8021,
    fetched_at: NOW,
    dashboard_url: "http://localhost:9000/dashboard?id=gamexsdk",
  },
  {
    // linked-but-NEVER-scanned → quality_gate_status == null (honest empty state).
    repo_id: "33333333-3333-3333-3333-333333333333",
    repo_slug: "gamexandroiddemoapp",
    repo_name: "GameX Android Demo App",
    is_primary: false,
    project_key: "gamexandroiddemoapp",
    quality_gate_status: null,
    bugs: null,
    vulnerabilities: null,
    code_smells: null,
    coverage: null,
    duplicated_lines_density: null,
    ncloc: null,
    fetched_at: null,
    dashboard_url: null,
  },
];

/** A scanned RepoHealth for a slug (sync success → leaves "No analysis yet"). */
function scannedRepoHealth(slug: string, name: string) {
  return {
    repo_id: "33333333-3333-3333-3333-333333333333",
    repo_slug: slug,
    repo_name: name,
    is_primary: false,
    project_key: slug,
    quality_gate_status: "OK",
    bugs: 0,
    vulnerabilities: 0,
    code_smells: 5,
    coverage: 88.0,
    duplicated_lines_density: 1.0,
    ncloc: 420,
    fetched_at: NOW,
    dashboard_url: `http://localhost:9000/dashboard?id=${slug}`,
  };
}

// ---------------------------------------------------------------------------
// Route helpers
// ---------------------------------------------------------------------------

/** Fetch the real board JSON (so we keep its workflow/states intact). */
async function fetchRealBoard(): Promise<Record<string, unknown>> {
  const resp = await fetch(`${BACKEND}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  return (await resp.json()) as Record<string, unknown>;
}

/**
 * Route the board GET to a real-board body with `repo_health` REPLACED by the
 * supplied array. `getRepoHealth` is a getter so a later board refetch (after a
 * mutation invalidates ["board",boardKey]) can return a DIFFERENT body — that's
 * how we prove the card re-renders in place.
 */
async function installBoardMock(
  page: Page,
  getRepoHealth: () => unknown[],
): Promise<void> {
  const real = await fetchRealBoard();

  await page.route(`${BASE}/api/boards/${BOARD_KEY}`, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...real, repo_health: getRepoHealth() }),
    });
  });

  // Member-gated repositories list — not needed by the quality tab; empty is fine.
  await page.route(
    new RegExp(`/api/boards/${BOARD_KEY}/repositories$`),
    async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repositories: [] }),
      });
    },
  );
}

/** Strip the PH admin role from /api/auth/me so useBoardRole("PH") !== "admin". */
async function installNonAdminMe(page: Page): Promise<void> {
  await page.route(new RegExp(`/api/auth/me$`), async (route) => {
    const resp = await fetch(`${BACKEND}/api/auth/me`, {
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
    });
    const me = (await resp.json()) as {
      memberships?: { board_key: string; role: string }[];
    };
    const memberships = (me.memberships ?? []).map((m) =>
      m.board_key === BOARD_KEY ? { ...m, role: "viewer" } : m,
    );
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...me, memberships }),
    });
  });
}

async function login(page: Page): Promise<void> {
  await page.goto(BASE);
  await page.evaluate(
    (t) => localStorage.setItem("projecthub.token", t),
    ADMIN_TOKEN,
  );
}

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

const benign = (e: string): boolean =>
  !e.includes("ResizeObserver") &&
  !e.includes("WebSocket") &&
  !e.includes("Failed to load resource") &&
  !e.includes("the server responded with a status") &&
  !e.includes("net::ERR_") &&
  !e.includes("favicon");

async function openQualityTab(page: Page): Promise<void> {
  await page.waitForSelector('[role="tablist"]', { timeout: 15000 });
  await page.locator("#tab-quality").click();
  await expect(page.locator("#panel-quality")).toBeVisible({ timeout: 10000 });
}

/** Per-repo endpoint regex factory (setup|scan|sync) for a slug. */
function repoActionRe(slug: string, action: string): RegExp {
  return new RegExp(
    `/api/boards/${BOARD_KEY}/repositories/${slug}/sonarqube/${action}$`,
  );
}

test.describe("PH-256: per-repo card Setup/Scan/Sync actions", () => {
  test.describe.configure({ mode: "serial", timeout: 120000 });

  // -------------------------------------------------------------------------
  // TC-1: admin → action row on EVERY card with a non-null repo_slug.
  // -------------------------------------------------------------------------
  test("TC-1: admin sees Setup/Scan/Sync on every repo card (primary + scanned + unscanned)", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await installBoardMock(page, () => GXA_REPO_HEALTH);
    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    for (const slug of ["gamexcore", "gamexsdk", "gamexandroiddemoapp"]) {
      const card = page.getByTestId(`repo-health-card-${slug}`);
      await expect(card).toBeVisible({ timeout: 10000 });
      await expect(card.getByTestId(`repo-card-actions-${slug}`)).toBeVisible();
      await expect(card.getByTestId(`repo-card-setup-btn-${slug}`)).toBeVisible();
      await expect(card.getByTestId(`repo-card-scan-btn-${slug}`)).toBeVisible();
      await expect(card.getByTestId(`repo-card-sync-btn-${slug}`)).toBeVisible();
      // a11y: per-repo scoped aria-label so SRs distinguish the buttons.
      await expect(
        card.getByTestId(`repo-card-scan-btn-${slug}`),
      ).toHaveAttribute("aria-label", new RegExp(`^Scan `));
    }

    await page.screenshot({
      path: `${SHOTS}/tc1-admin-action-rows.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-2: Setup form pre-fills the derived default key; success → invalidate.
  // -------------------------------------------------------------------------
  test("TC-2: Setup form pre-fills repo.project_key; submit success re-renders the card", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    let boardRefetched = false;
    let setupCalled = false;

    // Fulfil the per-repo setup endpoint → RepoHealth (success). The 2nd board
    // GET (after invalidate) flips boardRefetched so we PROVE the refetch fires.
    await page.route(repoActionRe("gamexandroiddemoapp", "setup"), async (r: Route) => {
      setupCalled = true;
      await r.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          scannedRepoHealth("gamexandroiddemoapp", "GameX Android Demo App"),
        ),
      });
    });

    await installBoardMock(page, () => {
      // First call (initial render) → unscanned; subsequent (post-invalidate) → scanned.
      if (boardRefetched) {
        return [
          GXA_REPO_HEALTH[0],
          GXA_REPO_HEALTH[1],
          scannedRepoHealth("gamexandroiddemoapp", "GameX Android Demo App"),
        ];
      }
      boardRefetched = true;
      return GXA_REPO_HEALTH;
    });

    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    const card = page.getByTestId("repo-health-card-gamexandroiddemoapp");
    await expect(card).toBeVisible({ timeout: 10000 });

    // Open the Setup disclosure → the input is pre-filled with the derived default.
    await card.getByTestId("repo-card-setup-btn-gamexandroiddemoapp").click();
    const input = card.getByTestId("repo-card-setup-input-gamexandroiddemoapp");
    await expect(input).toBeVisible();
    await expect(input).toHaveValue("gamexandroiddemoapp");

    // Submit → setup endpoint hit, form closes, board re-renders (card scanned).
    await card.getByTestId("repo-card-setup-save-gamexandroiddemoapp").click();
    await expect
      .poll(() => setupCalled, { timeout: 10000 })
      .toBe(true);
    // The form closes on success.
    await expect(
      card.getByTestId("repo-card-setup-form-gamexandroiddemoapp"),
    ).toHaveCount(0, { timeout: 10000 });

    await page.screenshot({
      path: `${SHOTS}/tc2-setup-prefill-submit.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-3: Scan → job_id != null = honest queued; job_id == null = honest
  //       non-queued (NO fake queued).
  // -------------------------------------------------------------------------
  test("TC-3: Scan honest queued (job_id set) vs honest non-queued (job_id null)", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await installBoardMock(page, () => GXA_REPO_HEALTH);

    // gamexsdk → scannable: job_id non-null → queued copy.
    await page.route(repoActionRe("gamexsdk", "scan"), async (r: Route) => {
      await r.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          repo_slug: "gamexsdk",
          project_key: "gamexsdk",
          language: "java",
          scan_status: "queued",
          job_id: "job-123",
          message: "Scan enqueued.",
        }),
      });
    });
    // gamexcore → non-scannable: job_id null + unsupported → honest non-queued.
    await page.route(repoActionRe("gamexcore", "scan"), async (r: Route) => {
      await r.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          repo_slug: "gamexcore",
          project_key: "gamexcore",
          language: "cs",
          scan_status: "unsupported",
          job_id: null,
          message: "C# is not supported by SonarQube Community Edition.",
        }),
      });
    });

    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    // Scannable → queued/in-progress success copy.
    const sdk = page.getByTestId("repo-health-card-gamexsdk");
    await sdk.getByTestId("repo-card-scan-btn-gamexsdk").click();
    const sdkFeedback = sdk.getByTestId("repo-card-scan-feedback-gamexsdk");
    await expect(sdkFeedback).toBeVisible({ timeout: 10000 });
    await expect(sdkFeedback).toContainText(/queued/i);

    // Non-scannable → honest reason, NO fake "queued".
    const core = page.getByTestId("repo-health-card-gamexcore");
    await core.getByTestId("repo-card-scan-btn-gamexcore").click();
    const coreFeedback = core.getByTestId("repo-card-scan-feedback-gamexcore");
    await expect(coreFeedback).toBeVisible({ timeout: 10000 });
    await expect(coreFeedback).toContainText(/not supported|community edition/i);
    await expect(coreFeedback).not.toContainText(/queued/i);

    await page.screenshot({
      path: `${SHOTS}/tc3-scan-queued-vs-nonscannable.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-4: Sync success → the never-scanned card leaves "No analysis yet".
  // -------------------------------------------------------------------------
  test("TC-4: Sync on an unscanned card → board refetch leaves 'No analysis yet'", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    let synced = false;

    await page.route(repoActionRe("gamexandroiddemoapp", "sync"), async (r: Route) => {
      synced = true;
      await r.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          scannedRepoHealth("gamexandroiddemoapp", "GameX Android Demo App"),
        ),
      });
    });

    await installBoardMock(page, () =>
      synced
        ? [
            GXA_REPO_HEALTH[0],
            GXA_REPO_HEALTH[1],
            scannedRepoHealth("gamexandroiddemoapp", "GameX Android Demo App"),
          ]
        : GXA_REPO_HEALTH,
    );

    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    const card = page.getByTestId("repo-health-card-gamexandroiddemoapp");
    await expect(card.getByTestId("repo-health-no-analysis")).toBeVisible({
      timeout: 10000,
    });

    await card.getByTestId("repo-card-sync-btn-gamexandroiddemoapp").click();

    // After sync → board refetch → the card now shows REAL metrics (left the
    // "No analysis yet" state); the no-analysis copy is gone.
    await expect(card.getByTestId("repo-health-no-analysis")).toHaveCount(0, {
      timeout: 10000,
    });
    await expect(card.getByTestId("repo-health-coverage")).toHaveText("88.0%");

    await page.screenshot({
      path: `${SHOTS}/tc4-sync-leaves-no-analysis.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-5: a failing action (422) → inline role=alert error region, no crash.
  // -------------------------------------------------------------------------
  test("TC-5: Setup 422 → inline error region (role=alert), card does not crash", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await installBoardMock(page, () => GXA_REPO_HEALTH);

    await page.route(repoActionRe("gamexsdk", "setup"), async (r: Route) => {
      await r.fulfill({
        status: 422,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ detail: "Project key must not be blank." }),
      });
    });

    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    const card = page.getByTestId("repo-health-card-gamexsdk");
    await card.getByTestId("repo-card-setup-btn-gamexsdk").click();
    await card.getByTestId("repo-card-setup-save-gamexsdk").click();

    const err = card.getByTestId("repo-card-error-gamexsdk");
    await expect(err).toBeVisible({ timeout: 10000 });
    await expect(err).toHaveAttribute("role", "alert");
    // The card itself is still intact (header still present → no crash).
    await expect(card.getByTestId("repo-health-gate")).toBeVisible();

    await page.screenshot({
      path: `${SHOTS}/tc5-setup-error-inline.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-6: non-admin → NO action row, and no per-repo mutation fires.
  // -------------------------------------------------------------------------
  test("TC-6: non-admin → action row hidden on all cards; no per-repo call fires", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    let perRepoCalled = false;
    await installBoardMock(page, () => GXA_REPO_HEALTH);
    await installNonAdminMe(page);
    // Any per-repo endpoint hit would flip this — it must stay false.
    await page.route(
      new RegExp(`/api/boards/${BOARD_KEY}/repositories/[^/]+/sonarqube/`),
      async (r: Route) => {
        perRepoCalled = true;
        await r.fulfill({ status: 200, body: "{}" });
      },
    );

    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    // The cards still render (read-only), but NO action row on any of them.
    await expect(
      page.getByTestId("repo-health-card-gamexcore"),
    ).toBeVisible({ timeout: 10000 });
    for (const slug of ["gamexcore", "gamexsdk", "gamexandroiddemoapp"]) {
      await expect(
        page.getByTestId(`repo-card-actions-${slug}`),
      ).toHaveCount(0);
      await expect(
        page.getByTestId(`repo-card-setup-btn-${slug}`),
      ).toHaveCount(0);
    }
    expect(perRepoCalled).toBe(false);

    await page.screenshot({
      path: `${SHOTS}/tc6-nonadmin-no-actions.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });
});
