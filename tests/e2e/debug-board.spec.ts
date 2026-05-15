import { test } from "@playwright/test";

test("debug board page", async ({ page }) => {
  await page.goto("http://localhost:5173");
  await page.evaluate((t) => localStorage.setItem("projecthub.token", t), "change-me-on-first-login");
  await page.goto("http://localhost:5173/boards/PH");
  await page.waitForTimeout(5000);

  // Screenshot
  await page.screenshot({ path: "/tmp/board-debug.png", fullPage: true });

  // Dump page content
  const html = await page.content();
  const fs = require("fs");
  fs.writeFileSync("/tmp/board-debug.html", html);

  // Console logs
  const logs: string[] = [];
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  await page.waitForTimeout(2000);
  
  // Check for WS connections
  const wsUrls: string[] = [];
  page.on("websocket", (ws) => wsUrls.push(ws.url()));
  await page.waitForTimeout(3000);
  
  console.log("Page URL:", page.url());
  console.log("Page title:", await page.title());
  console.log("WS connections:", wsUrls);
  
  // Get all links
  const links = await page.$$eval("a", (els) => els.map((e) => e.getAttribute("href")).filter(Boolean));
  console.log("Links on page:", links.slice(0, 20));
});
