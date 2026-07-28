// PRDEV-1 AC6 live smoke — standalone (no e2e globalSetup → no live-state mutation).
// Auth via localStorage token (jarwis-qa). Verifies absence-of-verdict-label gating on a
// real rendered ticket detail + kanban card, and captures console errors / pageerrors /
// failing network responses (to pin any 4xx/5xx source).
import { chromium } from "@playwright/test";
import fs from "node:fs";

const BASE = process.env.PW_BASE || "http://localhost:5188";
const TOKEN = process.env.PW_TOKEN;
const SHOT_DIR = process.env.PW_SHOT_DIR || ".";
const BADGE = '[aria-label^="PR incelemesi"]'; // md span/button + sm kanban icon all share this prefix

const r = { detail: {}, kanban: {}, console: { errors: [], pageErrors: [], warnings: [] }, netFail: [] };
const browser = await chromium.launch({ headless: true });
try {
  const ctx = await browser.newContext();
  await ctx.addInitScript((tok) => {
    try { window.localStorage.setItem("projecthub.token", tok); } catch (_) {}
  }, TOKEN);
  const page = await ctx.newPage();
  page.on("console", (m) => {
    const t = m.type();
    if (t === "error") r.console.errors.push(m.text());
    else if (t === "warning") r.console.warnings.push(m.text());
  });
  page.on("pageerror", (e) => r.console.pageErrors.push(String((e && e.message) || e)));
  page.on("response", (resp) => {
    const s = resp.status();
    if (s >= 400) r.netFail.push({ status: s, method: resp.request().method(), url: resp.url() });
  });

  // ---- AC6 detail: PRDEV-1 has no verdict label → no header badge, no PR-Review panel ----
  await page.goto(`${BASE}/boards/PRDEV/tickets/PRDEV-1`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.getByRole("heading", { level: 1 }).first().waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1500); // let react-query settle comments/attachments
  r.detail.url = page.url();
  r.detail.redirectedToLogin = page.url().includes("/login");
  r.detail.h1 = ((await page.getByRole("heading", { level: 1 }).first().textContent().catch(() => null)) || "").trim() || null;
  r.detail.verdictBadgeCount = await page.locator(BADGE).count();
  r.detail.panelCount = await page.locator("#pr-review-panel-title").count();
  r.detail.sidebarLabelsRowCount = await page.getByText("Labels", { exact: true }).count();
  await page.screenshot({ path: `${SHOT_DIR}/qa-ac6-detail.png`, fullPage: false }).catch(() => {});

  // ---- AC6/AC4 kanban: PRDEV board cards carry no verdict icon (zero-fetch, absence) ----
  await page.goto(`${BASE}/boards/PRDEV`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.getByText("PRDEV-1", { exact: false }).first().waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1200);
  r.kanban.url = page.url();
  r.kanban.cardKeyVisibleCount = await page.getByText("PRDEV-1", { exact: false }).count();
  r.kanban.verdictBadgeCount = await page.locator(BADGE).count();
  await page.screenshot({ path: `${SHOT_DIR}/qa-ac6-kanban.png`, fullPage: false }).catch(() => {});
} catch (e) {
  r.fatal = String((e && e.stack) || e);
} finally {
  await browser.close();
}
fs.writeFileSync(`${SHOT_DIR}/qa-ac6-result.json`, JSON.stringify(r, null, 2));
console.log("PW_RESULT_JSON=" + JSON.stringify(r));
