// Capture reference screenshots of every ProjectHub screen → screenshots/.
// Reuses the project's default dev admin token (same as the e2e suite).
// Dev server must be running on :5173.  Run from repo root:
//     node design/stitch-redesign/capture.mjs
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, "screenshots");
const BASE = "http://localhost:5173";
const TOKEN = "change-me-on-first-login"; // project default dev admin token
const BOARD = "PH";
const TICKET = "PH-167"; // has a branch → exercises the branch-diff modal

const VIEWPORT = { width: 1440, height: 900 };
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function makeContext(browser, theme, authed = true) {
  const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2, colorScheme: theme });
  await ctx.addInitScript(
    ([tok, th, authed]) => {
      if (authed) localStorage.setItem("projecthub.token", tok);
      localStorage.setItem("theme", th);
    },
    [TOKEN, theme, authed],
  );
  return ctx;
}

async function main() {
  const browser = await chromium.launch();
  const written = [];

  for (const theme of ["dark", "light"]) {
    const sfx = theme === "dark" ? "" : "-light";
    const ctx = await makeContext(browser, theme);
    const page = await ctx.newPage();
    const shot = async (name, opts = {}) => {
      await page.screenshot({ path: join(OUT, `${name}${sfx}.png`), fullPage: Boolean(opts.fullPage) });
      written.push(`${name}${sfx}.png`);
    };
    const elShot = async (sel, name) => {
      const el = page.locator(sel).first();
      if (await el.count()) { await el.scrollIntoViewIfNeeded().catch(() => {}); await pause(300); await el.screenshot({ path: join(OUT, `${name}${sfx}.png`) }); written.push(`${name}${sfx}.png`); }
    };
    const goto = (p) => page.goto(BASE + p, { waitUntil: "domcontentloaded" });
    console.log(`\n=== theme: ${theme} ===`);

    // 02 — Boards list
    await goto("/");
    await page.waitForSelector('a[href^="/boards/"]', { timeout: 15000 }).catch(() => {});
    await pause(700); await shot("02-boards-list"); console.log("02 ✓");

    // 03 — Kanban
    await goto(`/boards/${BOARD}`);
    await page.waitForSelector('[data-testid^="kanban-column-"]', { timeout: 15000 }).catch(() => {});
    await pause(800); await shot("03-board-kanban"); console.log("03 ✓");

    // 00 — header chrome crop + dialogs (dark only)
    if (theme === "dark") {
      await elShot("header", "00-shared-chrome"); console.log("00 chrome ✓");
      await page.click('button:has-text("Yeni ticket")').catch(() => {});
      await pause(600); await shot("00-new-ticket-dialog");
      await page.click('button:has-text("Vazgeç")').catch(() => {}); await pause(300);
      const bell = page.locator('button[aria-label="Bildirimler"]').first();
      if (await bell.count()) { await bell.click(); await pause(600); await shot("00-notifications"); await page.keyboard.press("Escape").catch(() => {}); }
      console.log("00 dialogs ✓");
    }

    // 04 — Branch Graph (CLICK the tab; hash-nav alone won't re-mount)
    await goto(`/boards/${BOARD}`);
    await page.waitForSelector("#tab-graph", { timeout: 15000 });
    await page.click("#tab-graph");
    await page.waitForSelector('[aria-label="Commit history"] button[role="listitem"]', { timeout: 20000 }).catch(() => {});
    await pause(1400); await shot("04-branch-graph"); console.log("04 ✓");
    // 04b — commit selected → diff panel
    await page.locator('[aria-label="Commit history"] button[role="listitem"]').first().click().catch(() => {});
    await page.waitForSelector('[aria-label="Commit diff panel"]', { timeout: 15000 }).catch(() => {});
    await pause(1800); await shot("04-branch-graph-commit-selected"); console.log("04b ✓");

    // 05 — Ticket Detail (+ branch diff modal)
    await goto(`/boards/${BOARD}/tickets/${TICKET}`);
    await page.waitForSelector("h1", { timeout: 15000 }).catch(() => {});
    await pause(1200); await shot("05-ticket-detail", { fullPage: true }); console.log("05 ✓");
    await page.click('button[aria-label^="View diff for branch"]').catch(() => {});
    await page.waitForSelector('[id="branch-diff-modal-title"]', { timeout: 8000 }).catch(() => {});
    await pause(1500); await shot("05-ticket-branch-diff");
    await page.keyboard.press("Escape").catch(() => {}); await pause(300); console.log("05b ✓");

    // 06 — Settings · General
    await goto(`/boards/${BOARD}/settings`);
    await page.waitForSelector("#general-tab, #workflow-tab", { timeout: 15000 }).catch(() => {});
    await pause(700); await shot("06-settings-general"); console.log("06 ✓");

    // 07 — Settings · Workflow (+ permissions matrix element crop)
    await page.click("#workflow-tab").catch(() => {});
    await page.waitForSelector('[data-testid="permission-matrix"]', { timeout: 15000 }).catch(() => {});
    await pause(1500); await shot("07-settings-workflow", { fullPage: true });
    await elShot('[data-testid="permission-matrix"]', "07-permissions-matrix"); console.log("07 ✓");

    // 08 — Settings · Members (+ add-member modal, dark only)
    await page.click("#members-tab").catch(() => {});
    await page.waitForSelector('[data-testid="add-member-btn"], #members-panel', { timeout: 15000 }).catch(() => {});
    await pause(1000); await shot("08-settings-members");
    if (theme === "dark") {
      await page.click('[data-testid="add-member-btn"]').catch(() => {});
      await page.waitForSelector('[role="dialog"][aria-labelledby="add-member-title"]', { timeout: 8000 }).catch(() => {});
      await pause(600); await shot("08-add-member-modal");
      await page.keyboard.press("Escape").catch(() => {}); await pause(300);
    }
    console.log("08 ✓");

    // 09 — Settings · Repository
    await goto(`/boards/${BOARD}/settings`);
    await page.click("#repository-tab").catch(() => {});
    await pause(1200); await shot("09-settings-repository", { fullPage: true }); console.log("09 ✓");

    await ctx.close();
  }

  // 01 — Login (no token)
  for (const theme of ["dark", "light"]) {
    const sfx = theme === "dark" ? "" : "-light";
    const ctx = await makeContext(browser, theme, false);
    const page = await ctx.newPage();
    await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
    await page.waitForSelector('input[type="password"]', { timeout: 15000 }).catch(() => {});
    await pause(600);
    await page.screenshot({ path: join(OUT, `01-login${sfx}.png`) });
    written.push(`01-login${sfx}.png`);
    await ctx.close();
  }
  console.log("01 ✓");

  await browser.close();
  console.log(`\nDONE — ${written.length} screenshots → screenshots/`);
}
main().catch((e) => { console.error(e); process.exit(1); });
