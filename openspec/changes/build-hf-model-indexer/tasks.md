## 1. Repository scaffolding

- [x] 1.1 Create `scripts/`, `frontend/`, and `.github/workflows/` directories
- [x] 1.2 Create `scripts/requirements.txt` pinning `requests`, `huggingface_hub`, `pandas`, `pyarrow`
- [x] 1.3 Add `.gitignore` to ensure `models.jsonl.gz` and `models.parquet` never land on `main`

## 2. Model state management (`scripts/fetch_updates.py`)

- [x] 2.1 Define the JSONL record schema (`id`, `author`, `url`, `size_b`, `quant`, `tags`, `downloads`, `likes`, `created_at`, `modified_at`) as a typed structure
- [x] 2.2 Implement state retrieval: HTTP GET of `<pages_url>/models.jsonl.gz?t=<timestamp>`; treat 404 as empty state; otherwise decompress and load into a dict keyed by `id`
- [x] 2.3 Implement the `modified_at` watermark: compute max `modified_at` from existing state and use `HfApi.list_models(sort="lastModified", direction=-1)`, stopping at the watermark
- [x] 2.4 Add a `--backfill` CLI flag that forces `full=True` rescan ignoring the watermark
- [x] 2.5 Implement quantization parsing: scan tags for `gguf`, `awq`, `gptq`, `exl2`; default to `"unknown"`
- [x] 2.6 Implement size parsing: prefer `size:<n>b` tags; fall back to model-ID regex for `<n>b` and `NxNb` (multiply for MoE); null if unknown
- [x] 2.7 Implement bounded exponential backoff retry on HF API HTTP 429; non-zero exit when retry cap exhausted
- [x] 2.8 Merge fetched models into state dict and write back to `models.jsonl.gz` (gzip, atomic write)
- [x] 2.9 Verify locally: run once (bootstrap), then again (incremental) and confirm no rescans past watermark

## 3. Parquet builder (`scripts/build_parquet.py`)

- [x] 3.1 Read `models.jsonl.gz` via pandas (native gzip support)
- [x] 3.2 Enforce column types: `size_b`→`float32`, `downloads`/`likes`→int, `created_at`/`modified_at`→timestamp
- [x] 3.3 Export `models.parquet` using pyarrow engine with `zstd` compression
- [x] 3.4 Verify the Parquet opens in DuckDB CLI and column types match expectations

## 4. Frontend (`frontend/`)

- [x] 4.1 Create `frontend/index.html` with dark-mode layout: text search, min/max size, quantization dropdown, Search button, results table
- [x] 4.2 Create `frontend/styles.css` for dark-mode styling and table layout
- [x] 4.3 Create `frontend/app.js` with DuckDB WASM bootstrap from a pinned JsDelivr CDN version (worker instantiation)
- [x] 4.4 Register `models.parquet` via `DuckDBDataProtocol.HTTP` and create view `models` from `read_parquet('models.parquet')`
- [x] 4.5 Surface an error UI state if DuckDB fails to initialize
- [x] 4.6 Build parameterized SQL from active filters (omit empty filters; e.g. `size_b >= ?`, `quant = ?`, ILIKE for text)
- [x] 4.7 Render loading state while a query is in flight (disable Search button)
- [x] 4.8 Render result rows into the table mapped to schema fields
- [x] 4.9 Local smoke test: serve `frontend/` + a sample `models.parquet` and confirm a filter query returns rows via Range requests

## 5. Deployment pipeline (`.github/workflows/update_index.yml`)

- [x] 5.1 Define triggers: `schedule: cron: '0 * * * *'` and `workflow_dispatch`
- [x] 5.2 Grant `contents: write` permission to the workflow's `GITHUB_TOKEN`
- [x] 5.3 Add ordered job steps: checkout `main`, setup Python 3.10, `pip install -r scripts/requirements.txt`, run `fetch_updates.py`, run `build_parquet.py`
- [x] 5.4 Implement deploy step: assemble `frontend/*`, `models.parquet`, `models.jsonl.gz` in a temp directory
- [x] 5.5 Initialize a fresh git repo in the temp dir, commit, and force-push to `gh-pages` using `https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}.git`
- [x] 5.6 Add a CI guard that fails the job if either data file exceeds GitHub's per-file size limit

## 6. Verification

- [ ] 6.1 Trigger `workflow_dispatch` on a branch and confirm `gh-pages` receives `index.html`, `app.js`, `styles.css`, `models.jsonl.gz`, `models.parquet`
- [x] 6.2 Confirm `main` remains free of `models.jsonl.gz` and `models.parquet`
- [ ] 6.3 Open the deployed GitHub Pages URL and verify DuckDB WASM initializes, the view is created, and a filter query returns rows
- [ ] 6.4 Verify a second hourly run performs an incremental update (stops at watermark) rather than a full rescan
