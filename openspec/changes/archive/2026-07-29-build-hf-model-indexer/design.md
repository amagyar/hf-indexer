## Context

This is a greenfield project to build a **serverless, static Hugging Face Model Indexer**. There is no existing codebase - everything is created in this change. The system runs entirely on free infrastructure (GitHub Actions for compute, GitHub Pages for hosting, DuckDB WASM in the browser for query execution). No backend server, database, or paid services are required.

Key constraints from the proposal:
- Code lives on `main`; data (`models.jsonl.gz`, `models.parquet`) lives only on `gh-pages`.
- Hourly CI/CD cycle: fetch current state via HTTP → update → rebuild Parquet → force-push to `gh-pages`.
- GitHub Pages has a soft 100MB file size limit per file, so the JSONL state is gzip-compressed.
- Hugging Face Hub API has rate limits (HTTP 429) that must be handled gracefully.

Stakeholders: developers searching HF models; the GitHub Actions runner that executes the hourly job.

## Goals / Non-Goals

**Goals:**
- Fetch and incrementally update HF model metadata without rescanning the entire Hub each run.
- Parse quantization format (`gguf`, `awq`, `gptq`, `exl2`) and parameter size (e.g. `7b`, `8x7b` → `56.0`) from tags and model ID.
- Persist state as gzip-compressed JSONL to stay under GitHub's file size limit.
- Produce a typed, zstd-compressed Parquet file optimized for DuckDB WASM HTTP Range reads.
- Run automatically every hour via GitHub Actions with zero manual intervention.
- Provide a dark-mode static frontend that queries the Parquet client-side via DuckDB WASM using HTTP Range requests (no full download).

**Non-Goals:**
- Authentication, user accounts, or per-user state.
- Server-side query execution (everything runs in the browser via WASM).
- Indexing datasets, spaces, or non-model repos (models only for v1).
- Real-time push updates (hourly batch is sufficient).
- Full-text search engine beyond SQL `LIKE`/`ILIKE` filters.
- Storing model weights, snapshots, or file contents - only metadata.

## Decisions

### Decision 1: Two-branch Git model (`main` + `gh-pages`) instead of releases/artifacts
**Choice:** Store code on `main`, force-push static assets to `gh-pages` each run from a throwaway temp git repo.
**Rationale:** Keeps `main` free of data bloat (no LFS, no growing diffs), avoids GitHub Actions artifacts expiry, and makes the latest state always available at a stable Pages URL for the next run's HTTP fetch.
**Alternatives considered:**
- Commit data to `main` → rejected: pollutes history, requires LFS, slow clones.
- GitHub Actions artifacts (`actions/upload-artifact`) → rejected: expire (90 days default), not directly served by Pages, harder for next run to fetch.

### Decision 2: JSONL (gzip) as source of truth, Parquet as derived artifact
**Choice:** `models.jsonl.gz` is the canonical mutable state; `models.parquet` is rebuilt each run from it.
**Rationale:** JSONL is human-readable, append/edit-friendly in Python (dict keyed by `id`), and trivially compressed. Parquet is the read-optimized format consumed by DuckDB WASM.
**Alternatives considered:**
- SQLite as state → rejected: binary merge conflicts, harder to inspect, overkill for key-by-id updates.
- Only Parquet (no JSONL) → rejected: Parquet is column-oriented and awkward for incremental row updates.

### Decision 3: DuckDB WASM with HTTP Range protocol (no full download)
**Choice:** Use `@duckdb/duckdb-wasm`, register the Parquet via `DuckDBDataProtocol.HTTP`, and let DuckDB issue Range requests to fetch only needed byte ranges.
**Rationale:** A full HF model catalog Parquet can be tens of MB; Range reads keep page loads fast and bandwidth low for typical filter queries.
**Alternatives considered:**
- Download whole Parquet then query → rejected: slow first load, high bandwidth.
- Server-side API → rejected: violates serverless/static goal.

### Decision 4: Two-pass fetch — incremental watermark + iterative cursor backfill
**Choice:** Each run executes two passes against the HF Hub API (sorted by `lastModified` desc):
1. **Incremental pass** — iterate from newest (no cursor) and stop at the `modified_at` watermark (newest model already in state). Catches new/updated models since the last run. Naturally bounded.
2. **Backfill pass** — resume from a **persisted pagination cursor** stored in `backfill_state.json`, fetch up to `--limit` more (older) models, then persist the new cursor. When the API returns no `next` page, backfill is marked complete and only the incremental pass runs thereafter.

The HF API paginates via a `cursor` query parameter (exposed in the `Link: rel="next"` header) that encodes `lastModified < <timestamp>`. Persisting it lets each backfill batch resume exactly where the last stopped — no re-iteration, no quadratic cost. At `--limit 50000` per hourly run, ~1M models backfill in ~20 runs.

**Rationale:** A single full rescan would be too slow/rate-limit-prone for hourly cadence; pure watermark updates would never reach historical models. The two-pass design catches new models immediately *and* progressively completes historical coverage.

