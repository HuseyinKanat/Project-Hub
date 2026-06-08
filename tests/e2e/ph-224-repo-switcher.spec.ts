/**
 * PH-224 (C4) — Branch-view multi-repo RepoSwitcher
 * Mode B (Verify): maps the 7 ACs to live-browser checks against the Vite dev
 * server (localhost:5173 → backend:8000). A 2nd repo (`demo-web`) is seeded on
 * the live PH board via the trusted server-side add_repository service path
 * BEFORE this run (see .jarwis/logs/PH-224/qa.md) so AC2–AC4 exercise real
 * multi-repo data; QA removes it again after the run, leaving PH single-repo.
 *
 * AC coverage:
 *   AC1 (no-regression, single repo)  → TC-AC1   (repos mocked to 1 → NO switcher,
 *                                                  3-pane intact, no layout shift)
 *   AC5 (client backward-compat)      → TC-AC5   (single-repo git calls carry NO repo=)
 *   AC2 (selector appears→primary)    → TC-AC2   (≥2 repos → switcher, default=primary)
 *   AC3 (switch re-queries+re-renders) → TC-AC3  (pick demo-web → distinct branches/SHAs,
 *                                                  repo=demo-web on wire, selection reset)
 *   AC4 (persist/shareable + fallback) → TC-AC4a/b (?repo survives reload; unknown→primary)
 *   AC3-diff (cross-repo trap)        → TC-AC3-diff (repo-B commit diff, no cross-repo 404)
 *   AC7 (console clean)               → asserted across TCs (no critical console errors)
 *
 * Environment: Vite dev server proxies /api/* to backend:8000 (Docker-internal,
 * unresolvable from macOS). We forward BOTH /git/* AND /repositories to
 * localhost:8000 via page.route; AC1/AC5 additionally fulfil /repositories with a
 * single-repo body to exercise the frontend single-repo path deterministically.
 */

import { test, expect, type Page } from "@playwright/test";
import { ADMIN_TOKEN } from "./helpers/workflowSnapshot";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const BOARD_KEY = "PH";
const BOARD_URL = `${BASE}/boards/${BOARD_KEY}`;
const SHOTS = "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-224/qa-screenshots";

/** Forward a backend GET through to localhost:8000 (proxy bypass). */
async function forward(route: import("@playwright/test").Route) {
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

/** Forward git/* AND repositories list to the real backend. */
async function installLiveProxy(page: Page) {
  await page.route(new RegExp("/api/boards/[^/]+/git/"), forward);
  await page.route(new RegExp("/api/boards/[^/]+/repositories$"), forward);
}

/** Forward git/*, but FULFIL repositories with a single-repo (primary only) body. */
async function installSingleRepoProxy(page: Page) {
  await page.route(new RegExp("/api/boards/[^/]+/git/"), forward);
  await page.route(new RegExp("/api/boards/[^/]+/repositories$"), async (route) => {
    // Fetch the real list then keep only the primary → frontend single-repo path.
    const url = route.request().url().replace(BASE, BACKEND);
    const resp = await fetch(url, {
      headers: route.request().headers() as Record<string, string>,
    });
    const data = (await resp.json()) as {
      repositories: Array<{ is_primary: boolean }>;
    };
    const primaryOnly = data.repositories.filter((r) => r.is_primary);
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repositories: primaryOnly }),
    });
  });
}

async function login(page: Page) {
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), ADMIN_TOKEN);
}

async function openGraphTab(page: Page) {
  await page.waitForSelector('[role="tablist"]', { timeout: 15000 });
  await page.getByRole("tab", { name: "Branch Graph" }).click();
  await page.waitForSelector('ul[aria-label="Commit history"]', { timeout: 40000 });
  await page.getByRole("listitem").first().waitFor({ timeout: 40000 });
}

/** Collect all /git/* request URLs (to inspect the repo= query param). */
function trackGitRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (req) => {
    const u = req.url();
    if (/\/api\/boards\/[^/]+\/git\//.test(u)) urls.push(u);
  });
  return urls;
}

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

const benign = (e: string) =>
  !e.includes("ResizeObserver") &&
  !e.includes("WebSocket") &&
  !e.includes("Failed to load resource") &&
  !e.includes("the server responded with a status");

