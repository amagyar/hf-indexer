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
  form: document.getElementById("filter-form"),
  searchBtn: document.getElementById("search-btn"),
  idInput: document.getElementById("f-id"),
  minSize: document.getElementById("f-min-size"),
  maxSize: document.getElementById("f-max-size"),
  quant: document.getElementById("f-quant"),
  rowCount: document.getElementById("row-count"),
  resultsBody: document.getElementById("results-body"),
};

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

  // Register the Parquet via HTTP so DuckDB issues Range requests instead of
  // downloading the whole file. We MUST pass an absolute URL: the worker runs
  // inside a blob, so a relative URL would resolve against the blob URL (broken).
  const parquetUrl = new URL("models.parquet", document.baseURI).href;
  try {
    await db.registerFileURL(
      "models.parquet",
      parquetUrl,
      duckdb.DuckDBDataProtocol.HTTP,
      false
    );
    await conn.query(
      "CREATE VIEW models AS SELECT * FROM read_parquet('models.parquet')"
    );
    console.info("[hf-indexer] Registered models.parquet via HTTP (Range reads).");
  } catch (httpErr) {
    // Fallback: fetch the whole file and register it as a buffer. Works in
    // every bundle at the cost of a one-time full download.
    console.warn("[hf-indexer] HTTP registration failed, falling back to buffer:", httpErr);
    const resp = await fetch(parquetUrl);
    if (!resp.ok) throw new Error(`Failed to fetch models.parquet: HTTP ${resp.status}`);
    const buffer = new Uint8Array(await resp.arrayBuffer());
    await db.registerFileBuffer("models.parquet", buffer);
    await conn.query(
      "CREATE VIEW models AS SELECT * FROM read_parquet('models.parquet')"
    );
    console.info(`[hf-indexer] Registered models.parquet via buffer (${buffer.length} bytes).`);
  }

  setStatus("ready", "Ready. Indexed and queryable.");
}

// ---------------------------------------------------------------------------
// Filter form -> parameterized SQL
// ---------------------------------------------------------------------------
function buildQuery() {
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

  const quantVal = els.quant.value;
  if (quantVal) {
    conditions.push("quant = ?");
    params.push(quantVal);
  }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const sql = `
    SELECT id, size_b, quant, downloads, likes, modified_at, url
    FROM models
    ${where}
    ORDER BY downloads DESC
    LIMIT 500
  `;
  return { sql, params };
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
    td.colSpan = 6;
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

    // Quant
    const tdQuant = document.createElement("td");
    tdQuant.className = "cell-quant";
    tdQuant.textContent = r.quant || "\u2014";
    tr.appendChild(tdQuant);

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

    // Modified
    const tdMod = document.createElement("td");
    tdMod.className = "cell-date";
    tdMod.textContent = formatDate(r.modified_at);
    tr.appendChild(tdMod);

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
      quant: row.quant,
      downloads: row.downloads,
      likes: row.likes,
      modified_at: row.modified_at,
      url: row.url,
    }));
    renderRows(rows);
    els.rowCount.textContent = `${rows.length} model${rows.length === 1 ? "" : "s"} matched${rows.length >= 500 ? " (capped at 500)" : ""}.`;
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
els.form.addEventListener("submit", runSearch);

initDuckDB().catch((err) => {
  setStatus("error", "DuckDB failed to initialize.");
  showError(
    `Failed to initialize DuckDB WASM: ${err && err.message ? err.message : String(err)}`
  );
  console.error(err);
});
