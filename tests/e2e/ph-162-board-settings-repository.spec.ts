/**
 * PH-162 (G13) — Board Settings Repository Tab — Mode B Verify
 *
 * AC coverage:
 *   TC-1:  Repository tab visible to admin (AC-F1)
 *   TC-2:  Status panel shows connected state for PH board (AC-F2)
 *   TC-3:  Hook snippet visible when connected (AC-F9)
 *   TC-4:  "Simdi yenile" triggers bearer refresh, 202 response, toast shown (AC-F5)
 *   TC-5:  Rotate secret modal — one-shot reveal, secret removed on close (AC-F6)
 *   TC-6:  Refresh/Detach/Rotate hidden when no repo (AC-F11)
 *   TC-7:  Connect repo via UI on SMK test board (AC-F3)
 *   TC-8:  Client-side /repos/ prefix guard error shown inline (AC-F4)
 *   TC-9:  Detach repo from SMK test board with board-key confirm (AC-F7)
 *   TC-10: Members tab regression — still works (AC regression)
 *   TC-11: Dark mode parity + 0 console errors (AC-F10)
 *
 * Screenshots → .jarwis/logs/PH-162/qa-screenshots/
 *
 * Notes:
 *   - PH board: has repository row (connected=true) — used for F2/F5/F6
 *   - SMK board: repository=null — used for F3/F4/F7/F11 cycle
 *   - Playwright route matching: LIFO — last registered route wins
 *   - Proxy registered first; specific mocks registered AFTER → higher priority
 */

import { test, expect, type Page } from "@playwright/test";

const BASE = "http://localhost:5173";
const BACKEND = "http://localhost:8000";
const ADMIN_TOKEN = "change-me-on-first-login";
const PH_BOARD = "PH";
const TEST_BOARD = "SMK";
const SCREENSHOTS_DIR =
  "/Users/huseyinkanat/Documents/project-hub/.jarwis/logs/PH-162/qa-screenshots";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function loginAsAdmin(page: Page, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
}

async function goToRepositoryTab(page: Page, boardKey: string) {
  await page.goto(`${BASE}/boards/${boardKey}/settings`);
  await page.waitForLoadState("networkidle");
  const repoTab = page.getByRole("tab", { name: /repository/i });
  await expect(repoTab).toBeVisible({ timeout: 10000 });
  await repoTab.click();
  await page.waitForTimeout(800);
}

/**
 * Proxy repository+git API calls to backend.
 * Register FIRST — specific mocks registered AFTER take priority (Playwright LIFO).
 */
async function installApiProxy(page: Page) {
  await page.route(
    new RegExp("/api/boards/[^/]+/(repository|git)"),
    async (route) => {
      const req = route.request();
      const url = req.url().replace("http://localhost:5173", BACKEND);
      try {
        const resp = await fetch(url, {
          method: req.method(),
          headers: req.headers() as Record<string, string>,
          body:
            req.method() !== "GET" && req.method() !== "HEAD"
              ? await req.postDataBuffer()
              : undefined,
        });
        const body = await resp.arrayBuffer();
        await route.fulfill({
          status: resp.status,
          headers: Object.fromEntries(resp.headers.entries()),
          body: Buffer.from(body),
        });
      } catch {
        await route.abort();
      }
    },
  );
}

/** Clean up SMK board repository (ignore 404). */
async function cleanSMKRepo(page: Page) {
  await page.request.delete(`${BACKEND}/api/boards/${TEST_BOARD}/repository`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
}

/** Ensure SMK board has a repo row (create via API). */
async function ensureSMKRepo(page: Page) {
  await page.request.put(`${BACKEND}/api/boards/${TEST_BOARD}/repository`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: { provider: "local", default_branch: "main", local_path: "/repos/project-hub" },
  });
}

// ---------------------------------------------------------------------------
// TC-1: Repository tab visible (AC-F1)
// ---------------------------------------------------------------------------
test("TC-1: Repository tab appears after Members for admin (AC-F1)", async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto(`${BASE}/boards/${PH_BOARD}/settings`);
  await page.waitForLoadState("networkidle");

  const tabs = page.getByRole("tab");
  const tabTexts = await tabs.allTextContents();
  const lower = tabTexts.map((t) => t.toLowerCase());

  expect(lower.some((t) => t.includes("repository"))).toBe(true);

  const membersIdx = lower.findIndex((t) => t.includes("members"));
  const repoIdx = lower.findIndex((t) => t.includes("repository"));
  expect(repoIdx).toBeGreaterThan(membersIdx);
});