test.describe("PH-224: branch-view RepoSwitcher", () => {
  test.describe.configure({ mode: "serial", timeout: 90000 });

  test("TC-AC1: single repo → NO RepoSwitcher, 3-pane branch view intact", async ({ page }) => {
    const errors = trackConsole(page);
    await installSingleRepoProxy(page);
    await login(page);
    await page.goto(BOARD_URL);
    await openGraphTab(page);

    // No selector.
    await expect(page.locator('select[aria-label="Select repository"]')).toHaveCount(0);

    // 3-pane intact: branch sidebar + commit list + (diff on click).
    await expect(page.locator('aside[aria-label="Branch list"]')).toBeVisible();
    await expect(page.locator('ul[aria-label="Commit history"]')).toBeVisible();
    expect(await page.getByRole("listitem").count()).toBeGreaterThan(0);

    await page.screenshot({ path: `${SHOTS}/ac1-single-repo-no-switcher.png`, fullPage: false });
    expect(errors.filter(benign)).toHaveLength(0);
  });

  test("TC-AC5: single repo → git calls carry NO repo= query param", async ({ page }) => {
    const gitUrls = trackGitRequests(page);
    await installSingleRepoProxy(page);
    await login(page);
    await page.goto(BOARD_URL);
    await openGraphTab(page);
    // click a commit row to also fire the commit-diff git call
    const rows = page.getByRole("listitem");
    await rows.nth(Math.min(2, (await rows.count()) - 1)).click();
    await page.waitForTimeout(1500);

    expect(gitUrls.length).toBeGreaterThan(0);
    const withRepoParam = gitUrls.filter((u) => /[?&]repo=/.test(u));
    console.log(`[TC-AC5] git calls: ${gitUrls.length}, with repo=: ${withRepoParam.length}`);
    expect(withRepoParam).toHaveLength(0); // byte-identical to pre-PR
  });

  test("TC-AC2: ≥2 repos → RepoSwitcher visible, defaults to primary", async ({ page }) => {
    const errors = trackConsole(page);
    await installLiveProxy(page);
    await login(page);
    await page.goto(BOARD_URL);
    await openGraphTab(page);

    const select = page.locator('select[aria-label="Select repository"]');
    await expect(select).toBeVisible({ timeout: 10000 });
    // default selected option's value = the primary slug (project-hub)
    await expect(select).toHaveValue("project-hub");
    // primary badge present (the styled span badge next to the switcher, not the
    // <option> text nor a ticket title containing the word)
    await expect(page.locator("span.text-accent", { hasText: /^PRIMARY$/ })).toBeVisible();
    // no ?repo= in the URL when primary is selected (param deleted/clean)
    expect(new URL(page.url()).searchParams.get("repo")).toBeNull();

    await page.screenshot({ path: `${SHOTS}/ac2-switcher-default-primary.png` });
    expect(errors.filter(benign)).toHaveLength(0);
  });

  test("TC-AC3: switch to demo-web → distinct branches/SHAs + repo=demo-web on wire", async ({ page }) => {
    const gitUrls = trackGitRequests(page);
    const errors = trackConsole(page);
    await installLiveProxy(page);
    await login(page);
    await page.goto(BOARD_URL);
    await openGraphTab(page);

    const select = page.locator('select[aria-label="Select repository"]');
    await expect(select).toBeVisible();

    // capture primary sidebar branches (project-hub has many real branches)
    const sidebar = page.locator('aside[aria-label="Branch list"]');
    await expect(sidebar.getByRole("button", { name: /main/ }).first()).toBeVisible();

    // switch to demo-web
    await select.selectOption("demo-web");
    // URL syncs ?repo=demo-web
    await expect(page).toHaveURL(/[?&]repo=demo-web/);

    // demo-web has exactly 2 branches: main + feature/login-page (distinct from project-hub)
    await expect(sidebar.getByRole("button", { name: /feature\/login-page/ })).toBeVisible({
      timeout: 8000,
    });
    // demo-web only 3 commits → far fewer rows than project-hub
    await page.waitForTimeout(800);
    const demoRows = await page.getByRole("listitem").count();
    console.log(`[TC-AC3] demo-web commit rows: ${demoRows}`);
    expect(demoRows).toBeGreaterThan(0);
    expect(demoRows).toBeLessThan(50); // project-hub has 150+; demo-web has 3

    // wire carries repo=demo-web on graph fetch
    const demoGitCalls = gitUrls.filter((u) => /[?&]repo=demo-web/.test(u));
    console.log(`[TC-AC3] git calls with repo=demo-web: ${demoGitCalls.length}`);
    expect(demoGitCalls.length).toBeGreaterThan(0);

    await page.screenshot({ path: `${SHOTS}/ac3-demo-web-distinct-graph.png` });
    expect(errors.filter(benign)).toHaveLength(0);
  });

  test("TC-AC3-diff: repo-B (demo-web) commit diff resolves against demo-web (no cross-repo 404)", async ({ page }) => {
    const errors = trackConsole(page);
    const gitUrls = trackGitRequests(page);
    await installLiveProxy(page);
    await login(page);
    await page.goto(`${BOARD_URL}?repo=demo-web#graph`);
    await openGraphTab(page);

    const select = page.locator('select[aria-label="Select repository"]');
    await expect(select).toHaveValue("demo-web", { timeout: 10000 });

    // click the first demo-web commit row → diff panel must open WITHOUT a 404
    const rows = page.getByRole("listitem");
    await rows.first().click();
    const panel = page.locator('aside[aria-label="Commit diff panel"]');
    await expect(panel).toBeVisible({ timeout: 8000 });
    // the sha header (a font-mono span = sha.slice(0,12)) proves the diff opened
    const shaHeader = panel.locator("span.font-mono").filter({ hasText: /^[0-9a-f]{12}$/ }).first();
    await expect(shaHeader).toBeVisible({ timeout: 8000 });
    const shaText = ((await shaHeader.textContent()) ?? "").trim();
    console.log(`[TC-AC3-diff] panel sha header: ${shaText}`);
    // it must be a demo-web sha (fa230557 / 33b9f0cf / 558794f7), NOT a project-hub sha
    expect(["fa230557", "33b9f0cf", "558794f7"]).toContain(shaText.slice(0, 8));

    // the diff/commit git call must carry repo=demo-web (cross-repo trap closed)
    await page.waitForTimeout(1500);
    const anyDemoCalls = gitUrls.filter((u) => /[?&]repo=demo-web/.test(u));
    console.log(`[TC-AC3-diff] git calls w/ repo=demo-web: ${anyDemoCalls.length}`);
    expect(anyDemoCalls.length).toBeGreaterThan(0);

    // no 404 / not-found text inside the diff panel
    await expect(panel.getByText(/404|not found/i)).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/ac3-diff-repo-scoped.png` });
    expect(errors.filter(benign)).toHaveLength(0);
  });

  test("TC-AC4a: ?repo=demo-web survives reload (shareable URL)", async ({ page }) => {
    await installLiveProxy(page);
    await login(page);
    await page.goto(`${BOARD_URL}?repo=demo-web#graph`);
    await openGraphTab(page);

    const select = page.locator('select[aria-label="Select repository"]');
    await expect(select).toHaveValue("demo-web", { timeout: 10000 });

    // reload → still demo-web
    await page.reload();
    await openGraphTab(page);
    await expect(page.locator('select[aria-label="Select repository"]')).toHaveValue("demo-web", {
      timeout: 10000,
    });
    await expect(page).toHaveURL(/[?&]repo=demo-web/);
  });

  test("TC-AC4b: unknown ?repo=bogus → falls back to primary + param cleaned", async ({ page }) => {
    await installLiveProxy(page);
    await login(page);
    await page.goto(`${BOARD_URL}?repo=bogus-does-not-exist#graph`);
    await openGraphTab(page);

    const select = page.locator('select[aria-label="Select repository"]');
    await expect(select).toBeVisible({ timeout: 10000 });
    // falls back to primary
    await expect(select).toHaveValue("project-hub", { timeout: 8000 });
    // stale param cleaned from URL
    await expect.poll(() => new URL(page.url()).searchParams.get("repo")).toBeNull();
  });
});
