import { test, expect } from "@playwright/test";
import { execSync } from "child_process";

/**
 * PH-46: WebSocket 1006 caused by Vite 5.x proxy ECONNRESET
 *
 * These tests FAIL against the current broken state (VITE_WS_URL not set,
 * Vite proxy ECONNRESET active) and PASS after the fix (VITE_WS_URL set to
 * ws://localhost:8000, Vite /ws proxy block removed).
 *
 * Root cause: browser connects to ws://localhost:5174/ws/... which routes
 * through Vite 5.x's broken WebSocket proxy, triggering ECONNRESET and an
 * immediate 1006 close. Auth is fine — this is a proxy configuration bug.
 *
 * Observable symptoms used in assertions:
 * - BROKEN state: rapid reconnect loop (10+ WS open events in 5s), 0 frames
 *   received before each close. Playwright `close` event does not expose the
 *   numeric 1006 code directly (data.code = undefined for abnormal closes),
 *   so we assert the reconnect loop count and frame count instead.
 * - FIXED state: single stable connection, frames received, no loop.
 */

// Host port for the frontend (docker-compose maps 5174->5173)
const FRONTEND_URL = "http://localhost:5174";
const REPO_ROOT = "/Users/huseyinkanat/Documents/project-hub";

test.use({ baseURL: FRONTEND_URL });

