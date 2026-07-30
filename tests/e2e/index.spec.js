// E2E tests for the HF Model Indexer frontend.
//
// Guards browser-only behaviors that unit/data tests can't catch:
//   - DuckDB WASM worker init (cross-origin blob worker)
//   - Parquet registration via HTTP Range / buffer fallback
//   - Parameterized query binder (e.g. TIMESTAMPTZ date filters)
//
// All checks run within a SINGLE page load. The first load downloads the
// ~39MB DuckDB wasm, so reloading per test (the Playwright default) makes the
// suite far slower for no benefit. We use test.step() for per-filter reporting.

const { test, expect } = require("@playwright/test");

// Navigate to the explicit SITE_URL. Do NOT use page.goto("/") - Playwright
// resolves a bare "/" against the baseURL ORIGIN, dropping any subpath
// (e.g. https://user.github.io/hf-indexer/ -> https://user.github.io/).
const SITE_URL = process.env.SITE_URL || "https://amagyar.github.io/hf-indexer/";

const READY_TIMEOUT = 180_000; // first load fetches ~39MB wasm from CDN
const QUERY_TIMEOUT = 90_000;

async function waitForReady(page) {
  // domcontentloaded is enough; the explicit Ready wait below covers DuckDB init.
  await page.goto(SITE_URL, { waitUntil: "domcontentloaded" });
  // Fail fast with a clear signal if we landed on the wrong page.
  await expect(page).toHaveTitle(/Hugging Face Model Indexer/);
  await expect(page.locator("#status-banner")).toHaveClass(/ready/, { timeout: READY_TIMEOUT });
  await expect(page.locator("#error-banner")).toBeHidden();
}

/** Reset every filter to its default (empty/Any), then apply the given ones. */
async function setFilters(page, f) {
  await page.locator("#filter-form").evaluate((form) => form.reset());
  if (f.id) await page.locator("#f-id").fill(f.id);
  if (f.minSize != null) await page.locator("#f-min-size").fill(String(f.minSize));
  if (f.maxSize != null) await page.locator("#f-max-size").fill(String(f.maxSize));
  if (f.quant) await page.locator("#f-quant").selectOption(f.quant);
  if (f.createdFrom) await page.locator("#f-created-from").fill(f.createdFrom);
  if (f.createdTo) await page.locator("#f-created-to").fill(f.createdTo);
  if (f.modifiedFrom) await page.locator("#f-modified-from").fill(f.modifiedFrom);
  if (f.modifiedTo) await page.locator("#f-modified-to").fill(f.modifiedTo);
}

/** Run a search and assert it succeeds (rows render, no error banner). */
async function searchAndExpectSuccess(page) {
  await page.locator("#search-btn").click();
  // Wait for either the success indicator or the error banner, then require success.
  await Promise.race([
    expect(page.locator("#row-count")).toContainText(/model/i, { timeout: QUERY_TIMEOUT }),
    expect(page.locator("#error-banner")).toBeVisible({ timeout: QUERY_TIMEOUT }),
  ]);
  await expect(page.locator("#error-banner")).toBeHidden();
  await expect(page.locator("#row-count")).toContainText(/model/i);
}

test("HF Model Indexer: init + every filter", async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await waitForReady(page);

  await test.step("unfiltered search returns rows", async () => {
    await setFilters(page, {});
    await searchAndExpectSuccess(page);
    expect(await page.locator("#results-body tr").count()).toBeGreaterThan(0);
  });

  await test.step("text search filter", async () => {
    await setFilters(page, { id: "llama" });
    await searchAndExpectSuccess(page);
  });

  await test.step("size range filter", async () => {
    await setFilters(page, { minSize: 7, maxSize: 70 });
    await searchAndExpectSuccess(page);
  });

  await test.step("quantization filter", async () => {
    await setFilters(page, { quant: "gguf" });
    await searchAndExpectSuccess(page);
  });

  // Regression guard: comparing a string param to a TIMESTAMPTZ column threw a
  // Binder Error before the CAST(? AS TIMESTAMPTZ) fix.
  await test.step("created_at date filter (TIMESTAMPTZ binder regression)", async () => {
    await setFilters(page, { createdFrom: "2024-01-01" });
    await searchAndExpectSuccess(page);
  });

  await test.step("modified_at date range filter", async () => {
    await setFilters(page, { modifiedFrom: "2025-01-01", modifiedTo: "2026-12-31" });
    await searchAndExpectSuccess(page);
  });

  await test.step("combined filters", async () => {
    await setFilters(page, {
      id: "llama", minSize: 7, maxSize: 70,
      createdFrom: "2023-01-01", quant: "gguf",
    });
    await searchAndExpectSuccess(page);
  });

  // Hard-fail on uncaught exceptions only. Console errors are attached to the
  // report for visibility but don't fail the run - DuckDB/wasm/deps can emit
  // benign console.error noise (e.g. deprecation notices) that isn't a real bug.
  expect(pageErrors).toEqual([]);
  if (consoleErrors.length) {
    test.info().attach("console-errors", {
      contentType: "text/plain",
      body: consoleErrors.join("\n"),
    });
  }
});
