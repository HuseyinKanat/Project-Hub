import { test, expect } from "@playwright/test";

const BASE = "http://localhost:5173";
const TOKEN = "change-me-on-first-login";

test("debug WS connection", async ({ page }) => {
  // Listen for WS before navigating
  const wsUrls: string[] = [];
  const wsFrames: string[] = [];
  page.on("websocket", (ws) => {
    wsUrls.push(ws.url());
    console.log(`WS opened: ${ws.url()}`);
    ws.on("framereceived", (f) => {
      wsFrames.push(String(f.payload));
      console.log(`WS frame: ${String(f.payload).slice(0, 200)}`);
    });
    ws.on("close", () => console.log(`WS closed: ${ws.url()}`));
    ws.on("socketerror", (err) => console.log(`WS error: ${err}`));
  });

  // Capture console
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.text().includes("WebSocket") || msg.text().includes("ws"))
      console.log(`CONSOLE ${msg.type()}: ${msg.text()}`);
  });

  // Set token and navigate
  await page.goto(BASE);
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), TOKEN);
  await page.goto(`${BASE}/boards/PH`);

  // Wait for page to fully load and WS to connect
  await page.waitForTimeout(5000);

  // Check what token the component sees
  const storedToken = await page.evaluate(() => {
    return {
      projecthub: localStorage.getItem("projecthub.token"),
      plain: localStorage.getItem("token"),
    };
  });
  console.log("Stored tokens:", JSON.stringify(storedToken));
  console.log("WS connections found:", wsUrls.length, wsUrls);
  console.log("WS frames received:", wsFrames.length);

  // Check if WS indicator shows connected
  const pageContent = await page.textContent("body");
  const hasConnected = pageContent?.includes("Connected");
  const hasDisconnected = pageContent?.includes("Disconnected");
  console.log("UI shows Connected:", hasConnected, "Disconnected:", hasDisconnected);

  expect(wsUrls.length).toBeGreaterThan(0);
});
