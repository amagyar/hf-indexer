// E2E tests for the search-quality & UX features (search-quality-and-ux-pack):
//   - min-downloads filter
//   - hide-empty-repos checkbox (incl. column feature-detect on old shards)
//   - per-row actions: copy download command, block author
//   - localStorage author blocklist + management modal + hidden feedback
//   - CSV / JSON export of the active query
//
// Runs against a DETERMINISTIC local catalog (SITE_URL=http://127.0.0.1:8000)
// crafted by the verification scripts:
//   orgGood  300 models  has_weights=true   downloads 0..299  (llama/gguf/apache)
//   orgSpam  120 models  has_weights=false  downloads 0..19
//   orgNull   40 models  has_weights=null   (legacy, no field)
// The old-shape variant at :8001 (SITE_URL_OLD) publishes no has_weights column.

const { test, expect } = require("@playwright/test");

const SITE_URL = process.env.SITE_URL || "http://127.0.0.1:8000/";
const SITE_URL_OLD = process.env.SITE_URL_OLD || "http://127.0.0.1:8001/";
const SITE_URL_BIG = process.env.SITE_URL_BIG || "http://127.0.0.1:8002/";

const READY_TIMEOUT = 180_000;
const QUERY_TIMEOUT = 90_000;

async function waitForReady(page, url = SITE_URL) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle(/Hugging Face Model Indexer/);
  await expect(page.locator("#status-banner")).toHaveClass(/ready/, { timeout: READY_TIMEOUT });
  await expect(page.locator("#error-banner")).toBeHidden();
}

async function setFilters(page, f = {}) {
  await page.locator("#filter-form").evaluate((form) => form.reset());
  // form.reset() restores the checked attribute default (hide-empty ON).
  if (f.id) await page.locator("#f-id").fill(f.id);
  if (f.minSize != null) await page.locator("#f-min-size").fill(String(f.minSize));
  if (f.maxSize != null) await page.locator("#f-max-size").fill(String(f.maxSize));
  if (f.format) await page.locator("#f-format").fill(f.format);
  if (f.license) await page.locator("#f-license").fill(f.license);
  if (f.minDownloads != null) await page.locator("#f-min-downloads").fill(String(f.minDownloads));
  if (f.hideEmpty === false) await page.locator("#f-hide-empty").uncheck();
  if (f.hideEmpty === true) await page.locator("#f-hide-empty").check();
}

async function search(page) {
  await page.locator("#search-btn").click();
  await Promise.race([
    expect(page.locator("#row-count")).toContainText(/model/i, { timeout: QUERY_TIMEOUT }),
    expect(page.locator("#error-banner")).toBeVisible({ timeout: QUERY_TIMEOUT }),
  ]);
  await expect(page.locator("#error-banner")).toBeHidden();
  const text = await page.locator("#row-count").textContent();
  const matched = /(\d+) models? matched/.exec(text || "");
  return matched ? Number(matched[1]) : 0;
}

test.describe.configure({ mode: "serial" });

test("min-downloads filter narrows results", async ({ page }) => {
  await waitForReady(page);

  const all = await search(page);
  expect(all).toBe(340); // hide-empty ON: orgGood(300) + orgNull(40); orgSpam hidden

  await setFilters(page, { minDownloads: 100 });
  const filtered = await search(page);
  // downloads >= 100 keeps orgGood i=100..299 (200); orgNull (downloads=50)
  // and orgSpam (max 19) fall below the threshold.
  expect(filtered).toBe(200);

  await setFilters(page, { minDownloads: 10000 });
  const none = await search(page);
  expect(none).toBe(0);
  await expect(page.locator("#results-body .empty-row")).toBeVisible();
});

test("hide-empty checkbox: default hides confirmed-empty only", async ({ page }) => {
  await waitForReady(page);

  // Default (checked): orgSpam (has_weights=false) hidden, orgNull (null) kept.
  expect(await search(page)).toBe(340);

  // Unchecked: everything visible.
  await setFilters(page, { hideEmpty: false });
  expect(await search(page)).toBe(460);

  // Blocking orgNull proves nulls were never hidden by the empty filter:
  // they're only excluded once explicitly blocked.
  await setFilters(page, {});
  expect(await search(page)).toBe(340);
});

