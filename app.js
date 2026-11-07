// Hugging Face Model Indexer - frontend logic.
//
// Initializes DuckDB WASM (pinned JsDelivr CDN), registers the Parquet over
// HTTP Range protocol, and runs parameterized SQL queries built from the
// filter form.

import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const els = {
  statusBanner: document.getElementById("status-banner"),
  statusText: document.getElementById("status-text"),
  errorBanner: document.getElementById("error-banner"),
  themeToggle: document.getElementById("theme-toggle"),
  form: document.getElementById("filter-form"),
  searchBtn: document.getElementById("search-btn"),
  idInput: document.getElementById("f-id"),
  minSize: document.getElementById("f-min-size"),
  maxSize: document.getElementById("f-max-size"),
  format: document.getElementById("f-format"),
  license: document.getElementById("f-license"),
  createdFrom: document.getElementById("f-created-from"),
  createdTo: document.getElementById("f-created-to"),
  modifiedFrom: document.getElementById("f-modified-from"),
  modifiedTo: document.getElementById("f-modified-to"),
  minDownloads: document.getElementById("f-min-downloads"),
  hideEmpty: document.getElementById("f-hide-empty"),
  hideEmptyHint: document.getElementById("hide-empty-hint"),
  rowCount: document.getElementById("row-count"),
  resultsBody: document.getElementById("results-body"),
  hiddenCount: document.getElementById("hidden-count"),
  exportNote: document.getElementById("export-note"),
  blocklistBtn: document.getElementById("blocklist-btn"),
  blocklistBtnLabel: document.getElementById("blocklist-btn-label"),
  blocklistModal: document.getElementById("blocklist-modal"),
  blocklistEntries: document.getElementById("blocklist-entries"),
  blocklistEmpty: document.getElementById("blocklist-empty"),
  blocklistClear: document.getElementById("blocklist-clear"),
  blocklistClose: document.getElementById("blocklist-close"),
  exportCsvBtn: document.getElementById("export-csv-btn"),
  exportJsonBtn: document.getElementById("export-json-btn"),
};

// ---------------------------------------------------------------------------
// Theme toggle (dark default, persisted in localStorage)
// ---------------------------------------------------------------------------
const THEME_KEY = "hf-indexer-theme";

function applyTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  if (els.themeToggle) {
    els.themeToggle.setAttribute("aria-pressed", String(next === "light"));
    els.themeToggle.setAttribute(
      "aria-label",
      next === "light" ? "Switch to dark mode" : "Switch to light mode"
    );
  }
  return next;
}

