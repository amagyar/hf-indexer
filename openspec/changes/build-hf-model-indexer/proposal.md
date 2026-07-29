## Why

There is no easy way to search and filter Hugging Face models by size, quantization, and metadata at scale in a single place. Building a **serverless, static** indexer that runs for free on GitHub Pages (using DuckDB WASM for client-side SQL) makes this dataset instantly queryable in the browser without backend infrastructure, database costs, or API servers.

## What Changes

- Add Python scripts that fetch HF model metadata via `huggingface_hub`, parse quantization and parameter size, and persist state as a compressed `models.jsonl.gz`.
- Add a Parquet builder that converts the JSONL state into a typed `models.parquet` (zstd-compressed) optimized for DuckDB WASM Range queries.
- Add a GitHub Actions workflow that runs hourly, downloads current state via HTTP, updates it, rebuilds the Parquet, and force-pushes static assets to the `gh-pages` branch.
- Add a static dark-mode frontend that initializes DuckDB WASM, registers the Parquet over HTTP Range, and offers text/size/quantization filtering with a results table.
- Establish repository conventions: code lives on `main`, data (`models.jsonl.gz`, `models.parquet`) lives only on `gh-pages`; zero data on `main`.

## Capabilities

### New Capabilities
- `model-state-management`: Fetching, parsing (quant + size), and persisting HF model metadata as `models.jsonl.gz`; incremental updates keyed by most recent `modified_at`.
- `parquet-build`: Converting JSONL state into a typed, zstd-compressed Parquet file for DuckDB WASM consumption.
- `deployment-pipeline`: Hourly GitHub Actions CI/CD that updates state and force-deploys static assets to `gh-pages`.
- `frontend-query-ui`: DuckDB WASM-based static frontend that filters models client-side via HTTP Range requests on the Parquet file.

### Modified Capabilities
<!-- None - greenfield project with no existing specs. -->

## Impact

- **Code**: New `scripts/` directory (`fetch_updates.py`, `build_parquet.py`, `requirements.txt`), new `frontend/` directory (`index.html`, `app.js`, `styles.css`), new `.github/workflows/update_index.yml`.
- **Dependencies (Python)**: `requests`, `huggingface_hub`, `pandas`, `pyarrow`.
- **Dependencies (Frontend)**: `@duckdb/duckdb-wasm` via CDN (JsDelivr); no build step required.
- **Systems**: GitHub Actions runner (hourly cron), GitHub Pages (`gh-pages` branch), Hugging Face Hub API (must handle HTTP 429 rate limits).
- **Git**: Two-branch model - `main` (code only) and `gh-pages` (static site + data, force-pushed each run).
- **No breaking changes** - initial implementation.
