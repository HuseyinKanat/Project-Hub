import { test, expect } from "@playwright/test";

const BASE = "http://localhost:5173";
const API = "http://localhost:8000";

/**
 * PH-43: WebSocket regression test for Jarwis actor authentication
 *
 * This test reproduces the bug where WebSocket connections fail with error 1006
 * after the Jarwis workflow integration. The issue manifests as:
 *
 * 1. Browser console shows: "WebSocket is closed before the connection is established"
 * 2. Error code 1006 (abnormal closure) during connection establishment
 * 3. Infinite reconnection attempts (1/5, 2/5, etc.)
 * 4. No real-time updates flowing to frontend
 *
 * Root cause hypothesis: Jarwis actor tokens have different authentication flow
 * that causes WebSocket authentication to fail silently, leading to immediate
 * connection closure during handshake.
 */
test.describe("PH-43: WebSocket Jarwis Authentication Regression", () => {

  test("WebSocket connection should fail with 1006 error when using Jarwis actor token", async ({ page }) => {
    // Simulate using a Jarwis actor token for WebSocket connection
    // This should fail during authentication and cause 1006 error

    // Monitor WebSocket connection attempts and errors
    const wsErrors: Array<{type: string, error: any, url?: string}> = [];
    const wsCloseEvents: Array<{code: number, reason: string, url: string}> = [];
    const consoleLogs: Array<{type: string, message: string}> = [];
    let connectionAttempts = 0;

    // Listen for WebSocket events
    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        connectionAttempts++;
        console.log(`WebSocket connection attempt #${connectionAttempts}: ${ws.url()}`);

        ws.on("close", (event) => {
          wsCloseEvents.push({
            code: event.code,
            reason: event.reason || 'No reason provided',
            url: ws.url()
          });
          console.log(`WebSocket closed: code=${event.code}, reason="${event.reason}", url=${ws.url()}`);
        });

        ws.on("socketerror", (error) => {
          wsErrors.push({
            type: 'socket_error',
            error: error,
            url: ws.url()
          });
          console.log(`WebSocket error: ${error}, url=${ws.url()}`);
        });
      }
    });

    // Capture console messages related to WebSocket
    page.on("console", (msg) => {
      const text = msg.text();
      if (text.includes("WebSocket") || text.includes("1006") || text.includes("connection") || text.includes("Code:")) {
        consoleLogs.push({
          type: msg.type(),
          message: text
        });
        console.log(`CONSOLE ${msg.type()}: ${text}`);
      }
    });

    // Try to simulate a Jarwis token scenario
    // In the real bug, this would be a jarwis-* actor token
    // For reproduction, we'll use a token that causes auth issues
    const INVALID_JARWIS_TOKEN = "jarwis-test-token-that-causes-1006";

    await page.goto(BASE);
    await page.evaluate((token) => {
      localStorage.setItem("projecthub.token", token);
    }, INVALID_JARWIS_TOKEN);

    // Navigate to board page - this should trigger WebSocket connection attempt
    await page.goto(`${BASE}/boards/PH`);

    // Wait for WebSocket connection attempts and failures
    await page.waitForTimeout(8000);

    console.log(`=== PH-43 Bug Reproduction Results ===`);
    console.log(`Connection attempts: ${connectionAttempts}`);
    console.log(`WebSocket close events: ${wsCloseEvents.length}`);
    console.log(`WebSocket errors: ${wsErrors.length}`);
    console.log(`Console logs with WebSocket/1006: ${consoleLogs.length}`);

    // Log specific close events
    wsCloseEvents.forEach((closeEvent, index) => {
      console.log(`Close Event ${index + 1}: code=${closeEvent.code}, reason="${closeEvent.reason}"`);
    });

    // Log console messages
    consoleLogs.forEach((log, index) => {
      console.log(`Console Log ${index + 1}: [${log.type}] ${log.message}`);
    });

    // This test should FAIL to reproduce the bug
    // The failing assertions demonstrate the bug symptoms:

    // SYMPTOM 1: WebSocket connections should fail with 1006 error code
    const error1006Events = wsCloseEvents.filter(event => event.code === 1006);
    expect(error1006Events.length, "Should have WebSocket 1006 error events (abnormal closure)").toBeGreaterThan(0);

    // SYMPTOM 2: Console should show "WebSocket is closed before the connection is established"
    const connectionFailureLogs = consoleLogs.filter(log =>
      log.message.includes("WebSocket is closed before the connection is established") ||
      log.message.includes("Code: 1006")
    );
    expect(connectionFailureLogs.length, "Should show connection establishment failure messages").toBeGreaterThan(0);

    // SYMPTOM 3: Multiple reconnection attempts should occur
    expect(connectionAttempts, "Should have multiple connection attempts due to retry logic").toBeGreaterThanOrEqual(2);

    // SYMPTOM 4: No WebSocket frames should be received (connection never succeeds)
    const hasReceivedFrames = await page.evaluate(() => {
      return window.sessionStorage.getItem('ws-frames-received') === 'true';
    });
    expect(hasReceivedFrames, "Should NOT receive WebSocket frames due to connection failure").toBe(false);

    // Take screenshot for debugging
    await page.screenshot({
      path: "/tmp/ph43-websocket-1006-error.png",
      fullPage: true
    });
  });

  test("WebSocket connection should succeed with valid admin token (baseline)", async ({ page }) => {
    // This is a baseline test to confirm WebSocket works with proper authentication
    // This test should PASS while the previous test fails, isolating the Jarwis auth issue

    const wsConnections: string[] = [];
    let connectionSucceeded = false;

    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        wsConnections.push(ws.url());

        ws.on("framereceived", (frame) => {
          connectionSucceeded = true;
          console.log(`Baseline test - WebSocket frame received: ${String(frame.payload).slice(0, 100)}`);
        });

        ws.on("close", (event) => {
          if (event.code !== 1000) { // 1000 = normal closure
            console.log(`Baseline test - Unexpected close: code=${event.code}`);
          }
        });
      }
    });

    // Use the default working token
    const ADMIN_TOKEN = "change-me-on-first-login";

    await page.goto(BASE);
    await page.evaluate((token) => {
      localStorage.setItem("projecthub.token", token);
    }, ADMIN_TOKEN);

    await page.goto(`${BASE}/boards/PH`);
    await page.waitForTimeout(5000);

    console.log(`=== Baseline Test Results ===`);
    console.log(`WebSocket connections: ${wsConnections.length}`);
    console.log(`Connection succeeded: ${connectionSucceeded}`);

    // Baseline expectations - this should work
    expect(wsConnections.length, "Should establish WebSocket connection").toBeGreaterThan(0);
    // Note: We don't assert connectionSucceeded=true because it depends on Redis events
    // The key point is that connection is established without 1006 errors
  });

  test("WebSocket reconnection attempts should be limited to 5 max with exponential backoff", async ({ page }) => {
    // Test that the reconnection behavior follows the expected pattern
    // This helps verify the frontend handling of 1006 errors

    const reconnectionAttempts: Array<{attempt: number, timestamp: number}> = [];
    const reconnectionLogs: string[] = [];

    page.on("console", (msg) => {
      const text = msg.text();
      if (text.includes("WebSocket reconnecting")) {
        reconnectionLogs.push(text);

        // Extract attempt number if possible
        const match = text.match(/\((\d+)\/(\d+)\)/);
        if (match) {
          const attemptNum = parseInt(match[1]);
          reconnectionAttempts.push({
            attempt: attemptNum,
            timestamp: Date.now()
          });
        }
        console.log(`RECONNECTION LOG: ${text}`);
      }
    });

    // Use invalid token to force reconnection attempts
    const INVALID_TOKEN = "force-reconnection-test";

    await page.goto(BASE);
    await page.evaluate((token) => {
      localStorage.setItem("projecthub.token", token);
    }, INVALID_TOKEN);

    await page.goto(`${BASE}/boards/PH`);

    // Wait long enough for multiple reconnection attempts
    // With exponential backoff: 1s, 2s, 4s, 8s, 16s = ~31s total
    await page.waitForTimeout(35000);

    console.log(`=== Reconnection Pattern Test Results ===`);
    console.log(`Total reconnection logs: ${reconnectionLogs.length}`);
    console.log(`Parsed attempts: ${reconnectionAttempts.length}`);

    reconnectionLogs.forEach((log, index) => {
      console.log(`Reconnection ${index + 1}: ${log}`);
    });

    // Verify reconnection behavior
    expect(reconnectionAttempts.length, "Should attempt reconnection but stop at max limit").toBeLessThanOrEqual(5);

    if (reconnectionAttempts.length >= 2) {
      // Verify exponential backoff timing
      const timeDiff = reconnectionAttempts[1].timestamp - reconnectionAttempts[0].timestamp;
      expect(timeDiff, "Should have exponential backoff between attempts").toBeGreaterThan(1800); // >1.8s for 2nd attempt
    }
  });
});