function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch {
    stored = null;
  }
  applyTheme(stored === "light" ? "light" : "dark");

  if (els.themeToggle) {
    els.themeToggle.addEventListener("click", () => {
      const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const next = current === "light" ? "dark" : "light";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        // Storage unavailable (e.g. private mode) - theme still applies.
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Status / error helpers
// ---------------------------------------------------------------------------
function setStatus(state, text) {
  els.statusBanner.classList.remove("loading", "ready", "error");
  els.statusBanner.classList.add(state);
  els.statusText.textContent = text;
}

function showError(message) {
  els.errorBanner.textContent = message;
  els.errorBanner.classList.remove("hidden");
}

function clearError() {
  els.errorBanner.classList.add("hidden");
  els.errorBanner.textContent = "";
}

// ---------------------------------------------------------------------------
// DuckDB WASM initialization
// ---------------------------------------------------------------------------
let db = null;
let conn = null;

async function initDuckDB() {
  setStatus("loading", "Initializing DuckDB WASM\u2026");

  // Pinned CDN dist. We keep both bundles; selectBundle picks `eh` when
  // cross-origin isolation (SharedArrayBuffer) is available, else falls back to
  // `mvp`. GitHub Pages does not set COOP/COEP, so `mvp` (single-threaded) is used.
  const CDN = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/dist";
  const bundle = await duckdb.selectBundle({
    mvp: {
      mainModule: `${CDN}/duckdb-mvp.wasm`,
      mainWorker: `${CDN}/duckdb-browser-mvp.worker.js`,
    },
    eh: {
      mainModule: `${CDN}/duckdb-eh.wasm`,
      mainWorker: `${CDN}/duckdb-browser-eh.worker.js`,
    },
  });

  // Browsers forbid `new Worker(crossOriginUrl)`. The worker source is CORS-
  // enabled on jsDelivr, so we fetch it, wrap it in a same-origin blob URL, and
  // spawn the worker from that blob. (The MVP worker is fully bundled - no
  // relative imports - so a blob URL is safe.)
  const workerSrc = await (await fetch(bundle.mainWorker)).text();
  const blob = new Blob([workerSrc], { type: "application/javascript" });
  const worker = new Worker(URL.createObjectURL(blob));

  const logger = new duckdb.ConsoleLogger();
  db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule);

  conn = await db.connect();

  // The published Parquet is split into N shards (crc32(id) % N) to stay under
  // GitHub Pages' per-file size limit. We register every shard, then build a
  // single view over the list - DuckDB reads them as one logical table and
  // issues Range requests across shards on demand.
  const PARQUET_SHARDS = 4;
  const shardNames = Array.from({ length: PARQUET_SHARDS }, (_, i) =>
    `models-${String(i).padStart(3, "0")}.parquet`);
  // The file list must be inlined in the SQL (DuckDB binds a single `?` here
  // as a glob string, not a list); filter values remain parameterized below.
  const listSql = "[" + shardNames.map((n) => `'${n}'`).join(",") + "]";
  const createView = () =>
    conn.query(`CREATE VIEW models AS SELECT * FROM read_parquet(${listSql})`);

  // Register each shard via HTTP so DuckDB issues Range requests instead of
  // downloading whole files. We MUST pass absolute URLs: the worker runs
  // inside a blob, so a relative URL would resolve against the blob URL.
  try {
    for (const name of shardNames) {
      const url = new URL(name, document.baseURI).href;
      await db.registerFileURL(name, url, duckdb.DuckDBDataProtocol.HTTP, false);
    }
    await createView();
    console.info(`[hf-indexer] Registered ${PARQUET_SHARDS} parquet shards via HTTP (Range reads).`);
  } catch (httpErr) {
    // Fallback: fetch each shard fully and register as a buffer. Works in
    // every bundle at the cost of one-time full downloads.
    console.warn("[hf-indexer] HTTP registration failed, falling back to buffer:", httpErr);
    for (const name of shardNames) {
      const url = new URL(name, document.baseURI).href;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`Failed to fetch ${name}: HTTP ${resp.status}`);
      const buffer = new Uint8Array(await resp.arrayBuffer());
      await db.registerFileBuffer(name, buffer);
    }
    await createView();
    console.info(`[hf-indexer] Registered ${PARQUET_SHARDS} parquet shards via buffer.`);
  }

  // Feature-detect the `has_weights` column. The published Parquet may predate
  // the pipeline change (or local dev may serve old shards); in that case the
  // hide-empty checkbox stays disabled and its predicate is omitted, so this
  // frontend works with any shard generation.
  try {
    const desc = await conn.query("DESCRIBE models");
    hasWeightsColumn = desc.toArray().some((r) => r.column_name === "has_weights");
  } catch (err) {
    console.warn("[hf-indexer] DESCRIBE models failed; assuming no has_weights column:", err);
    hasWeightsColumn = false;
  }
  if (!hasWeightsColumn && els.hideEmpty) {
    els.hideEmpty.disabled = true;
    els.hideEmpty.checked = false;
    if (els.hideEmptyHint) els.hideEmptyHint.classList.remove("hidden");
  }

  setStatus("ready", "Ready. Indexed and queryable.");
}

// ---------------------------------------------------------------------------
// Filter form -> parameterized SQL
// ---------------------------------------------------------------------------
// Set to true after init once the published Parquet is confirmed to carry the
// `has_weights` column. When false (stale published data / local dev with old
// shards), the hide-empty checkbox is disabled and its predicate omitted, so
// queries keep working regardless of deployment order.
let hasWeightsColumn = false;

/** Collect the form filters (everything except the blocklist) as SQL pieces. */
function collectFilters() {
  const params = [];
  const conditions = [];

  const idText = els.idInput.value.trim();
  if (idText) {
    conditions.push("id ILIKE ?");
    params.push(`%${idText}%`);
  }

  const minVal = els.minSize.value.trim();
  if (minVal !== "") {
    const min = parseFloat(minVal);
    if (!Number.isNaN(min)) {
      conditions.push("size_b >= ?");
      params.push(min);
    }
  }

  const maxVal = els.maxSize.value.trim();
  if (maxVal !== "") {
    const max = parseFloat(maxVal);
    if (!Number.isNaN(max)) {
      conditions.push("size_b <= ?");
      params.push(max);
    }
  }

  const formatText = els.format.value.trim();
  if (formatText) {
    conditions.push("format ILIKE ?");
    params.push(`%${formatText}%`);
  }

  const licenseText = els.license.value.trim();
  if (licenseText) {
    conditions.push("license ILIKE ?");
    params.push(`%${licenseText}%`);
  }

  // Minimum-downloads threshold (anti-spam lever): bound only when non-empty,
  // integer-coerced (the strict WASM prepared-statement binder dislikes loose
  // numeric types, cf. the TIMESTAMPTZ CAST below).
  const minDl = els.minDownloads.value.trim();
  if (minDl !== "") {
    const n = Number.parseInt(minDl, 10);
    if (!Number.isNaN(n) && n >= 0) {
      conditions.push("downloads >= ?");
      params.push(n);
    }
  }

  // Hide empty repos: only confirmed-empty models (has_weights = false) are
  // hidden. Null means "not yet re-swept by the pipeline" and is kept visible,
  // so unknown records are never silently dropped.
  if (hasWeightsColumn && els.hideEmpty.checked && !els.hideEmpty.disabled) {
    conditions.push("has_weights IS DISTINCT FROM FALSE");
  }

  // Date-range filters. `type=date` inputs yield YYYY-MM-DD; bound them to the
  // start (00:00:00Z) or end (23:59:59Z) of that UTC day so a single date is
  // inclusive of the whole day.
  const dateFrom = (el) => (el.value ? `${el.value}T00:00:00Z` : null);
  const dateTo = (el) => (el.value ? `${el.value}T23:59:59Z` : null);

  const createdFromVal = dateFrom(els.createdFrom);
  if (createdFromVal) {
    conditions.push("created_at >= CAST(? AS TIMESTAMPTZ)");
    params.push(createdFromVal);
  }
  const createdToVal = dateTo(els.createdTo);
  if (createdToVal) {
    conditions.push("created_at <= CAST(? AS TIMESTAMPTZ)");
    params.push(createdToVal);
  }
  const modifiedFromVal = dateFrom(els.modifiedFrom);
  if (modifiedFromVal) {
    conditions.push("modified_at >= CAST(? AS TIMESTAMPTZ)");
    params.push(modifiedFromVal);
  }
  const modifiedToVal = dateTo(els.modifiedTo);
  if (modifiedToVal) {
    conditions.push("modified_at <= CAST(? AS TIMESTAMPTZ)");
    params.push(modifiedToVal);
  }

  return { conditions, params };
}

/**
 * Build the results query.
 * @param {object} [opts]
 * @param {number} [opts.limit=500]   Render cap, or the larger export cap.
 * @param {boolean} [opts.withBlocklist=true] Apply the author blocklist.
 */
function buildQuery({ limit = 500, withBlocklist = true } = {}) {
  const { conditions, params } = collectFilters();

  if (withBlocklist) {
    // `starts_with` matches the author prefix literally - no LIKE wildcard
    // escaping to worry about.
    for (const author of blockedAuthors) {
      conditions.push("NOT starts_with(id, ?)");
      params.push(`${author}/`);
    }
  }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const sql = `
    SELECT id, size_b, format, license, downloads, likes, modified_at, created_at
    FROM models
    ${where}
    ORDER BY downloads DESC
    LIMIT ${limit}
  `;
  return { sql, params };
}

/** Count how many rows the ACTIVE filters match that are hidden by the
 *  blocklist (one extra aggregate query, run only when blocking is active). */
function buildHiddenCountQuery() {
  const { conditions, params } = collectFilters();
  const blocked = blockedAuthors.map(() => "starts_with(id, ?)");
  const all = [...conditions, `(${blocked.join(" OR ")})`];
  const sql = `
    SELECT count(*) AS hidden
    FROM models
    WHERE ${all.join(" AND ")}
  `;
  return { sql, params: [...params, ...blockedAuthors.map((a) => `${a}/`)] };
}

// ---------------------------------------------------------------------------
// Render results
// ---------------------------------------------------------------------------
function renderRows(rows) {
  els.resultsBody.innerHTML = "";

  if (!rows || rows.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "empty-row";
    const td = document.createElement("td");
    td.colSpan = 9;
    td.textContent = "No models matched the current filters.";
    tr.appendChild(td);
    els.resultsBody.appendChild(tr);
    return;
  }

  for (const r of rows) {
    const tr = document.createElement("tr");

    // ID (linked)
    const tdId = document.createElement("td");
    tdId.className = "cell-id";
    const a = document.createElement("a");
    a.href = r.url || `https://huggingface.co/${r.id}`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = r.id;
    tdId.appendChild(a);
    tr.appendChild(tdId);

    // Size (B)
    const tdSize = document.createElement("td");
    tdSize.className = "cell-num";
    tdSize.textContent = r.size_b == null ? "\u2014" : r.size_b.toFixed(1);
    tr.appendChild(tdSize);

    // Format
    const tdFormat = document.createElement("td");
    tdFormat.className = "cell-format";
    tdFormat.textContent = r.format || "\u2014";
    tr.appendChild(tdFormat);

    // License
    const tdLicense = document.createElement("td");
    tdLicense.className = "cell-license";
    tdLicense.textContent = r.license || "\u2014";
    tr.appendChild(tdLicense);

    // Downloads
    const tdDl = document.createElement("td");
    tdDl.className = "cell-num";
    tdDl.textContent = (r.downloads ?? 0).toLocaleString();
    tr.appendChild(tdDl);

    // Likes
    const tdLikes = document.createElement("td");
    tdLikes.className = "cell-num";
    tdLikes.textContent = (r.likes ?? 0).toLocaleString();
    tr.appendChild(tdLikes);

    // Created
    const tdCreated = document.createElement("td");
    tdCreated.className = "cell-date";
    tdCreated.textContent = formatDate(r.created_at);
    tr.appendChild(tdCreated);

    // Modified
    const tdMod = document.createElement("td");
    tdMod.className = "cell-date";
    tdMod.textContent = formatDate(r.modified_at);
    tr.appendChild(tdMod);

    // Actions: copy download command + block author. Models without an
    // author prefix in their id have nothing sensible to block.
    const tdActions = document.createElement("td");
    tdActions.className = "cell-actions";
    const author = r.id.includes("/") ? r.id.split("/", 1)[0] : null;

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "row-btn";
    copyBtn.title = "Copy download command";
    copyBtn.setAttribute("aria-label", `Copy download command for ${r.id}`);
    copyBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.addEventListener("click", () => copyDownloadCommand(r.id, copyBtn));
    tdActions.appendChild(copyBtn);

    if (author) {
      const blockBtn = document.createElement("button");
      blockBtn.type = "button";
      blockBtn.className = "row-btn";
      blockBtn.title = `Block ${author}`;
      blockBtn.setAttribute("aria-label", `Block ${author} (hide all their models)`);
      blockBtn.innerHTML =
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
        '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/></svg>';
      blockBtn.addEventListener("click", () => blockAuthor(author));
      tdActions.appendChild(blockBtn);
    }

    tr.appendChild(tdActions);

    els.resultsBody.appendChild(tr);
  }
}

function formatDate(value) {
  if (!value) return "\u2014";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "\u2014";
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Author blocklist (localStorage only - never leaves the browser)
// ---------------------------------------------------------------------------
const BLOCKLIST_KEY = "hf-indexer:blocklist";

let blockedAuthors = loadBlocklist();

function loadBlocklist() {
  try {
    const raw = localStorage.getItem(BLOCKLIST_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return [...new Set(parsed.filter((v) => typeof v === "string" && v))];
  } catch {
    return [];
  }
}

function saveBlocklist() {
  try {
    localStorage.setItem(BLOCKLIST_KEY, JSON.stringify(blockedAuthors));
  } catch {
    // Storage unavailable (e.g. private mode) - the in-memory list still works.
  }
}

function blockAuthor(author) {
  if (!author || blockedAuthors.includes(author)) return;
  blockedAuthors.push(author);
  saveBlocklist();
  updateBlocklistUi();
  runSearch();
}

function unblockAuthor(author) {
  blockedAuthors = blockedAuthors.filter((a) => a !== author);
  saveBlocklist();
  updateBlocklistUi();
}

function updateBlocklistUi() {
  const n = blockedAuthors.length;
  if (els.blocklistBtnLabel) {
    els.blocklistBtnLabel.textContent = n > 0 ? `Blocked (${n})` : "Blocked";
  }
}

// --- Management modal ------------------------------------------------------

function openBlocklistModal() {
  renderBlocklistModal();
  if (typeof els.blocklistModal.showModal === "function") {
    els.blocklistModal.showModal();
  }
}

function renderBlocklistModal() {
  els.blocklistEntries.innerHTML = "";
  const n = blockedAuthors.length;
  els.blocklistEmpty.classList.toggle("hidden", n > 0);
  els.blocklistClear.disabled = n === 0;
  for (const author of blockedAuthors) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = author;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Remove";
    btn.setAttribute("aria-label", `Unblock ${author}`);
    btn.addEventListener("click", () => {
      unblockAuthor(author);
      renderBlocklistModal();
      runSearch();
    });
    li.appendChild(name);
    li.appendChild(btn);
    els.blocklistEntries.appendChild(li);
  }
}

// --- Hidden-results feedback -----------------------------------------------

async function updateHiddenIndicator() {
  if (!conn || blockedAuthors.length === 0) {
    els.hiddenCount.classList.add("hidden");
    els.hiddenCount.textContent = "";
    return;
  }
  try {
    const { sql, params } = buildHiddenCountQuery();
    const stmt = await conn.prepare(sql);
    const result = await stmt.query(...params);
    const hidden = Number(result.toArray()[0]?.hidden ?? 0);
    if (hidden > 0) {
      els.hiddenCount.textContent =
        `${hidden.toLocaleString()} hidden by blocklist`;
      els.hiddenCount.title = "Some results match your filters but are hidden by your blocklist. Click to manage.";
      els.hiddenCount.classList.remove("hidden");
    } else {
      els.hiddenCount.classList.add("hidden");
      els.hiddenCount.textContent = "";
    }
  } catch {
    // The indicator is advisory; never surface its failures as errors.
    els.hiddenCount.classList.add("hidden");
    els.hiddenCount.textContent = "";
  }
}

// ---------------------------------------------------------------------------
// Copy download command (per-row action)
// ---------------------------------------------------------------------------
async function copyDownloadCommand(modelId, btn) {
  const command = `huggingface-cli download ${modelId}`;
  let copied = false;
  try {
    await navigator.clipboard.writeText(command);
    copied = true;
  } catch {
    // Clipboard API unavailable (insecure context / permission denied) -
    // fall back to the legacy selected-text copy.
    try {
      const ta = document.createElement("textarea");
      ta.value = command;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      ta.remove();
    } catch {
      copied = false;
    }
  }
  if (copied && btn) {
    const original = btn.getAttribute("aria-label");
    btn.classList.add("copied");
    btn.setAttribute("aria-label", "Copied!");
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.setAttribute("aria-label", original);
    }, 1200);
  }
}

