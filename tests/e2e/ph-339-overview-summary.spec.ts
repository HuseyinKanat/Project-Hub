/**
 * PH-339 — "Genel Bakış" project summary: Turkish sections + visual milestone
 * timeline + editor (fills the PH-337 OverviewTab slot; consumes the PH-338
 * singleton summary REST).
 *
 * AC coverage:
 *   TC-1 (AC4/AC3/AC1/AC2) empty-state (200+null) → an admin's "Özet oluştur"
 *        CTA → editor → fill 4 sections + 3 milestones (one per status) → save →
 *        view renders the Turkish sections (MarkdownRenderer bullets) + the
 *        ordered milestone timeline; RELOAD persists (full-replace round-trip).
 *   TC-2 (AC2/AC5) the 3 milestones render `order`-ascending with BOTH a status
 *        colour token (text-success/​warning/​muted) AND a Turkish text label
 *        (Tamamlandı / Aktif / Planlı) — colour is never the sole differentiator.
 *   TC-3 (AC3 E1) a PUT that fails (intercepted 500) shows an inline error AND
 *        PRESERVES the typed content (no data loss; retry possible).
 *   TC-4 (AC4) a GET that fails (intercepted 500) shows the inline summary error
 *        while the epic-progress panel (a separate query) stays mounted.
 *
 * Environment: the ISOLATED worktree stack — FE :5273 (Vite proxy /api →) the
 * worktree backend :8010 on a throwaway `projecthub_ph339` DB (live data never
 * touched). Bootstrapped admin token = the well-known dev token.
 */
import { test, expect, type Page } from "@playwright/test";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5273";
const API = process.env.E2E_API_URL ?? "http://localhost:8010";
const TOKEN = "change-me-on-first-login";
const BOARD_URL = `${BASE}/boards/PH`;

async function loginAndOpenOverview(page: Page) {
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);
  await page.goto(`${BOARD_URL}#overview`);
  await page.waitForSelector('[role="tablist"]', { timeout: 10000 });
  await page.locator("#tab-overview").click();
  await expect(page.locator("#panel-overview")).toBeVisible();
}

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