**Alternatives considered:**
- Full rescan every run → rejected: too slow for hourly cadence.
- Capped batches without cursor resume → rejected: re-walks already-fetched models each run (quadratic blowup).
- HF date-range filters for resume → rejected: empirically the `filter` param returns 0 for `lastModified` ranges; only the opaque cursor works.

### Decision 4b: Raw `requests` instead of `huggingface_hub`
**Choice:** Hit `https://huggingface.co/api/models` directly with `requests`, managing the pagination cursor ourselves.
**Rationale:** Two implementation findings forced this: (1) `full=false` already returns every schema field (`downloads`, `likes`, `tags`, `createdAt`, `lastModified`) — `full=true` adds nothing, so the library's backfill mode is moot; (2) `huggingface_hub`'s iterator hides the `cursor`, which we must persist to resume iterative backfill. Raw HTTP also drops a heavy dependency.
**Alternatives considered:**
- Keep `huggingface_hub` for incremental, raw HTTP for backfill → rejected: two code paths for the same API; unnecessary complexity.

### Decision 4c: Continuous metrics sweep (popularity counters drift independently of `lastModified`)
**Choice:** After the backfill cursor exhausts, do NOT stop. Reset the cursor to newest and keep cycling the same pass as a **metrics sweep**: each run advances through another `--limit` chunk of already-known models, refreshing their `downloads`/`likes`. Every refreshed record is stamped with `metrics_refreshed_at` (capture time); untouched records keep their prior stamp.
**Rationale:** The incremental pass is keyed on `lastModified`, but `downloads`/`likes` change constantly *without* bumping `lastModified` (they are not file changes). Without the sweep, a stable-but-popular model (e.g. `all-MiniLM-L6-v2`, unchanged for months) would show a frozen download count forever. At `--limit 50000`/hour over ~1M models, the sweep refreshes each record's counters roughly once per day.
**Alternatives considered:**
- Stop after backfill complete (incremental-only) → rejected: popularity numbers go stale for any model that doesn't get file edits.
- Separate weekly full re-scan → rejected: spikey load; the continuous sweep spreads the work evenly across hourly runs and reuses the existing cursor pipeline.
- Recompute counters from event data → rejected: HF exposes no such event stream; the list API is the only source.



### Decision 5: Size parsing via tags-first, regex-fallback
**Choice:** First check tags for `size:<n>b` patterns; if absent, regex the model ID for `-<n>b` and `NxNb` (e.g. `8x7b` → 8×7 = 56.0). Null if unknown.
**Rationale:** Tags are authoritative when present; model-ID regex covers the common naming convention for models without explicit size tags.
**Trade-off:** Regex may mis-classify edge cases; null is the safe fallback rather than guessing.

### Decision 6: Handle HF rate limits with bounded retry/backoff
**Choice:** On HTTP 429 (or transient `requests` errors) from the HF API, sleep with exponential backoff (capped attempts) before retrying; fail loudly in CI if the cap is exhausted.
**Rationale:** Keeps hourly runs resilient without hiding persistent failures.

## Risks / Trade-offs

- **[Risk] GitHub Pages 100MB file limit** → Mitigation: gzip JSONL; Parquet zstd is already highly compressed; monitor file size in CI and fail fast if exceeded.
- **[Risk] HF API rate limiting (429) stalling hourly runs** → Mitigation: bounded exponential backoff; backfill batch size capped (`--limit`, default 50k); CI step fails loudly rather than silently producing partial data. Optional `HF_TOKEN` secret raises rate limits.
- **[Risk] Force-push to `gh-pages` discards history** → Trade-off accepted: Pages data is reproducible from `main` + HF API; history is not valuable and force-push keeps the branch small.
- **[Risk] DuckDB WASM CDN outage breaks frontend** → Mitigation: pin a specific JsDelivr version; document self-hosting as a fallback.
- **[Risk] Size regex mis-parses unusual model IDs** → Mitigation: null on uncertainty; parsing is best-effort, not authoritative.
- **[Risk] First run has no existing state (404 on fetch)** → Mitigation: `fetch_updates.py` treats 404 as empty state and bootstraps from scratch (backfill-from-newest with cursor set for next run).
- **[Risk] Backfill cursor lost/corrupted** → Mitigation: `backfill_state.json` is rewritten atomically each run; if lost, backfill restarts from newest (worst case: re-fetches already-known models, deduped by `id` in state).
- **[Trade-off] Hourly cadence means up to 1hr staleness** → Accepted; near-real-time is a non-goal.
- **[Trade-off] Backfill takes ~20 hourly runs to complete (~1 day)** → Accepted; incremental pass serves fresh models immediately while history fills in progressively. After completion, the same pass continues as a metrics sweep, so no behavior change is needed at cutover.
- **[Trade-off] Popularity counters up to ~1 day stale** → Accepted; the metrics sweep cycles the full catalog continuously (`--limit`/hour). `metrics_refreshed_at` makes the staleness of any given record visible to the UI.