// ---------------------------------------------------------------------------
// Export current query (CSV / JSON) - fully client-side
// ---------------------------------------------------------------------------
const EXPORT_ROW_CAP = 100000;

const EXPORT_COLUMNS = [
  "id", "size_b", "format", "license",
  "downloads", "likes", "modified_at", "created_at",
];

/** Normalize a DuckDB row value for serialization (Date -> ISO string,
 *  BigInt -> Number; int64 counters are far below Number.MAX_SAFE_INTEGER). */
function exportValue(v) {
  if (v instanceof Date) return v.toISOString();
  if (typeof v === "bigint") return Number(v);
  return v;
}

function csvEscape(value) {
  if (value == null) return "";
  const s = String(value);
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function toCsv(rows) {
  const lines = [EXPORT_COLUMNS.join(",")];
  for (const row of rows) {
    lines.push(EXPORT_COLUMNS.map((c) => csvEscape(exportValue(row[c]))).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

function toJson(rows) {
  const records = rows.map((row) => {
    const o = {};
    for (const c of EXPORT_COLUMNS) o[c] = exportValue(row[c]);
    return o;
  });
  return JSON.stringify(records, null, 2);
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function runExport(format) {
  if (!conn) {
    showError("DuckDB is not ready yet.");
    return;
  }
  clearError();
  els.exportNote.textContent = "";
  const buttons = [els.exportCsvBtn, els.exportJsonBtn];
  for (const b of buttons) b.disabled = true;

  try {
    // Re-run the ACTIVE filters (blocklist included) without the 500-row
    // render cap - the export reflects the query, not the rendered page.
    const { sql, params } = buildQuery({ limit: EXPORT_ROW_CAP });
    const stmt = await conn.prepare(sql);
    const result = await stmt.query(...params);
    const rows = result.toArray();

    const stamp = new Date().toISOString().replace(/[-:.]/g, "").slice(0, 15); // YYYYMMDDTHHMMSS
    if (format === "csv") {
      downloadBlob(new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }),
        `hf-models-${stamp}.csv`);
    } else {
      downloadBlob(new Blob([toJson(rows)], { type: "application/json;charset=utf-8" }),
        `hf-models-${stamp}.json`);
    }

    if (rows.length >= EXPORT_ROW_CAP) {
      els.exportNote.textContent =
        `Export capped at ${EXPORT_ROW_CAP.toLocaleString()} rows - narrow the filters for the full result set.`;
    }
  } catch (err) {
    showError(`Export failed: ${err && err.message ? err.message : String(err)}`);
  } finally {
    for (const b of buttons) b.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Search handler
// ---------------------------------------------------------------------------
async function runSearch(event) {
  if (event) event.preventDefault();
  if (!conn) {
    showError("DuckDB is not ready yet.");
    return;
  }
  clearError();

  els.searchBtn.disabled = true;
  const originalLabel = els.searchBtn.textContent;
  els.searchBtn.textContent = "Searching\u2026";
  els.rowCount.textContent = "Running query\u2026";

  try {
    const { sql, params } = buildQuery();
    const stmt = await conn.prepare(sql);
    const result = await stmt.query(...params);
    const rows = result.toArray().map((row) => ({
      id: row.id,
      size_b: row.size_b,
      format: row.format,
      license: row.license,
      downloads: row.downloads,
      likes: row.likes,
      created_at: row.created_at,
      modified_at: row.modified_at,
      // `url` is not stored in the Parquet (trimmed for size); reconstruct it.
      url: `https://huggingface.co/${row.id}`,
    }));
    renderRows(rows);
    els.rowCount.textContent = `${rows.length} model${rows.length === 1 ? "" : "s"} matched${rows.length >= 500 ? " (capped at 500)" : ""}.`;
    await updateHiddenIndicator();
  } catch (err) {
    showError(`Query failed: ${err && err.message ? err.message : String(err)}`);
    els.rowCount.textContent = "";
  } finally {
    els.searchBtn.disabled = false;
    els.searchBtn.textContent = originalLabel;
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
initTheme();
updateBlocklistUi();
els.form.addEventListener("submit", runSearch);

els.blocklistBtn.addEventListener("click", openBlocklistModal);
els.hiddenCount.addEventListener("click", openBlocklistModal);
els.blocklistClose.addEventListener("click", () => els.blocklistModal.close());
els.blocklistClear.addEventListener("click", () => {
  blockedAuthors = [];
  saveBlocklist();
  updateBlocklistUi();
  renderBlocklistModal();
  runSearch();
});
els.exportCsvBtn.addEventListener("click", () => runExport("csv"));
els.exportJsonBtn.addEventListener("click", () => runExport("json"));

initDuckDB().catch((err) => {
  setStatus("error", "DuckDB failed to initialize.");
  showError(
    `Failed to initialize DuckDB WASM: ${err && err.message ? err.message : String(err)}`
  );
  console.error(err);
});
