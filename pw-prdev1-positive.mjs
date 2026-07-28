// PRDEV-1 positive-render — NON-MUTATING. Route-intercepts ONLY this browser session's
// API responses to inject a verdict label (+ a synthetic pr-reviewer HANDOFF comment) so
// the badge / panel / severity+sonar rows / most-cautious-priority actually RENDER live.
// No real ticket data is modified (pure response interception).
import { chromium } from "@playwright/test";
import fs from "node:fs";

const BASE = process.env.PW_BASE || "http://localhost:5188";
const TOKEN = process.env.PW_TOKEN;
const SHOT_DIR = process.env.PW_SHOT_DIR || ".";
const BADGE = '[aria-label^="PR incelemesi"]';

const r = { safe: {}, priority: {}, console: { errors: [], pageErrors: [] } };
let INJECT_LABELS = ["pr_safe"];

const browser = await chromium.launch({ headless: true });
try {
  const ctx = await browser.newContext();
  await ctx.addInitScript((tok) => { try { window.localStorage.setItem("projecthub.token", tok); } catch (_) {} }, TOKEN);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => r.console.pageErrors.push(String((e && e.message) || e)));
  page.on("console", (m) => { if (m.type() === "error") r.console.errors.push(m.text()); });

  // inject verdict label(s) into the ticket GET
  await page.route(/\/api\/tickets\/PRDEV-1(\?.*)?$/, async (route) => {
    const resp = await route.fetch();
    let body; try { body = await resp.json(); } catch { return route.fulfill({ response: resp }); }
    body.labels = Array.isArray(body.labels) ? [...body.labels, ...INJECT_LABELS] : [...INJECT_LABELS];
    await route.fulfill({ response: resp, json: body });
  });
  // append a synthetic pr-reviewer HANDOFF comment (clones a real one → valid shape)
  await page.route(/\/api\/tickets\/PRDEV-1\/comments(\?.*)?$/, async (route) => {
    const resp = await route.fetch();
    let arr; try { arr = await resp.json(); } catch { return route.fulfill({ response: resp }); }
    if (Array.isArray(arr) && arr.length) {
      const base = arr[arr.length - 1];
      arr = [...arr, { ...base, id: String(base.id) + "-mock-prr", created_at: new Date(Date.now() + 60000).toISOString(),
        body: "[HANDOFF pr-reviewer→coordinator] verdict: 3🔴 / 2🟠 / 5🟡 / 1🔵 — sonar=0 new issues, gate OK" }];
    }
    await route.fulfill({ response: resp, json: arr });
  });

  // Load A — pr_safe → SAFE badge + panel + severity row + sonar row
  await page.goto(`${BASE}/boards/PRDEV/tickets/PRDEV-1`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.locator(BADGE).first().waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1500);
  r.safe.badgeCount = await page.locator(BADGE).count();
  r.safe.badgeAria = await page.locator(BADGE).first().getAttribute("aria-label").catch(() => null);
  r.safe.panelCount = await page.locator("#pr-review-panel-title").count();
  r.safe.severityRowCount = await page.locator('[aria-label="Bulgu sayıları"]').count();
  r.safe.sonarVisibleCount = await page.getByText("0 new issues", { exact: false }).count();
  await page.screenshot({ path: `${SHOT_DIR}/qa-positive-safe.png`, fullPage: false }).catch(() => {});

  // Load B — pr_conditions + pr_not_safe → most-cautious (NOT SAFE) wins live (fresh document = fresh query)
  INJECT_LABELS = ["pr_conditions", "pr_not_safe"];
  await page.goto(`${BASE}/boards/PRDEV/tickets/PRDEV-1?v=2`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.locator(BADGE).first().waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1200);
  r.priority.badgeAria = await page.locator(BADGE).first().getAttribute("aria-label").catch(() => null);
  await page.screenshot({ path: `${SHOT_DIR}/qa-positive-priority.png`, fullPage: false }).catch(() => {});
} catch (e) {
  r.fatal = String((e && e.stack) || e);
} finally {
  await browser.close();
}
fs.writeFileSync(`${SHOT_DIR}/qa-positive-result.json`, JSON.stringify(r, null, 2));
console.log("PW_POS_JSON=" + JSON.stringify(r));
