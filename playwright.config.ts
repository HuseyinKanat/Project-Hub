import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30000,
  // PH-137: serialise all tests (workers: 1) because the 6 in-scope specs all
  // mutate shared live PH workflow state — parallel execution would cause races.
  workers: 1,
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",
  use: {
    baseURL: "http://localhost:5173",
    headless: true,
  },
});
