import { test, expect } from "@playwright/test";

const TOKEN = "change-me-on-first-login";

test("WS direct to backend (bypass vite proxy)", async ({ page }) => {
  const wsFrames: string[] = [];
  let wsConnected = false;
  let wsError = "";

  page.on("websocket", (ws) => {
    console.log(`WS opened: ${ws.url()}`);
    ws.on("framereceived", (f) => {
      const payload = String(f.payload);
      wsFrames.push(payload);
      console.log(`WS frame: ${payload.slice(0, 300)}`);
    });
    ws.on("close", () => console.log("WS closed"));
    ws.on("socketerror", (err) => console.log(`WS error: ${err}`));
  });

  // Navigate to a blank page then open WS directly to backend
  await page.goto("http://localhost:5173");
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);

  // Open WS directly to backend:8000 (no vite proxy)
  const result = await page.evaluate(async (token) => {
    return new Promise<{ connected: boolean; frames: string[]; error: string }>((resolve) => {
      const frames: string[] = [];
      let error = "";
      const ws = new WebSocket(`ws://localhost:8000/ws/boards/PH?token=${token}`);
      ws.onopen = () => {
        console.log("Direct WS connected!");
      };
      ws.onmessage = (e) => {
        frames.push(e.data);
        console.log("Direct WS msg:", e.data);
      };
      ws.onerror = (e) => {
        error = "WS error";
        console.log("Direct WS error");
      };
      ws.onclose = (e) => {
        console.log("Direct WS closed:", e.code, e.reason);
      };

      // Wait 3s, then check
      setTimeout(() => {
        const connected = ws.readyState === WebSocket.OPEN;
        resolve({ connected, frames, error });
        ws.close();
      }, 3000);
    });
  }, TOKEN);

  console.log("Direct WS result:", JSON.stringify(result));
  expect(result.connected).toBeTruthy();
});