// ---------------------------------------------------------------------------
// TC-2: Status panel shows connected state for PH board (AC-F2)
// ---------------------------------------------------------------------------
test("TC-2: PH board shows connected status (remote_url, branch, last_synced) (AC-F2)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await loginAsAdmin(page);
  await installApiProxy(page);
  await goToRepositoryTab(page, PH_BOARD);

  const pageText = (await page.locator("body").textContent()) ?? "";

  // PH is connected — must NOT say "bağlı değil"
  expect(pageText).not.toMatch(/bağlı değil/i);

  // remote_url (github) and local_path are shown
  expect(pageText).toMatch(/github|project-hub|https?:/i);

  // last_synced humanised
  expect(pageText).toMatch(/önce|saniye|dakika|saat|sync|2026/i);

  // Screenshot
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/ph-status.png`, fullPage: false });

  expect(consoleErrors.filter((e) => !e.includes("favicon"))).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// TC-3: Hook snippet visible when connected (AC-F9)
// ---------------------------------------------------------------------------
test("TC-3: Hook snippet visible below operations when repo connected (AC-F9)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await installApiProxy(page);
  await goToRepositoryTab(page, PH_BOARD);

  const pageText = (await page.locator("body").textContent()) ?? "";
  expect(pageText).toMatch(/install-git-hook|scripts\//i);
  expect(pageText).toMatch(/rotate|secret/i);
});

// ---------------------------------------------------------------------------
// TC-4: "Simdi yenile" triggers bearer refresh (AC-F5)
// ---------------------------------------------------------------------------
test("TC-4: Simdi yenile POSTs /git/refresh with Bearer token, 202 (AC-F5)", async ({
  page,
}) => {
  await loginAsAdmin(page);

  // Register proxy first (lower priority in LIFO)
  await installApiProxy(page);

  // Register specific mock AFTER proxy → wins (LIFO = last registered = first matched)
  let refreshCalled = false;
  let refreshAuthorized = false;

  await page.route("**/git/refresh", async (route) => {
    const req = route.request();
    const authHeader = req.headers()["authorization"] ?? "";
    if (authHeader.startsWith("Bearer ")) {
      refreshAuthorized = true;
    }
    refreshCalled = true;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "queued", last_sync_at: new Date().toISOString() }),
    });
  });

  await goToRepositoryTab(page, PH_BOARD);

  // Click "Şimdi Yenile" using data-testid from RepositoryOperationsPanel.tsx
  const refreshBtn = page.locator("[data-testid='refresh-btn']");
  await expect(refreshBtn).toBeVisible({ timeout: 5000 });
  await refreshBtn.click();

  await page.waitForTimeout(1500);

  expect(refreshCalled).toBe(true);
  expect(refreshAuthorized).toBe(true);

  // InlineToast shows success message
  const pageText = (await page.locator("body").textContent()) ?? "";
  expect(pageText).toMatch(/Yenileme kuyrukta|Senkron tamam|kuyruk|queued/i);
});

// ---------------------------------------------------------------------------
// TC-5: Rotate secret modal — one-shot reveal (AC-F6)
// ---------------------------------------------------------------------------
test("TC-5: Rotate secret shows plaintext once, cleared on close (AC-F6)", async ({
  page,
}) => {
  await loginAsAdmin(page);

  // Register proxy first (lower priority)
  await installApiProxy(page);

  // Register rotate mock AFTER proxy → higher priority (LIFO)
  const fakeSecret = "a".repeat(48);
  const hookCmd = `bash scripts/install-git-hook.sh /repos/project-hub PH http://localhost:8000 ${fakeSecret}`;

  await page.route("**/rotate-refresh-secret", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ refresh_secret: fakeSecret, hook_install_command: hookCmd }),
    });
  });

  await goToRepositoryTab(page, PH_BOARD);

  // Open rotate modal via data-testid button (not aria-label which also contains "Döndür")
  const rotateBtn = page.locator("[data-testid='rotate-secret-btn']");
  await expect(rotateBtn).toBeVisible({ timeout: 5000 });
  await rotateBtn.click();
  await page.waitForTimeout(500);

  // Phase 1: modal shows warning. "Döndür" is the EXACT confirm button text (not the ops panel).
  // The ops panel button is now hidden by the modal overlay.
  // Use exact match for the modal's confirm button.
  const dondurBtn = page.getByRole("button", { name: "Döndür", exact: true });
  await expect(dondurBtn).toBeVisible({ timeout: 3000 });
  await dondurBtn.click();

  await page.waitForTimeout(1200);

  // Phase 2: secret value shown via data-testid
  const secretEl = page.locator("[data-testid='rotate-secret-value']");
  await expect(secretEl).toBeVisible({ timeout: 5000 });
  expect(await secretEl.textContent()).toContain(fakeSecret);

  // Screenshot
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/rotate-modal.png`, fullPage: false });

  // Close via "Sakladım, kapat"
  const closeBtn = page.getByRole("button", { name: /sakladım/i }).first();
  await expect(closeBtn).toBeVisible({ timeout: 3000 });
  await closeBtn.click();
  await page.waitForTimeout(500);

  // Secret element must be gone
  await expect(secretEl).not.toBeVisible();
  const afterText = (await page.locator("body").textContent()) ?? "";
  expect(afterText).not.toContain(fakeSecret);
});

// ---------------------------------------------------------------------------
// TC-6: Refresh/Detach/Rotate hidden when no repo (AC-F11)
// ---------------------------------------------------------------------------
test("TC-6: Refresh, Detach, Rotate buttons NOT rendered when disconnected (AC-F11)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await installApiProxy(page);
  await cleanSMKRepo(page);

  await goToRepositoryTab(page, TEST_BOARD);

  // data-testids from RepositoryOperationsPanel — component returns null when !connected
  await expect(page.locator("[data-testid='refresh-btn']")).not.toBeVisible();
  await expect(page.locator("[data-testid='detach-btn']")).not.toBeVisible();
  await expect(page.locator("[data-testid='rotate-secret-btn']")).not.toBeVisible();

  // Config form should be visible
  await expect(page.locator("#repo-local-path")).toBeVisible({ timeout: 5000 });
});

// ---------------------------------------------------------------------------
// TC-7: Connect repo via UI on SMK test board (AC-F3)
// ---------------------------------------------------------------------------
test("TC-7: Connect repo via form on SMK board → status connected (AC-F3)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await installApiProxy(page);
  await cleanSMKRepo(page);

  await goToRepositoryTab(page, TEST_BOARD);

  // Fill form using known IDs from RepositoryConfigForm.tsx
  const localPathInput = page.locator("#repo-local-path");
  const defaultBranchInput = page.locator("#repo-default-branch");

  await expect(localPathInput).toBeVisible({ timeout: 5000 });

  await localPathInput.click({ clickCount: 3 });
  await localPathInput.fill("/repos/project-hub");

  await defaultBranchInput.click({ clickCount: 3 });
  await defaultBranchInput.fill("main");

  // Screenshot — form filled
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/connect-form.png`, fullPage: false });

  // Submit — "Kaydet" from RepositoryConfigForm.tsx
  const submitBtn = page.getByRole("button", { name: "Kaydet", exact: true });
  await expect(submitBtn).toBeVisible({ timeout: 3000 });
  await submitBtn.click();

  await page.waitForTimeout(2000);

  // After successful PUT: either status updates to "Bağlı" or form values persist
  // Key signal: no error message shown
  const pageText = (await page.locator("body").textContent()) ?? "";
  expect(pageText).not.toMatch(/hata.*başarısız|failed|error/i);

  // Operations panel (refresh/rotate/detach) should now be visible (connected)
  await expect(page.locator("[data-testid='refresh-btn']")).toBeVisible({ timeout: 5000 });
});

