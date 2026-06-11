/**
 * PH-249 (epic PH-245, child D — FINAL) — Frontend: per-repo SonarQube health
 * cards + repo-labeled git activity (consuming PH-246 `repo_health` + PH-247
 * commit tagging).
 *
 * This spec closes the AC's missing-spec gap:
 *   "the existing Playwright e2e suite runs ... and a new/updated spec asserts
 *    the GXA multi-repo health cards + repo-labeled commit rows render."
 *
 * Environment & strategy (mirrors ph-224-repo-switcher.spec.ts):
 *   - Host Vite dev server: http://localhost:5173 → proxies /api/* to backend:8000.
 *   - The e2e DB (live PH board) is SINGLE-repo (its `repo_health` has exactly one
 *     entry, `repositories` is empty) — it does NOT contain GXA. Per the PH-224
 *     precedent we DETERMINISTICALLY exercise the multi-repo render path by
 *     `page.route`-fulfilling the board's two driving endpoints with a synthetic
 *     GXA-shaped body (3 repos: gamexcore primary + scanned, gamexsdk scanned,
 *     gamexandroiddemoapp linked-but-never-scanned). This asserts the FE
 *     CONSUMPTION/RENDER of the already-shipped backend surface without depending
 *     on GXA being seeded in the e2e DB.
 *   - The single-repo NO-REGRESSION path uses the REAL PH board (repo_health
 *     stripped to empty) → asserts the per-repo grid does NOT mount and the
 *     existing SonarDashboard still renders.
 *   - Repo-labeled commit rows + the non-primary `?repo=` drill-down are asserted
 *     against a real PH ticket (PH-161, 6 commits) whose `/commits` response is
 *     route-fulfilled with multi-repo-tagged rows; the commit drill-down git call
 *     is forwarded to the real backend AND inspected for the `?repo=<slug>` param.
 *
 * data-testid hooks asserted (grepped from the shipped components):
 *   SonarRepoHealthCards.tsx:
 *     sonar-repo-health-cards, repo-health-card-<slug>, repo-health-primary-badge,
 *     repo-health-gate, repo-health-no-analysis, repo-health-{bugs,vulnerabilities,
 *     code-smells,coverage,duplications,ncloc}, repo-health-dashboard-link,
 *     repo-health-fetched-at
 *   TicketCommits.tsx:
 *     ticket-commit-repo-badge
 *
 * AC coverage:
 *   TC-1  Multi-repo: 3 per-repo cards render from repo_health[]; gamexcore card
 *         primary-badged (others not); per-card gate pill + 6 metric tiles +
 *         dashboard deep link.                          [per-repo cards / primary]
 *   TC-2  Never-scanned repo (quality_gate_status==null) → honest "no analysis
 *         yet" + em-dash metrics, NO crash, NO fake-zero card.   [honest empty]
 *   TC-3  Single-repo no-regression: repo_health==[] → NO per-repo grid; the
 *         existing SonarDashboard still renders.                 [no regression]
 *   TC-4  Repo-labeled commit rows: each row shows a repo badge (repo_slug);
 *         rows from ≥2 repos appear (aggregated).        [repo-labeled commits]
 *   TC-5  Non-primary drill-down: expanding a gamexsdk commit issues the commit
 *         git call with `?repo=gamexsdk` and the file list opens WITHOUT the
 *         "Dosyalar yüklenemedi" 404 banner.                     [drill-down ?repo=]
 *
 * Screenshots → .jarwis/logs/PH-249/qa-screenshots/
 */

import { test, expect, type Page, type Route } from "@playwright/test";
import { ADMIN_TOKEN } from "./helpers/workflowSnapshot";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const BOARD_KEY = "PH";
const BOARD_URL = `${BASE}/boards/${BOARD_KEY}`;
const COMMIT_TICKET = "PH-161"; // a real PH ticket with ≥1 commit
const TICKET_URL = `${BASE}/boards/${BOARD_KEY}/tickets/${COMMIT_TICKET}`;
const SHOTS =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-249/qa-screenshots";

// ---------------------------------------------------------------------------
// Synthetic GXA-shaped fixtures (the e2e DB has no GXA; we route-fulfill).
// ---------------------------------------------------------------------------

const NOW = "2026-06-09T12:00:00.000Z";