test("block author from a row hides its models and shows the indicator", async ({ page }) => {
  await waitForReady(page);
  await page.evaluate(() => localStorage.clear());

  await setFilters(page, { hideEmpty: false });
  expect(await search(page)).toBe(460);

  // Block orgSpam from its first result row. Capture every network request
  // the action triggers: blocking + the hidden-count query must only ever
  // touch the Parquet shards (same origin) - never any third party.
  const requests = [];
  page.on("request", (req) => requests.push(req.url()));
  const spamRow = page.locator("#results-body tr", { hasText: "orgSpam/" }).first();
  await expect(spamRow).toBeVisible();
  await spamRow.locator("button[aria-label^='Block orgSpam']").click();

  // Results update without a reload; orgSpam gone -> 340.
  await expect(page.locator("#row-count")).toContainText("340 models matched", { timeout: QUERY_TIMEOUT });
  await expect(page.locator("#results-body tr", { hasText: "orgSpam/" })).toHaveCount(0);
  for (const url of requests) {
    expect(url, "blocklist must not cause non-shard network requests").toMatch(
      /^http:\/\/127\.0\.0\.1:8000\/models-\d{3}\.parquet/
    );
  }

  // Toolbar button shows the active count; hidden-results indicator appears.
  await expect(page.locator("#blocklist-btn-label")).toHaveText("Blocked (1)");
  await expect(page.locator("#hidden-count")).toBeVisible();
  await expect(page.locator("#hidden-count")).toContainText(/hidden by blocklist/);

  // Persistence: reload keeps the author blocked, storage intact.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator("#status-banner")).toHaveClass(/ready/, { timeout: READY_TIMEOUT });
  await expect(page.locator("#blocklist-btn-label")).toHaveText("Blocked (1)");
  await setFilters(page, { hideEmpty: false });
  expect(await search(page)).toBe(340);
});

test("blocklist modal: remove entry restores models", async ({ page }) => {
  await waitForReady(page);
  await page.evaluate(() => localStorage.clear());

  await setFilters(page, { hideEmpty: false });
  expect(await search(page)).toBe(460);

  await page.locator("#results-body tr", { hasText: "orgSpam/" })
    .first().locator("button[aria-label^='Block orgSpam']").click();
  await expect(page.locator("#row-count")).toContainText("340 models matched", { timeout: QUERY_TIMEOUT });

  // Open the modal from the hidden-results indicator.
  await page.locator("#hidden-count").click();
  const modal = page.locator("#blocklist-modal");
  await expect(modal).toBeVisible();
  await expect(page.locator("#blocklist-entries li")).toHaveCount(1);
  await expect(page.locator("#blocklist-entries li")).toContainText("orgSpam");

  // Remove -> models return without a manual re-query.
  await page.locator("#blocklist-entries li button", { hasText: "Remove" }).click();
  await expect(page.locator("#row-count")).toContainText("460 models matched", { timeout: QUERY_TIMEOUT });
  await expect(page.locator("#blocklist-btn-label")).toHaveText("Blocked");
  await expect(page.locator("#hidden-count")).toBeHidden();

  // Clear-all path: block two authors, then clear. (Close the modal first -
  // it stays open after a removal so multiple entries can be managed.)
  await page.locator("#blocklist-close").click();
  await expect(modal).toBeHidden();
  await page.locator("#results-body tr", { hasText: "orgSpam/" })
    .first().locator("button[aria-label^='Block orgSpam']").click();
  await expect(page.locator("#row-count")).toContainText("340 models matched", { timeout: QUERY_TIMEOUT });
  await page.locator("#blocklist-btn").click();
  await expect(modal).toBeVisible();
  await page.locator("#blocklist-clear").click();
  await expect(page.locator("#row-count")).toContainText("460 models matched", { timeout: QUERY_TIMEOUT });
  await page.locator("#blocklist-close").click();
  await expect(modal).toBeHidden();
});

test("copy download command puts the exact command on the clipboard", async ({ page }) => {
  await waitForReady(page);
  await page.evaluate(() => localStorage.clear());

  await setFilters(page, { id: "orgGood/llama-000" });
  await search(page);
  const row = page.locator("#results-body tr", { hasText: "orgGood/llama-000" }).first();
  await expect(row).toBeVisible();

  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await row.locator("button[aria-label^='Copy download command for']").click();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(clip).toBe("huggingface-cli download orgGood/llama-000");
  // Transient copied affordance.
  await expect(row.locator("button.copied")).toHaveCount(1);
});

