# Hugging Face Model Indexer — Search & Filter Every HF Model In-Browser

> A fast, fully client-side search engine for the Hugging Face Hub. Browse and
> filter **~3 million models** by parameter size, format, license, downloads,
> and date — right in your browser. No backend, no tracking, instant queries.

🔗 **Live site: <https://amagyar.github.io/hf-indexer/>**

## What is it?

**hf-indexer** is a free tool for **searching and filtering every public model
on the Hugging Face Hub** — from transformers and safetensors checkpoints to
GGUF, AWQ, GPTQ, MLX, and ONNX weights. It indexes the entire Hub catalog
(currently around 3 million models) and lets you slice it down with SQL-grade
filters that run entirely in your browser.

The Hub's own search is great, but it doesn't easily answer questions like
*"show me all 7B–13B GGUF models with an Apache license, created this year,
sorted by downloads."* This tool does — and because it loads the catalog as
Parquet and queries it with **DuckDB compiled to WebAssembly**, every filter
runs client-side over HTTP Range reads: fast, and your searches never leave
your machine.

## Use cases

- **Find models by parameter size** — e.g. every model between 7B and 13B
  parameters, or all "small" models under 3B for local inference.
- **Filter by quantization / weight format** — GGUF, AWQ, GPTQ, EXL2,
  compressed-tensors, bitsandbytes, MLX, BitNet, ONNX.
- **Search by license** — Apache-2.0, MIT, Llama, CC-BY, and more.
- **Discover the most popular models** — results sort by downloads and likes.
- **Browse by recency** — filter on created or modified date to find newly
  released or recently updated models.
- **Compare the open-source LLM landscape** — what's out there, in what format,
  at what size, under what license.

## Features

- 🔍 **Model ID search** — substring match across `org/model-name`.
- 📏 **Parameter size range** (in billions).
- 🏷️ **Format dropdown** — `gguf`, `awq`, `gptq`, `exl2`, `compressed-tensors`,
  `bitsandbytes`, `mlx`, `bitnet`, `onnx`, or `unknown`.
- 📜 **License free-text filter** — matches SPDX ids (apache, mit, llama…).
- 📅 **Date-range filters** — `created_at` and `modified_at`.
- ⭐ **Sorted by downloads** — most popular models first (top 500 per query).
- ⚡ **Private & static** — no server, no database, no tracking. The whole thing
  is served from GitHub Pages.
- 🔄 **Hourly refresh** — the catalog is re-fetched from the Hub every hour via
  GitHub Actions.

## How it works

```
Hourly GitHub Actions job
   │  fetch_updates.py  →  huggingface.co/api/models  (paginated, 2 reqs/page)
   │  build_parquet.py  →  sharded, zstd-compressed Parquet
   ▼
GitHub Pages (static)
   │  models-00N.parquet   (4 query shards)
   │  models-NNN.jsonl.gz  (8 state shards)
   ▼
Browser: DuckDB-WASM registers the parquet shards over HTTP,
         runs your filters as SQL — only the matching column
         chunks are range-fetched.
```

The fetcher walks the Hub with a two-pass strategy (incremental by
`lastModified` watermark + a continuous metrics sweep that refreshes
`downloads`/`likes`). State is **sharded** (`crc32(id) % N`) so every published
file stays under GitHub Pages' 100 MB per-file limit as the catalog grows.
Full design — including the API quirk that forces a two-request merge per page —
is documented in [ARCHITECTURE.md](ARCHITECTURE.md).

## Tech stack

- **Python** — `requests`, `pandas`, `pyarrow` for fetching and building.
- **DuckDB-WASM** — the in-browser SQL engine that queries the Parquet.
- **Vanilla HTML / CSS / JS** — no framework, no build step.
- **GitHub Actions + Pages** — hourly CI and free static hosting.

## Run it locally

The frontend is static; the data pipeline needs Python.

```bash
# 1. Fetch model metadata from the Hub (writes sharded models-NNN.jsonl.gz).
pip install -r scripts/requirements.txt
python scripts/fetch_updates.py \
  --pages-base https://amagyar.github.io/hf-indexer \
  --output models.jsonl.gz

# 2. Build the sharded Parquet (writes models-00N.parquet).
python scripts/build_parquet.py --input models.jsonl.gz --output models.parquet

# 3. Serve the frontend + generated data and open it.
python3 -m http.server --directory frontend 8000
# then visit http://localhost:8000  (parquet shards must be alongside)
```

To run the end-to-end browser tests:

```bash
npm install
SITE_URL=http://localhost:8000 npm test
```

## Project layout

```
scripts/        Python: fetch_updates.py (Hub fetcher) + build_parquet.py
frontend/       Static DuckDB-WASM app (index.html, app.js, styles.css)
.github/        Hourly Actions workflow (fetch → build → deploy to gh-pages)
tests/e2e/      Playwright suite against the live frontend
openspec/       Specifications (model-state-management, parquet-build, …)
ARCHITECTURE.md Full system design and key decisions
```

## Status & caveats

- The catalog approaches full public coverage (~3M models); the metrics sweep
  refreshes each model's counters roughly once per day.
- `format` reflects the weight/runtime format (gguf, awq, …), not the
  quantization *level* (e.g. `Q4_K_M`). Per-file quant levels aren't exposed by
  the Hub list API and would require a per-model fetch — not yet implemented.
- Built and maintained in the open. Issues and ideas welcome.
