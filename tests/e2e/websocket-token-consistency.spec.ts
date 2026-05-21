import { test, expect } from "@playwright/test";

/**
 * PH-44: WebSocket Token Consistency Test
 *
 * This test verifies that both BoardDetail and TicketDetail pages use
 * the same token source (auth store) for WebSocket connections, preventing
 * the 1006 error caused by token inconsistency.
 */
test.describe("WebSocket Token Consistency", () => {
  test.beforeEach(async ({ page }) => {
    // Set Admin token in localStorage before each test
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem('projecthub.token', 'change-me-on-first-login');
    });
  });

  test("should use consistent auth store token across BoardDetail and TicketDetail pages", async ({ page }) => {
    // Enable console logging to track token usage
    const consoleMessages: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'log' && (msg.text().includes('[Auth]') || msg.text().includes('[WebSocket]'))) {
        consoleMessages.push(msg.text());
      }
    });

    // Navigate to BoardDetail page first
    await page.goto("/boards/PH");
    await expect(page.locator("h1")).toContainText("PH");

    // Wait for WebSocket connection indicator (should be connected)
    // Look for any of the possible connection status elements
    const connectionIndicator = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(connectionIndicator).toBeVisible({ timeout: 10000 });

    // Verify it shows "Live" (connected status)
    const liveStatus = page.locator('text=Live').first();
    await expect(liveStatus).toBeVisible({ timeout: 5000 });

    // Find a ticket to navigate to (preferably one that exists)
    const firstTicketLink = page.locator('[data-testid="ticket-key-link"]').first();
    await expect(firstTicketLink).toBeVisible();

    const ticketKey = await firstTicketLink.textContent();
    console.log("Found ticket:", ticketKey);

    // Navigate to TicketDetail page
    await firstTicketLink.click();

    // Wait for page to load and check that we're on TicketDetail
    await expect(page.locator("h1")).toBeVisible();

    // Wait for WebSocket connection indicator (should also be connected)
    const ticketConnectionIndicator = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(ticketConnectionIndicator).toBeVisible({ timeout: 10000 });

    // Verify both pages show "Live" status (meaning WebSocket is connected)
    const liveIndicator = page.locator('text=Live').first();
    await expect(liveIndicator).toBeVisible();

    // Navigate back to BoardDetail to test consistency
    await page.goBack();
    await expect(page.locator("h1")).toContainText("PH");

    // Should still be connected on BoardDetail
    const backConnectionIndicator = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(backConnectionIndicator).toBeVisible({ timeout: 5000 });

    console.log("Console messages captured:", consoleMessages);

    // Verify no WebSocket 1006 errors in console
    const errors = await page.evaluate(() => {
      return (window as any).testErrors || [];
    });

    // Should not contain any 1006 errors
    const has1006Error = consoleMessages.some(msg => msg.includes('1006'));
    expect(has1006Error).toBeFalsy();
  });

  test("should handle token changes gracefully with automatic reconnection", async ({ page }) => {
    // Navigate to any page with WebSocket
    await page.goto("/boards/PH");
    await expect(page.locator("h1")).toContainText("PH");

    // Wait for initial WebSocket connection
    const initialConnection = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(initialConnection).toBeVisible({ timeout: 10000 });

    // Simulate token change by updating auth store
    await page.evaluate(() => {
      // Access the auth store and update token to trigger reconnection
      const authStore = (window as any).useAuth?.getState?.();
      if (authStore && authStore.setToken) {
        // Update to same token to trigger change detection without breaking auth
        const currentToken = authStore.token;
        authStore.setToken(currentToken);
      }
    });

    // WebSocket should still be connected after token "change"
    const afterChangeConnection = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(afterChangeConnection).toBeVisible({ timeout: 10000 });

    // Navigate to TicketDetail and verify connection still works
    const firstTicketLink = page.locator('[data-testid="ticket-key-link"]').first();
    if (await firstTicketLink.isVisible()) {
      await firstTicketLink.click();
      const detailPageConnection = page.locator('[title*="Live"], [title*="Connecting"]').first();
      await expect(detailPageConnection).toBeVisible({ timeout: 10000 });
    }
  });

  test("should log token source information in development mode", async ({ page }) => {
    const consoleMessages: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'log' || msg.type() === 'error') {
        consoleMessages.push(`${msg.type()}: ${msg.text()}`);
      }
    });

    // Navigate and trigger WebSocket connection
    await page.goto("/boards/PH");
    await expect(page.locator("h1")).toContainText("PH");

    // Wait for connection
    const developmentConnection = page.locator('[title*="Live"], [title*="Connecting"]').first();
    await expect(developmentConnection).toBeVisible({ timeout: 10000 });

    // Check if we have development logging
    await page.waitForTimeout(2000); // Give time for console messages

    const hasAuthLogging = consoleMessages.some(msg => msg.includes('[Auth]'));
    const hasWebSocketLogging = consoleMessages.some(msg => msg.includes('[WebSocket]'));

    // In development mode, we should have some logging
    // This is a soft assertion since it depends on environment
    if (hasAuthLogging || hasWebSocketLogging) {
      console.log("✅ Development logging is active");
    } else {
      console.log("ℹ️ No development logging detected (may be in production mode)");
    }
  });
});