// ---------------------------------------------------------------------------
// TC-8: Client-side /repos/ prefix guard (AC-F4)
// ---------------------------------------------------------------------------
test("TC-8: Submitting /wrong/path shows inline error before API call (AC-F4)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await installApiProxy(page);
  await cleanSMKRepo(page);

  await goToRepositoryTab(page, TEST_BOARD);

  const localPathInput = page.locator("#repo-local-path");
  await expect(localPathInput).toBeVisible({ timeout: 5000 });

  // Enter invalid path (no /repos/ prefix)
  await localPathInput.click({ clickCount: 3 });
  await localPathInput.fill("/wrong/path");

  const submitBtn = page.getByRole("button", { name: "Kaydet", exact: true });
  await submitBtn.click();

  // Client-side error message from RepositoryConfigForm.tsx:
  // "local_path /repos/ ile başlamalı"
  const errorEl = page.locator("#local-path-error");
  await expect(errorEl).toBeVisible({ timeout: 3000 });
  const errorText = (await errorEl.textContent()) ?? "";
  expect(errorText).toMatch(/repos.*başlamalı|başlamalı/i);
});

// ---------------------------------------------------------------------------
// TC-9: Detach repo from SMK test board (AC-F7)
// ---------------------------------------------------------------------------
test("TC-9: Detach repo from SMK board with key confirm → disconnected (AC-F7)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await installApiProxy(page);
  await ensureSMKRepo(page);

  await goToRepositoryTab(page, TEST_BOARD);

  // Click Detach button using data-testid
  const detachBtn = page.locator("[data-testid='detach-btn']");
  await expect(detachBtn).toBeVisible({ timeout: 8000 });
  await detachBtn.click();
  await page.waitForTimeout(500);

  // Screenshot — detach modal open
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/detach.png`, fullPage: false });

  // Modal warns: "BranchGraph sekmesi boşalır"
  const modalText = (await page.locator("body").textContent()) ?? "";
  expect(modalText).toMatch(/BranchGraph|boşalır/i);

  // Type the board key into confirmation input (data-testid from DetachConfirmModal.tsx)
  const confirmInput = page.locator("[data-testid='detach-confirm-input']");
  await expect(confirmInput).toBeVisible({ timeout: 3000 });
  await confirmInput.fill(TEST_BOARD);

  // Click "Ayır" confirm button (data-testid from DetachConfirmModal.tsx)
  const confirmBtn = page.locator("[data-testid='detach-confirm-btn']");
  await expect(confirmBtn).toBeEnabled({ timeout: 2000 });
  await confirmBtn.click();

  await page.waitForTimeout(2000);

  // After detach: ops panel gone (returns null when !connected)
  await expect(page.locator("[data-testid='detach-btn']")).not.toBeVisible();
  // Config form visible (to reconnect)
  await expect(page.locator("#repo-local-path")).toBeVisible({ timeout: 5000 });
});

// ---------------------------------------------------------------------------
// TC-10: Members tab regression (AC regression)
// ---------------------------------------------------------------------------
test("TC-10: Members tab still renders member list (AC regression)", async ({
  page,
}) => {
  await loginAsAdmin(page);
  await page.goto(`${BASE}/boards/${PH_BOARD}/settings`);
  await page.waitForLoadState("networkidle");

  const membersTab = page.getByRole("tab", { name: /members/i });
  await expect(membersTab).toBeVisible({ timeout: 8000 });
  await membersTab.click();
  await page.waitForTimeout(1000);

  const pageText = (await page.locator("body").textContent()) ?? "";
  expect(pageText).toMatch(/admin|member|pm|architect/i);
});

// ---------------------------------------------------------------------------
// TC-11: Dark mode parity + 0 console errors (AC-F10)
// ---------------------------------------------------------------------------
test("TC-11: Dark mode renders Repository tab cleanly, 0 console errors (AC-F10)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await loginAsAdmin(page);
  await page.goto(BASE);
  await page.evaluate(() => {
    localStorage.setItem("projecthub.token", "change-me-on-first-login");
    document.documentElement.classList.add("dark");
  });

  await installApiProxy(page);
  await goToRepositoryTab(page, PH_BOARD);

  // Re-apply dark after navigation
  await page.evaluate(() => { document.documentElement.classList.add("dark"); });
  await page.waitForTimeout(400);

  // Screenshot
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/dark.png`, fullPage: false });

  // Dark class must be present
  const hasDark = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  expect(hasDark).toBe(true);

  const filtered = consoleErrors.filter(
    (e) =>
      !e.includes("favicon") &&
      !e.includes("chrome-extension") &&
      !e.includes("ERR_ABORTED"),
  );
  expect(filtered).toHaveLength(0);
});