/** 3-repo per-repo health breakdown (PH-246 RepoHealth[] shape, verbatim). */
const GXA_REPO_HEALTH = [
  {
    repo_id: "11111111-1111-1111-1111-111111111111",
    repo_slug: "gamexcore",
    repo_name: "GameXCore",
    // PH-251: is_primary now lives on the un-gated repo_health[] surface and
    // DRIVES the per-repo `primary` badge (no longer the member-gated
    // /repositories-derived primarySlug). gamexcore is the GXA primary.
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
    fetched_at: null, // PH-252: linked-but-never-scanned → null freshness (honest wire shape)
    dashboard_url: null,
  },
];

/** 3 repositories so BoardDetail computes primarySlug = gamexcore. */
function gxaRepositories(boardId: string) {
  const base = {
    board_id: boardId,
    provider: "local",
    remote_url: null,
    last_synced_sha: null,
    last_synced_at: null,
    default_branch: "main",
    created_at: NOW,
    updated_at: NOW,
  };
  return {
    repositories: [
      {
        ...base,
        id: "11111111-1111-1111-1111-111111111111",
        slug: "gamexcore",
        name: "GameXCore",
        is_primary: true,
        local_path: "/repos/gamexcore",
      },
      {
        ...base,
        id: "22222222-2222-2222-2222-222222222222",
        slug: "gamexsdk",
        name: "GameX SDK",
        is_primary: false,
        local_path: "/repos/gamexsdk",
      },
      {
        ...base,
        id: "33333333-3333-3333-3333-333333333333",
        slug: "gamexandroiddemoapp",
        name: "GameX Android Demo App",
        is_primary: false,
        local_path: "/repos/gamexandroiddemoapp",
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Route helpers
// ---------------------------------------------------------------------------

/** Forward a request straight through to the real backend (proxy bypass). */
async function forward(route: Route): Promise<void> {
  const req = route.request();
  const url = req.url().replace(BASE, BACKEND);
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
}

/** Fetch the real board JSON (so we keep its workflow/states intact). */
async function fetchRealBoard(): Promise<Record<string, unknown>> {
  const resp = await fetch(`${BACKEND}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  return (await resp.json()) as Record<string, unknown>;
}

/**
 * Route the board GET to a real-board body with `repo_health` REPLACED by the
 * supplied array, and the repositories list to a synthetic body. Everything
 * else (workflow, tickets meta, health) stays real → the page renders fully.
 *
 * `repos` controls the member-gated `/repositories` fulfilment:
 *   "multi" → the 3 GXA repos (drives the #graph branch switcher),
 *   "empty" → empty list (single-repo / no per-repo rows),
 *   "403"   → PermissionDenied, the PH-251 root cause — a non-member viewer.
 * Post-PH-251 the quality-tab `primary` badge is driven by repo_health[].is_primary,
 * so it must render identically under ALL three /repositories outcomes.
 */
async function installBoardMock(
  page: Page,
  repoHealth: unknown[],
  repos: "multi" | "empty" | "403",
): Promise<void> {
  const real = await fetchRealBoard();
  const boardId = String(real["id"] ?? "");
  const patchedBoard = { ...real, repo_health: repoHealth };

  // Board GET — exact /api/boards/PH (NOT the nested /repositories or /git/*).
  await page.route(`${BASE}/api/boards/${BOARD_KEY}`, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patchedBoard),
    });
  });

  // Member-gated repositories list — feeds the #graph branch switcher only.
  await page.route(
    new RegExp(`/api/boards/${BOARD_KEY}/repositories$`),
    async (route) => {
      if (repos === "403") {
        // The PH-251 root cause: a non-member authorized viewer → 403.
        await route.fulfill({
          status: 403,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ detail: "Not a board member" }),
        });
        return;
      }
      const body =
        repos === "multi" ? gxaRepositories(boardId) : { repositories: [] };
      await route.fulfill({
        status: 200,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    },
  );
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

test.describe("PH-249: per-repo health cards + repo-labeled git activity", () => {
  test.describe.configure({ mode: "serial", timeout: 90000 });

  // -------------------------------------------------------------------------
  // TC-1: multi-repo → 3 per-repo cards; gamexcore primary-badged; gate pills,
  //       6 metric tiles, dashboard deep link.
  // -------------------------------------------------------------------------
  test("TC-1: GXA repo_health[] → 3 per-repo cards, gamexcore primary, gates+metrics+deep-link", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    // PH-251: /repositories 403s (non-member viewer) — the badge MUST still render
    // because it is now driven by repo_health[].is_primary, NOT the gated /repositories.
    await installBoardMock(page, GXA_REPO_HEALTH, "403");
    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    // The per-repo grid container mounts (repo_health.length > 0).
    const grid = page.getByTestId("sonar-repo-health-cards");
    await expect(grid).toBeVisible({ timeout: 10000 });

    // Exactly 3 cards — one per repo_health entry.
    const core = page.getByTestId("repo-health-card-gamexcore");
    const sdk = page.getByTestId("repo-health-card-gamexsdk");
    const demo = page.getByTestId("repo-health-card-gamexandroiddemoapp");
    await expect(core).toBeVisible();
    await expect(sdk).toBeVisible();
    await expect(demo).toBeVisible();

    // PH-251 regression: EXACTLY the gamexcore card carries the primary badge —
    // and it does so EVEN THOUGH /repositories 403'd above (badge sourced from
    // repo_health[].is_primary, not the now-403 /repositories-derived primarySlug).
    await expect(
      core.getByTestId("repo-health-primary-badge"),
    ).toBeVisible();
    await expect(sdk.getByTestId("repo-health-primary-badge")).toHaveCount(0);
    await expect(demo.getByTestId("repo-health-primary-badge")).toHaveCount(0);

    // Per-card quality-gate pill.
    await expect(core.getByTestId("repo-health-gate")).toBeVisible();
    await expect(sdk.getByTestId("repo-health-gate")).toBeVisible();

    // gamexcore metric tiles carry its OWN values (not the board primary's).
    await expect(core.getByTestId("repo-health-code-smells")).toHaveText("12");
    await expect(core.getByTestId("repo-health-coverage")).toHaveText("71.4%");
    await expect(core.getByTestId("repo-health-ncloc")).toHaveText("1174");
    await expect(core.getByTestId("repo-health-bugs")).toHaveText("0");

    // gamexsdk has its own (different) metrics — proves per-repo, not duplicated.
    await expect(sdk.getByTestId("repo-health-bugs")).toHaveText("3");
    await expect(sdk.getByTestId("repo-health-vulnerabilities")).toHaveText("1");
    await expect(sdk.getByTestId("repo-health-ncloc")).toHaveText("8021");

    // Per-repo dashboard deep link (host-facing, target=_blank rel=noopener).
    const coreLink = core.getByTestId("repo-health-dashboard-link");
    await expect(coreLink).toBeVisible();
    await expect(coreLink).toHaveAttribute("target", "_blank");
    await expect(coreLink).toHaveAttribute("rel", /noopener/);
    await expect(coreLink).toHaveAttribute(
      "href",
      "http://localhost:9000/dashboard?id=gamexcore",
    );

    await page.screenshot({
      path: `${SHOTS}/tc1-gxa-three-cards.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-2: never-scanned repo → honest "no analysis yet" + em-dash metrics, no
  //       fake-zero card, no crash.
  // -------------------------------------------------------------------------
  test("TC-2: never-scanned repo card → honest 'no analysis yet' + em-dash metrics, no fake-zero", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await installBoardMock(page, GXA_REPO_HEALTH, "multi");
    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    const demo = page.getByTestId("repo-health-card-gamexandroiddemoapp");
    await expect(demo).toBeVisible({ timeout: 10000 });

    // Honest never-scanned copy is present (NOT a fabricated all-zero grid).
    await expect(demo.getByTestId("repo-health-no-analysis")).toBeVisible();
    await expect(demo.getByTestId("repo-health-no-analysis")).toContainText(
      /run a scan to populate metrics/i,
    );
    // Gate pill reads "No analysis yet" (null gate), not a fake OK/ERROR.
    await expect(demo.getByTestId("repo-health-gate")).toContainText(
      /no analysis yet/i,
    );
    // Metric tiles show the em-dash NO-DATA sentinel — NOT a real "0".
    await expect(demo.getByTestId("repo-health-bugs")).toHaveText("—");
    await expect(demo.getByTestId("repo-health-coverage")).toHaveText("—");
    await expect(demo.getByTestId("repo-health-ncloc")).toHaveText("—");
    // A never-scanned repo with dashboard_url==null shows NO deep link.
    await expect(demo.getByTestId("repo-health-dashboard-link")).toHaveCount(0);
    // No freshness stamp on a never-scanned card.
    await expect(demo.getByTestId("repo-health-fetched-at")).toHaveCount(0);

    // Sanity: a SCANNED card DOES carry real integer values + a fresh-stamp.
    const sdk = page.getByTestId("repo-health-card-gamexsdk");
    await expect(sdk.getByTestId("repo-health-fetched-at")).toBeVisible();
    await expect(sdk.getByTestId("repo-health-bugs")).not.toHaveText("—");

    await page.screenshot({
      path: `${SHOTS}/tc2-never-scanned-honest-empty.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-3: single-repo no-regression → repo_health==[] → NO per-repo grid; the
  //       existing SonarDashboard still renders.
  // -------------------------------------------------------------------------
  test("TC-3: single-repo board (repo_health==[]) → NO per-repo grid; SonarDashboard intact", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    // repo_health empty → the per-repo cards must NOT mount (Risk R3 / AC4).
    await installBoardMock(page, [], "empty");
    await login(page);
    await page.goto(BOARD_URL);
    await openQualityTab(page);

    // The per-repo grid is absent (no empty/duplicate grid).
    await expect(page.getByTestId("sonar-repo-health-cards")).toHaveCount(0);
    await expect(page.getByTestId("repo-health-card-gamexcore")).toHaveCount(0);

    // The existing single-repo SonarDashboard still renders (zero regression).
    await expect(page.getByTestId("sonar-dashboard")).toBeVisible({
      timeout: 10000,
    });

    await page.screenshot({
      path: `${SHOTS}/tc3-single-repo-no-grid.png`,
      fullPage: true,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-4: repo-labeled commit rows — each row shows a repo badge; rows from ≥2
  //       repos appear (aggregated across the board's repos, PH-247).
  // -------------------------------------------------------------------------
  test("TC-4: ticket commit rows carry a repo badge; ≥2 distinct repos appear", async ({
    page,
  }) => {
    const errors = trackConsole(page);

    // Fetch the real ticket commits, then re-tag the rows across two repos so the
    // aggregated multi-repo view is deterministic (the e2e DB ticket is 1-repo).
    const realResp = await fetch(
      `${BACKEND}/api/tickets/${COMMIT_TICKET}/commits`,
      { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
    );
    const realData = (await realResp.json()) as {
      commits: Array<Record<string, unknown>>;
    };
    expect(realData.commits.length).toBeGreaterThan(0);
    const reTagged = {
      ...realData,
      commits: realData.commits.map((c, i) => {
        const sdk = i % 2 === 1; // alternate repos so ≥2 appear
        return {
          ...c,
          repo_id: sdk
            ? "22222222-2222-2222-2222-222222222222"
            : "11111111-1111-1111-1111-111111111111",
          repo_slug: sdk ? "gamexsdk" : "gamexcore",
          repo_name: sdk ? "GameX SDK" : "GameXCore",
        };
      }),
    };

    await page.route(
      new RegExp(`/api/tickets/${COMMIT_TICKET}/commits$`),
      async (route) => {
        await route.fulfill({
          status: 200,
          headers: { "content-type": "application/json" },
          body: JSON.stringify(reTagged),
        });
      },
    );

    await login(page);
    await page.goto(TICKET_URL);
    await page.waitForSelector(`text=${COMMIT_TICKET}`, { timeout: 10000 });
    // Git filter tab → renders TicketCommits. The ActivitySection filter is a
    // `role="tab"` (accessible name "Git <count>"), NOT a plain button.
    await page.getByRole("tab", { name: /^Git/ }).first().click();

    // Each commit row carries a repo badge.
    const badges = page.getByTestId("ticket-commit-repo-badge");
    await expect(badges.first()).toBeVisible({ timeout: 10000 });
    const badgeCount = await badges.count();
    expect(badgeCount).toBe(reTagged.commits.length);

    // ≥2 distinct repo slugs appear (aggregated across repos).
    const labels = await badges.allTextContents();
    const distinct = new Set(labels.map((t) => t.trim()));
    console.log(`[TC-4] repo badges: ${labels.join(", ")}`);
    expect(distinct.has("gamexcore")).toBe(true);
    expect(distinct.has("gamexsdk")).toBe(true);
    expect(distinct.size).toBeGreaterThanOrEqual(2);

    await page.screenshot({
      path: `${SHOTS}/tc4-repo-labeled-commits.png`,
      fullPage: false,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-5: non-primary drill-down threads `?repo=gamexsdk` into the commit git
  //       call AND the file list opens WITHOUT the 404 "Dosyalar yüklenemedi"
  //       banner (the core PH-249 bug fix).
  // -------------------------------------------------------------------------
  test("TC-5: expanding a non-primary commit issues ?repo=gamexsdk; file list opens (no 404 banner)", async ({
    page,
  }) => {
    const errors = trackConsole(page);

    // Re-tag commits, but force the FIRST row to gamexsdk so expanding row 0
    // exercises the non-primary path deterministically.
    const realResp = await fetch(
      `${BACKEND}/api/tickets/${COMMIT_TICKET}/commits`,
      { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
    );
    const realData = (await realResp.json()) as {
      commits: Array<Record<string, unknown>>;
    };
    const reTagged = {
      ...realData,
      commits: realData.commits.map((c) => ({
        ...c,
        repo_id: "22222222-2222-2222-2222-222222222222",
        repo_slug: "gamexsdk",
        repo_name: "GameX SDK",
      })),
    };
    await page.route(
      new RegExp(`/api/tickets/${COMMIT_TICKET}/commits$`),
      async (route) => {
        await route.fulfill({
          status: 200,
          headers: { "content-type": "application/json" },
          body: JSON.stringify(reTagged),
        });
      },
    );

    // The commit-detail git call carries ?repo=gamexsdk. The synthetic gamexsdk
    // repo doesn't exist server-side, so we FULFILL the /git/commits/<sha>
    // detail with a valid one-file body → asserts the FE threads the param AND
    // renders the file list (no 404 banner) when the repo-scoped call succeeds.
    const commitGitUrls: string[] = [];
    await page.route(
      new RegExp(`/api/boards/${BOARD_KEY}/git/commits/[^/?]+(\\?.*)?$`),
      async (route) => {
        const url = route.request().url();
        // Only intercept the DETAIL call (…/commits/<sha>), not …/diff.
        if (/\/git\/commits\/[^/]+\/diff/.test(url)) {
          await forward(route);
          return;
        }
        commitGitUrls.push(url);
        await route.fulfill({
          status: 200,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            sha: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            short_sha: "deadbeef",
            summary: "gamexsdk commit",
            author_name: "qa",
            committed_at: NOW,
            files: [
              {
                path: "src/sdk/main.ts",
                old_path: null,
                change_type: "M",
                additions: 5,
                deletions: 2,
                is_binary: false,
              },
            ],
          }),
        });
      },
    );

    await login(page);
    await page.goto(TICKET_URL);
    await page.waitForSelector(`text=${COMMIT_TICKET}`, { timeout: 10000 });
    // ActivitySection filter is a `role="tab"` (accessible name "Git <count>").
    await page.getByRole("tab", { name: /^Git/ }).first().click();

    // Expand the first (gamexsdk) commit row.
    const firstBadge = page.getByTestId("ticket-commit-repo-badge").first();
    await expect(firstBadge).toBeVisible({ timeout: 10000 });
    await expect(firstBadge).toHaveText("gamexsdk");
    // The row button is the badge's enclosing button.
    await page
      .locator("li", { has: firstBadge })
      .getByRole("button")
      .first()
      .click();

    // The commit-detail git call fired WITH ?repo=gamexsdk.
    await expect
      .poll(() => commitGitUrls.filter((u) => /[?&]repo=gamexsdk/.test(u)).length, {
        timeout: 8000,
      })
      .toBeGreaterThan(0);
    console.log(`[TC-5] commit git calls: ${commitGitUrls.join("\n")}`);

    // The file list opened (the repo-scoped detail resolved) — NOT the 404 banner.
    await expect(page.getByText("src/sdk/main.ts")).toBeVisible({
      timeout: 8000,
    });
    await expect(page.getByText("Dosyalar yüklenemedi.")).toHaveCount(0);

    await page.screenshot({
      path: `${SHOTS}/tc5-non-primary-drilldown.png`,
      fullPage: false,
    });
    expect(errors.filter(benign), errors.filter(benign).join("\n")).toEqual([]);
  });
});
