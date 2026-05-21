import { test, expect } from "@playwright/test";

const BASE = "http://localhost:5174";
const API = "http://localhost:8000";
// Admin token: use the stored token from project-hub dev env
const ADMIN_TOKEN = "change-me-on-first-login";
// PM token: jarwis-pm actor (role=pm, not admin on PH board)
const PM_TOKEN = "26977b2fc3dc69082f1b430d2f21a3deb0d4ca56ae6cf2f6";

test.describe("PH-47: Admin-only ticket delete", () => {
  test("admin can delete a ticket via detail page — golden path", async ({ page }) => {
    // Inject admin token into localStorage
    await page.goto(BASE);
    await page.evaluate((t) => localStorage.setItem("projecthub.token", t), ADMIN_TOKEN);

    // Create a throwaway ticket via API to delete during this test
    const resp = await page.request.post(`${API}/api/tickets`, {
      headers: {
        Authorization: `Bearer ${ADMIN_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        board_id: "PH",
        type: "task",
        title: `[E2E-DELETE-TEST] PH-47 admin delete ${Date.now()}`,
        priority: "low",
        labels: ["e2e-test"],
      },
    });
    expect(resp.ok()).toBeTruthy();
    const created = (await resp.json()) as { key: string; title: string };
    const ticketKey = created.key;
    const ticketTitle = created.title;
    console.log(`Created test ticket: ${ticketKey} - ${ticketTitle}`);

    try {
      // Navigate to the ticket detail page
      await page.goto(`${BASE}/boards/PH/tickets/${ticketKey}`);

      // Wait for page to load and ticket title to appear
      await expect(page.locator("h1")).toContainText(ticketTitle, { timeout: 10000 });

      // AC1: Admin sees the "Sil" button
      const deleteButton = page.getByTestId("delete-ticket-button");
      await expect(deleteButton).toBeVisible({ timeout: 8000 });

      // Edge case: cancel does not delete
      await deleteButton.click();
      const modal = page.getByRole("dialog");
      await expect(modal).toBeVisible();
      await expect(modal).toContainText(ticketKey);
      await expect(modal).toContainText(ticketTitle);

      // Click cancel — modal closes, ticket not deleted
      await page.getByRole("button", { name: "İptal" }).click();
      await expect(modal).not.toBeVisible();
      // Ticket detail page still showing
      await expect(page.locator("h1")).toContainText(ticketTitle);

      // Golden path: open modal again, fill reason, confirm
      await deleteButton.click();
      await expect(page.getByRole("dialog")).toBeVisible();

      await page.getByTestId("delete-reason-input").fill("E2E test cleanup — PH-47 playwright spec");

      // Click confirm delete
      await page.getByTestId("confirm-delete-button").click();

      // After deletion, should redirect to /boards/PH
      await expect(page).toHaveURL(`${BASE}/boards/PH`, { timeout: 10000 });

      // Finding #1: success toast must be visible after redirect
      await expect(page.getByTestId("success-toast")).toBeVisible({ timeout: 5000 });
      await expect(page.getByTestId("success-toast")).toContainText(`${ticketKey} silindi`);

      // Ticket should not appear in board list anymore
      await expect(page.locator(`text=${ticketTitle}`)).not.toBeVisible({ timeout: 5000 });

      console.log(`Ticket ${ticketKey} successfully deleted and board redirect verified`);
    } catch (err) {
      // Cleanup: ensure test ticket is deleted even if test fails
      await page.request
        .delete(`${API}/api/tickets/${ticketKey}`, {
          headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
        })
        .catch(() => {});
      throw err;
    }
  });

  test("non-admin user does not see delete button in DOM", async ({ page }) => {
    // Inject PM token (role=pm, not admin) into localStorage before navigation
    // Use addInitScript so the token is in localStorage before the React app boots
    await page.addInitScript((t) => {
      window.localStorage.setItem("projecthub.token", t);
    }, PM_TOKEN);

    // Navigate to PH-47 ticket detail (current ticket, always exists)
    await page.goto(`${BASE}/boards/PH/tickets/PH-47`);

    // Wait for ticket detail to load — the title heading for PH-47
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 10000 });

    // AC: non-admin must NOT see the delete button — not even hidden, must be absent from DOM
    await expect(page.getByTestId("delete-ticket-button")).toHaveCount(0);

    console.log("Non-admin user correctly sees no delete button in DOM");
  });
});