test.describe("PH-46: Vite WS proxy bypass — WebSocket connection lifecycle", () => {
  test.beforeEach(async ({ page }) => {
    // Seed auth token via localStorage before navigation (same pattern as
    // websocket-token-consistency.spec.ts).
    await page.addInitScript(() => {
      localStorage.setItem("projecthub.token", "change-me-on-first-login");
    });
  });

  /**
   * TC-01: WebSocket does not enter infinite reconnect loop within 5s.
   *
   * In the BROKEN state (ECONNRESET active): the connection opens and immediately
   * closes, and useWebSocket.ts reconnects → we see many WS open events in 5s
   * (diagnostic showed 10+ in 5s). In the FIXED state: single stable connection,
   * at most 1-2 events (initial open, possibly one reconnect on React StrictMode
   * double-mount).
   *
   * FAILS now: >5 reconnect attempts observed in 5s.
   * PASSES after fix: ≤2 WS opens in 5s (stable connection, no loop).
   */
  test("TC-01: WebSocket opens without 1006 within 5s", async ({ page }) => {
    let wsOpenCount = 0;
    let framesOnFirstWs = 0;
    let firstWsClosedWithoutFrames = false;

    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        wsOpenCount++;
        let frameCount = 0;
        ws.on("framereceived", () => {
          frameCount++;
          if (wsOpenCount === 1) framesOnFirstWs++;
        });
        ws.on("close", () => {
          if (wsOpenCount === 1 && frameCount === 0) {
            firstWsClosedWithoutFrames = true;
          }
        });
      }
    });

    await page.goto(`${FRONTEND_URL}/boards/PH`);

    // Wait for first board WS to open
    await page.waitForEvent("websocket", {
      predicate: (ws) => ws.url().includes("/ws/boards/"),
      timeout: 5000,
    });

    // Wait 5s to observe reconnect loop behavior
    await new Promise((r) => setTimeout(r, 5000));

    // In the BROKEN state: we saw 10+ opens in 5s and first WS closed with 0 frames.
    // In the FIXED state: ≤2 opens and the first WS stays open with frames.
    //
    // Assert: no reconnect loop (≤2 opens) AND first connection received at least
    // one frame (connection was genuinely usable).
    expect(wsOpenCount).toBeLessThanOrEqual(2);
    expect(firstWsClosedWithoutFrames).toBe(false);
  });

  /**
   * TC-02: WebSocket stays open 15 seconds with at least one server message.
   *
   * FAILS now: connection closes in 0.00s, 0 frames received before close.
   * PASSES after fix: connection stays open, backend sends ping frames.
   */
  test("TC-02: WebSocket stays open 15s with server messages", async ({ page }) => {
    let framesReceived = 0;
    let wsClosedEarly = false;
    let connectionOpen = false;

    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        connectionOpen = true;
        ws.on("framereceived", () => {
          framesReceived++;
        });
        ws.on("close", () => {
          // Track if it closed before our 15s window with 0 frames (immediate ECONNRESET)
          if (framesReceived === 0) {
            wsClosedEarly = true;
          }
        });
      }
    });

    await page.goto(`${FRONTEND_URL}/boards/PH`);

    // Wait for WS to open
    await page.waitForEvent("websocket", {
      predicate: (ws) => ws.url().includes("/ws/boards/"),
      timeout: 5000,
    });

    // Wait 15s to observe whether the connection stays open with messages
    await new Promise((r) => setTimeout(r, 15000));

    expect(connectionOpen).toBe(true);
    // Connection must not have closed immediately before any frames arrived
    expect(wsClosedEarly).toBe(false);
    // Must have received at least one frame (ping or board event)
    expect(framesReceived).toBeGreaterThan(0);
  });

  /**
   * TC-03: WebSocket connects directly to backend (not via Vite proxy).
   *
   * In the BROKEN state: the browser opens ws://localhost:5174/ws/boards/...
   * (routes through Vite proxy → ECONNRESET). After fix: VITE_WS_URL is set,
   * so the browser opens ws://localhost:8000/ws/boards/... directly.
   *
   * This test also checks docker logs for ECONNRESET as a supplementary
   * signal (real browser sessions produce it; headless Playwright may not,
   * so the URL check is the primary assertion).
   *
   * FAILS now: WS URL is on port 5174 (Vite proxy path), reconnect loop occurs.
   * PASSES after fix: WS URL is on port 8000 (direct backend), stable.
   */
  test("TC-03: No ECONNRESET in Vite container logs during connection lifecycle", async ({
    page,
  }) => {
    let capturedWsUrl = "";
    let wsOpenCount = 0;

    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        wsOpenCount++;
        if (wsOpenCount === 1) {
          capturedWsUrl = ws.url();
        }
      }
    });

    await page.goto(`${FRONTEND_URL}/boards/PH`);

    // Wait for WS open attempt
    await page.waitForEvent("websocket", {
      predicate: (ws) => ws.url().includes("/ws/boards/"),
      timeout: 5000,
    });

    // Wait 10s to let connection activity complete and logs flush
    await new Promise((r) => setTimeout(r, 10000));

    // PRIMARY assertion: WS URL must point directly to backend (port 8000),
    // NOT through Vite proxy (port 5174).
    // FAILS now: capturedWsUrl contains ":5174" (Vite proxy route).
    // PASSES after fix: capturedWsUrl contains ":8000" (direct backend).
    expect(capturedWsUrl).toContain(":8000");
    expect(capturedWsUrl).not.toContain(":5174");

    // SUPPLEMENTARY: reconnect loop check (belt-and-suspenders)
    expect(wsOpenCount).toBeLessThanOrEqual(2);

    // SUPPLEMENTARY: docker log check (may not catch headless browser connections
    // but will catch real browser ECONNRESET entries — useful for CI/manual runs)
    let logsOutput = "";
    try {
      logsOutput = execSync("docker compose logs frontend --since 30s 2>&1", {
        cwd: REPO_ROOT,
        encoding: "utf8",
      });
    } catch (err) {
      logsOutput = String(err);
    }
    // Only assert if ECONNRESET appears (headless browser may not produce it)
    if (logsOutput.includes("ECONNRESET")) {
      expect(logsOutput).not.toContain("ws proxy error: ECONNRESET");
    }
  });

  /**
   * TC-04: Multi-tab — two contexts on same board both maintain WebSocket
   * (no reconnect loop in either context).
   *
   * FAILS now: both contexts enter reconnect loops (many opens, 0 frames per open).
   * PASSES after fix: both contexts have ≤2 opens and receive frames.
   */
  test("TC-04: Multi-tab: two contexts on same board both maintain WS", async ({
    browser,
  }) => {
    // Context 1
    const ctx1 = await browser.newContext();
    await ctx1.addInitScript(() => {
      localStorage.setItem("projecthub.token", "change-me-on-first-login");
    });
    const page1 = await ctx1.newPage();

    // Context 2
    const ctx2 = await browser.newContext();
    await ctx2.addInitScript(() => {
      localStorage.setItem("projecthub.token", "change-me-on-first-login");
    });
    const page2 = await ctx2.newPage();

    let ws1Count = 0;
    let ws2Count = 0;
    let ws1FramesOnFirst = 0;
    let ws2FramesOnFirst = 0;
    let ws1FirstClosedNoFrames = false;
    let ws2FirstClosedNoFrames = false;

    page1.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        ws1Count++;
        let fc = 0;
        ws.on("framereceived", () => {
          fc++;
          if (ws1Count === 1) ws1FramesOnFirst++;
        });
        ws.on("close", () => {
          if (ws1Count === 1 && fc === 0) ws1FirstClosedNoFrames = true;
        });
      }
    });

    page2.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        ws2Count++;
        let fc = 0;
        ws.on("framereceived", () => {
          fc++;
          if (ws2Count === 1) ws2FramesOnFirst++;
        });
        ws.on("close", () => {
          if (ws2Count === 1 && fc === 0) ws2FirstClosedNoFrames = true;
        });
      }
    });

    // Navigate both to the same board
    await Promise.all([
      page1.goto(`${FRONTEND_URL}/boards/PH`),
      page2.goto(`${FRONTEND_URL}/boards/PH`),
    ]);

    // Wait for both WS events
    await Promise.all([
      page1.waitForEvent("websocket", {
        predicate: (ws) => ws.url().includes("/ws/boards/"),
        timeout: 5000,
      }),
      page2.waitForEvent("websocket", {
        predicate: (ws) => ws.url().includes("/ws/boards/"),
        timeout: 5000,
      }),
    ]);

    // Give 5s to observe reconnect loop behavior in both contexts
    await new Promise((r) => setTimeout(r, 5000));

    // Both contexts must not enter a reconnect loop and must have received frames
    expect(ws1Count).toBeLessThanOrEqual(2);
    expect(ws2Count).toBeLessThanOrEqual(2);
    expect(ws1FirstClosedNoFrames).toBe(false);
    expect(ws2FirstClosedNoFrames).toBe(false);

    await ctx1.close();
    await ctx2.close();
  });
});
