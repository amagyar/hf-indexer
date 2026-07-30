// Playwright config for the HF Model Indexer E2E tests.
//
// Tests load the deployed GitHub Pages site by default and exercise the
// DuckDB WASM frontend end-to-end (init, parquet load, every filter).
// Override the target with: SITE_URL=http://127.0.0.1:8000/ npx playwright test
const { defineConfig, devices } = require("@playwright/test");

const SITE_URL = process.env.SITE_URL || "https://amagyar.github.io/hf-indexer/";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false, // the live site shares a single Parquet; run sequentially
  timeout: 180_000,            // generous: first load downloads ~39MB of wasm
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: SITE_URL,
    actionTimeout: 60_000,
    navigationTimeout: 60_000,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