test("CSV and JSON export download the full filtered result set", async ({ page }) => {
  await waitForReady(page);
  await page.evaluate(() => localStorage.clear());

  // Narrow enough to be fully below the export cap: min downloads 200.
  await setFilters(page, { minDownloads: 200 });
  await search(page); // 100 rendered rows (orgGood i=200..299)

  // Exports are computed client-side: during the CSV export the only network
  // traffic allowed is Parquet shard reads from the same origin.
  const requests = [];
  page.on("request", (req) => requests.push(req.url()));

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#export-csv-btn").click(),
  ]);
  for (const url of requests) {
    expect(url, "export must not cause non-shard network requests").toMatch(
      /^http:\/\/127\.0\.0\.1:8000\/models-\d{3}\.parquet/
    );
  }
  expect(download.suggestedFilename()).toMatch(/^hf-models-\d{8}T\d{6}\.csv$/);
  const csv = (await download.path()).toString();
  const { readFileSync } = require("fs");
  const csvText = readFileSync(csv, "utf8");
  const csvLines = csvText.trimEnd().split(/\r?\n/);
  expect(csvLines[0]).toBe("id,size_b,format,license,downloads,likes,modified_at,created_at");
  expect(csvLines).toHaveLength(101); // header + 100 rows (the full query, not the rendered page)

  // RFC-4180 escaping: comma-containing fields are quoted, embedded quotes
  // are doubled (licenses crafted in the fixture generator).
  const row299 = csvLines.find((l) => l.startsWith("orgGood/llama-299,"));
  expect(row299).toBeDefined();
  expect(row299).toContain('"apache-2.0, mit"');
  const row298 = csvLines.find((l) => l.startsWith("orgGood/llama-298,"));
  expect(row298).toBeDefined();
  expect(row298).toContain('"say ""hi"" license"');

  const [downloadJson] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#export-json-btn").click(),
  ]);
  expect(downloadJson.suggestedFilename()).toMatch(/^hf-models-\d{8}T\d{6}\.json$/);
  const jsonData = JSON.parse(readFileSync((await downloadJson.path()).toString(), "utf8"));
  expect(Array.isArray(jsonData)).toBe(true);
  expect(jsonData).toHaveLength(100);
  expect(jsonData[0]).toHaveProperty("id");
  expect(jsonData[0]).toHaveProperty("downloads");
  // Sorted by downloads desc: first row carries the highest counter.
  expect(jsonData[0].downloads).toBeGreaterThanOrEqual(jsonData[99].downloads);
  // JSON needs no quoting: the comma license round-trips verbatim.
  expect(jsonData[0].license).toBe("apache-2.0, mit");

  // No truncation notice below the cap.
  await expect(page.locator("#export-note")).toHaveText("");
});

test("export applies the blocklist alongside the filters", async ({ page }) => {
  await waitForReady(page);
  await page.evaluate(() => localStorage.clear());

  await setFilters(page, { minDownloads: 200, hideEmpty: false });
  await search(page);

  // Block the org that owns every row here -> export matches the (now empty) query.
  await page.locator("#results-body tr", { hasText: "orgGood/" })
    .first().locator("button[aria-label^='Block orgGood']").click();
  await expect(page.locator("#row-count")).toContainText("0 models matched", { timeout: QUERY_TIMEOUT });

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#export-csv-btn").click(),
  ]);
  const { readFileSync } = require("fs");
  const csvText = readFileSync((await download.path()).toString(), "utf8");
  expect(csvText.trimEnd().split(/\r?\n/)).toHaveLength(1); // header only
});

test("export hits the cap: truncation notice + file stops at the cap", async ({ page }) => {
  // 150k-row catalog: the unfiltered query exceeds EXPORT_ROW_CAP (100k).
  await waitForReady(page, SITE_URL_BIG);
  await setFilters(page, {});
  await search(page);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#export-csv-btn").click(),
  ]);
  const { readFileSync } = require("fs");
  const csvText = readFileSync((await download.path()).toString(), "utf8");
  expect(csvText.trimEnd().split(/\r?\n/)).toHaveLength(100_001); // header + cap
  await expect(page.locator("#export-note")).toContainText(/capped at 100,000 rows/);
});

test("feature-detect: old-shape parquet disables hide-empty, queries still work", async ({ page }) => {
  await waitForReady(page, SITE_URL_OLD);

  await expect(page.locator("#f-hide-empty")).toBeDisabled();
  await expect(page.locator("#f-hide-empty")).not.toBeChecked();
  await expect(page.locator("#hide-empty-hint")).toBeVisible();

  // Queries succeed on old shards (no has_weights predicate emitted).
  await setFilters(page, {});
  const all = await search(page);
  expect(all).toBe(460); // nothing hidden: the column is absent
});