test.describe("PH-339: Genel Bakış özet + milestone timeline + editor", () => {
  test.describe.configure({ mode: "serial" });

  // Reset the singleton to null so each run starts from the empty-state.
  test.beforeEach(async ({ request }) => {
    await request
      .delete(`${API}/api/boards/PH/summary`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      })
      .catch(() => undefined);
  });

  test("TC-1: empty → create via editor → view renders sections + timeline; reload persists", async ({
    page,
  }) => {
    const errors = trackConsole(page);
    await loginAndOpenOverview(page);

    // Empty-state (200 + null) with the write-authorised admin's Create CTA.
    await expect(page.locator('[data-testid="overview-summary-empty"]')).toBeVisible();
    await page.locator('[data-testid="summary-create-button"]').click();

    // Editor opens — fill the four Turkish sections.
    await expect(page.locator('[data-testid="summary-editor"]')).toBeVisible();
    await page
      .locator('[data-testid="summary-section-purpose"]')
      .fill("- Restoran POS sistemi\n- Mutfak ekranı entegrasyonu");
    await page.locator('[data-testid="summary-section-status"]').fill("Aktif geliştirme");
    await page.locator('[data-testid="summary-section-progress"]').fill("- 12/20 ticket kapandı");
    await page.locator('[data-testid="summary-section-highlights"]').fill("- PH-338 REST\n- PH-339 UI");

    // Add three milestones — one per status (order = row position).
    const rows = [
      { title: "Faz 1 — Temel", status: "done" },
      { title: "Faz 2 — Pilot", status: "active" },
      { title: "Faz 3 — Yaygınlaştırma", status: "planned" },
    ];
    for (let i = 0; i < rows.length; i++) {
      await page.locator('[data-testid="milestone-add"]').click();
      await page.locator('[data-testid="milestone-title-input"]').nth(i).fill(rows[i].title);
      await page
        .locator('[data-testid="milestone-status-select"]')
        .nth(i)
        .selectOption(rows[i].status);
    }

    // Save → back to the read view (editor gone, summary + edit button shown).
    await page.locator('[data-testid="summary-editor-save"]').click();
    await expect(page.locator('[data-testid="summary-editor"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="summary-edit-button"]')).toBeVisible();

    // Sections rendered (AC1) — the human's `- ` bullets become a markdown list.
    await expect(
      page.locator('[data-testid="summary-section-view-purpose"]'),
    ).toContainText("Restoran POS sistemi");
    await expect(page.locator('[data-testid="summary-section-view-purpose"]').locator("li")).toHaveCount(2);
    await expect(
      page.locator('[data-testid="summary-section-view-status"]'),
    ).toContainText("Aktif geliştirme");

    // Milestone timeline (AC2) — three ordered items.
    await expect(page.locator('[data-testid="milestone-timeline"]')).toBeVisible();
    await expect(page.locator('[data-testid="milestone-item"]')).toHaveCount(3);

    // RELOAD → full-replace round-trip persisted (AC3).
    await page.reload();
    await page.waitForSelector('[role="tablist"]', { timeout: 10000 });
    await page.locator("#tab-overview").click();
    await expect(page.locator('[data-testid="overview-summary"]')).toBeVisible();
    await expect(page.locator('[data-testid="milestone-item"]')).toHaveCount(3);
    await expect(
      page.locator('[data-testid="summary-section-view-purpose"]'),
    ).toContainText("Restoran POS sistemi");

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("TC-2: milestones render order-ascending with colour token AND Turkish text label (AC2/AC5)", async ({
    page,
    request,
  }) => {
    // Seed a deterministic summary via the API (order 0=done,1=active,2=planned).
    await request.put(`${API}/api/boards/PH/summary`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: {
        purpose: "amaç",
        status: null,
        progress: null,
        highlights: null,
        milestones: [
          { title: "Tamam-taş", target: "x", status: "done", order: 0, due_date: "2026-06-01" },
          { title: "Aktif-taş", target: null, status: "active", order: 1, due_date: null },
          { title: "Planlı-taş", target: null, status: "planned", order: 2, due_date: null },
        ],
      },
    });

    await loginAndOpenOverview(page);
    const badges = page.locator('[data-testid="milestone-status"]');
    await expect(badges).toHaveCount(3);

    // Text labels present in ascending order (colour is NOT the sole cue — AC5).
    await expect(badges.nth(0)).toHaveText("Tamamlandı");
    await expect(badges.nth(1)).toHaveText("Aktif");
    await expect(badges.nth(2)).toHaveText("Planlı");

    // Colour tokens applied per status (AC2 — ProjectHub tokens, no baked hex).
    await expect(badges.nth(0)).toHaveClass(/text-success/);
    await expect(badges.nth(1)).toHaveClass(/text-warning/);
    await expect(badges.nth(2)).toHaveClass(/text-text-muted/);

    // Timeline is a real ordered list (AC5).
    await expect(page.locator('ol[data-testid="milestone-timeline"]')).toBeVisible();
  });

  test("TC-3: a failed save shows an inline error AND preserves the typed content (AC3 E1)", async ({
    page,
  }) => {
    // Seed a summary so we can open the editor from view mode.
    await loginAndOpenOverview(page);
    await page.locator('[data-testid="summary-create-button"]').click();
    await page.locator('[data-testid="summary-section-purpose"]').fill("ilk hali");
    await page.locator('[data-testid="summary-editor-save"]').click();
    await expect(page.locator('[data-testid="summary-edit-button"]')).toBeVisible();

    // Re-enter edit, change content, then force the PUT to 500.
    await page.locator('[data-testid="summary-edit-button"]').click();
    const preserved = "DÜZENLENMİŞ — kaybolmamalı";
    await page.locator('[data-testid="summary-section-purpose"]').fill(preserved);
    await page.route("**/api/boards/**/summary", (route) => {
      if (route.request().method() === "PUT") {
        return route.fulfill({ status: 500, body: '{"detail":"boom"}' });
      }
      return route.continue();
    });
    await page.locator('[data-testid="summary-editor-save"]').click();

    // Inline error shown AND the edited content is still in the textarea (E1).
    await expect(page.locator('[data-testid="summary-editor-error"]')).toBeVisible();
    await expect(page.locator('[data-testid="summary-section-purpose"]')).toHaveValue(preserved);
    await page.unroute("**/api/boards/**/summary");
  });

  test("TC-4: a failed GET degrades inline while epic-progress stays mounted (AC4)", async ({
    page,
  }) => {
    await page.goto(BASE);
    await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);
    await page.route("**/api/boards/**/summary", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ status: 500, body: '{"detail":"boom"}' });
      }
      return route.continue();
    });
    await page.goto(`${BOARD_URL}#overview`);
    await page.waitForSelector('[role="tablist"]', { timeout: 10000 });
    await page.locator("#tab-overview").click();

    // Summary error surfaces inline; the epic-progress panel (separate query) is
    // unaffected and still mounted (non-blocking degrade).
    await expect(page.locator('[data-testid="overview-summary-error"]')).toBeVisible();
    await expect(page.locator('#panel-overview [data-testid^="epic-progress"]')).toHaveCount(1);
    await page.unroute("**/api/boards/**/summary");
  });
});
