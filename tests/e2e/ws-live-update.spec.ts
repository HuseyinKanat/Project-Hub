import { test, expect } from "@playwright/test";

const BASE = "http://localhost:5173";
const API = "http://localhost:8000";
const TOKEN = "change-me-on-first-login";

test.describe("PH-26: WebSocket live board updates", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE);
    await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);
  });

  test("new ticket appears on board without refresh", async ({ page }) => {
    // Listen for WS BEFORE navigating
    const wsMessages: string[] = [];
    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        console.log(`Board WS opened: ${ws.url()}`);
        ws.on("framereceived", (frame) => {
          const payload = String(frame.payload);
          console.log(`Board WS frame: ${payload.slice(0, 200)}`);
          wsMessages.push(payload);
        });
        ws.on("close", () => console.log("Board WS closed"));
      }
    });

    await page.goto(`${BASE}/boards/PH`);

    // Wait for tickets to render
    await page.waitForSelector("a[href*='/boards/PH/tickets/']", { timeout: 10000 });

    const initialCards = await page.locator("a[href*='/boards/PH/tickets/']").count();
    console.log(`Initial ticket count: ${initialCards}`);

    // Wait for WS to stabilize (Strict Mode double mount)
    await page.waitForTimeout(3000);

    // Create a new ticket via API (directly to backend)
    const uniqueTitle = `E2E-Test-${Date.now()}`;
    const resp = await page.request.post(`${API}/api/tickets`, {
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        board_id: "PH",
        type: "bug",
        title: uniqueTitle,
        priority: "low",
        labels: ["e2e-test"],
      },
    });
    expect(resp.ok()).toBeTruthy();
    const created = await resp.json();
    console.log(`Created ticket: ${created.key} - ${created.title}`);

    // Wait for WS event + UI update
    await page.waitForTimeout(5000);

    // Check if WS "created" event was received
    const createdMsg = wsMessages.find((m) => m.includes('"created"'));
    console.log(`WS created event received: ${!!createdMsg}`);
    if (createdMsg) console.log("WS event:", createdMsg.slice(0, 200));

    // Check if the new card appeared
    const newCard = page.locator(`text=${uniqueTitle}`);
    const isVisible = await newCard.isVisible();
    console.log(`New ticket visible on board: ${isVisible}`);

    // Screenshot for evidence
    await page.screenshot({ path: "/tmp/ws-test-after-create.png", fullPage: true });

    expect(createdMsg).toBeTruthy();
    expect(isVisible).toBeTruthy();

    // Cleanup: delete the test ticket
    await page.request.delete(`${API}/api/tickets/${created.key}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
  });

  test("ticket state change reflects live on board", async ({ page }) => {
    const wsMessages: string[] = [];
    page.on("websocket", (ws) => {
      if (ws.url().includes("/ws/boards/")) {
        ws.on("framereceived", (f) => {
          wsMessages.push(String(f.payload));
          console.log(`Board WS frame: ${String(f.payload).slice(0, 200)}`);
        });
      }
    });

    await page.goto(`${BASE}/boards/PH`);
    await page.waitForSelector("a[href*='/boards/PH/tickets/']", { timeout: 10000 });
    await page.waitForTimeout(3000);

    // Find a backlog ticket to transition
    const ticketsResp = await page.request.get(
      `${API}/api/tickets?board_key=PH&limit=100`,
      { headers: { Authorization: `Bearer ${TOKEN}` } }
    );
    const tickets = (await ticketsResp.json()).tickets;
    const backlogTicket = tickets.find((t: any) => t.state === "backlog");

    if (!backlogTicket) {
      console.log("No backlog ticket to test state change, skipping");
      test.skip();
      return;
    }

    console.log(`Testing state change on: ${backlogTicket.key}`);

    // Transition backlog → to_do
    const transResp = await page.request.post(
      `${API}/api/tickets/${backlogTicket.key}/transition/to_do`,
      { headers: { Authorization: `Bearer ${TOKEN}` } }
    );
    expect(transResp.ok()).toBeTruthy();

    // Wait for WS event
    await page.waitForTimeout(3000);

    const stateMsg = wsMessages.find(
      (m) => m.includes("state_changed") && m.includes(backlogTicket.id)
    );
    console.log(`WS state_changed event received: ${!!stateMsg}`);
    if (stateMsg) console.log("WS event:", stateMsg.slice(0, 200));

    await page.screenshot({ path: "/tmp/ws-test-after-transition.png", fullPage: true });

    expect(stateMsg).toBeTruthy();

    // Revert
    await page.request.post(
      `${API}/api/tickets/${backlogTicket.key}/transition/backlog`,
      { headers: { Authorization: `Bearer ${TOKEN}` } }
    );
  });
});
