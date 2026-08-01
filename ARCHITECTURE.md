# Architecture

A static, fully client-side index of Hugging Face models. An hourly GitHub
Actions job fetches model metadata from the Hub, stores it as sharded
gzip-JSONL, builds sharded Parquet, and deploys everything to GitHub Pages. The
browser loads DuckDB-WASM and queries the Parquet directly over HTTP Range
reads — there is no backend.

```
                       GitHub Actions (hourly cron + workflow_dispatch)
                       ┌──────────────────────────────────────────────┐
                       │  fetch_updates.py                            │
  huggingface.co  ───► │   incremental pass (newest → watermark)      │ ──► models-000..007.jsonl.gz
  /api/models          │   backfill / metrics-sweep pass (cursor)     │     (8 shards, crc32(id) % 8)
                       │   2 requests/page, merged by id              │ ──► backfill_state.json
                       │                       │                      │
                       │                       ▼                      │
                       │  build_parquet.py                            │
                       │   read shards → trim to frontend cols        │ ──► models-000..003.parquet
                       │   bucket by crc32(id) % 4                    │     (4 shards, zstd)
                       └──────────────────────────────────────────────┘
                                          │ force-push (fresh orphan repo)
                                          ▼
                                gh-pages branch
                       ┌──────────────────────────────────────────────┐
                       │  frontend/{index.html,app.js,styles.css}     │
                       │  models-00N.parquet    (4 query shards)      │
                       │  models-NNN.jsonl.gz   (8 state shards)      │
                       │  backfill_state.json                         │
                       └──────────────────────────────────────────────┘
                                          │ next run reads state back over HTTPS
                                          ▼
                          browser: DuckDB-WASM reads parquet shards
                          via HTTP Range; all SQL runs client-side
```

## Components

### 1. Fetcher — `scripts/fetch_updates.py`

Fetches model metadata and maintains the JSONL state. Runs **two passes** each
invocation (`fetch_updates.py:526` `main`):

- **Incremental pass** — iterates from newest (no cursor), sorted by
  `lastModified` desc, stopping at the max `modified_at` already in state (the
  watermark). Catches new/updated models only.
- **Backfill / metrics-sweep pass** — resumes from a persisted pagination
  `cursor` (`backfill_state.json`), fetching up to `--limit` (default
  `50_000`) older models per run. When the API returns no next cursor,
  backfill is marked `complete` and the cursor resets to newest; the pass then
  cycles indefinitely as a **metrics sweep**, refreshing `downloads`/`likes`
  (which drift continuously, independent of `lastModified`). At 50k/hour over
  ~3M models, every record refreshes roughly once per day.

**The two-request merge (`fetch_updates.py:462` `fetch_hf_page`).** The HF list
endpoint has a quirk: specifying *any* `expand` makes it return only
`{_id, id, <sort field>}` plus the expansions, **dropping** `downloads`,
`likes`, `createdAt`, `author`, and even `tags`. Since we need both the base
fields and the `safetensors`/`gguf`/`cardData` expansions (for authoritative
`size_b` and structured `license`), each page issues **two** requests with
identical sort + cursor and merges by id: base fields from the no-expand call,
expansions from the expand call. (`tags` is part of the default response and is
not expanded.) Cost: 2× request volume — the only way to obtain both field sets
from this endpoint. 429s are retried with exponential backoff
(`MAX_RETRIES = 5`).

**Parsing** (`fetch_updates.py`):
- `parse_format(tags)` — first match in `KNOWN_FORMATS` (gguf, awq, gptq, exl2,
  compressed-tensors, bitsandbytes, mlx, bitnet, onnx), else `"unknown"`.
- `parse_license(card_data, tags)` — `cardData.license` (or list, or
  `license_name` when `"other"`), falling back to a `license:<id>` tag.
- `parse_size(tags, model_id, param_total)` — `param_total` (from
  `safetensors.total` / `gguf.total`) ÷ 1e9, then `size:<n>b` tag, then id
  regex (`7b`, MoE `8x7b`), else null.

### 2. Parquet builder — `scripts/build_parquet.py`

Reads the JSONL shards and writes the published Parquet shards:
- `discover_input_shards` — globs `models-*.jsonl.gz`, falling back to the
  legacy single `models.jsonl.gz`.
- `normalize_columns` — coerces to the canonical column set; drops the legacy
  `quant` column from the rename transition and backfills any missing canonical
  column.
- Trims to `PARQUET_COLUMNS` (the 8 frontend-facing columns).
- Buckets rows by `crc32(id) % PARQUET_SHARD_COUNT` and writes
  `models-000.parquet .. models-003.parquet` (zstd).

### 3. Frontend — `frontend/`

Static HTML/JS, no framework. `app.js`:
- Initializes DuckDB-WASM from a pinned CDN, spawning the worker from a
  same-origin blob (CDN workers can't be loaded cross-origin directly).
- Registers each `models-00N.parquet` shard via `registerFileURL` (HTTP Range
  reads), with a buffer-download fallback.
- Builds a single view over the shard list:
  `CREATE VIEW models AS SELECT * FROM read_parquet(['models-000.parquet', ...])`
  — the file list is inlined in the SQL (DuckDB binds a single `?` here as a
  glob string, not a list); filter values remain parameterized.
- Builds parameterized SQL from the filter form (id substring, size range,
  format, license substring, created/modified date ranges) and renders results.
  `url` is reconstructed from `id` client-side.

### 4. CI — `.github/workflows/update_index.yml`

Hourly `cron: '0 * * * *'` plus `workflow_dispatch`. Checks out `main`, installs
deps, runs fetch → build → size guard → deploy. The size guard fails the job if
any `models-*.jsonl.gz` or `models-*.parquet` exceeds 100 MB. Deploy assembles
the site in a throwaway temp dir and force-pushes a fresh orphan repo to
`gh-pages` (so `main` stays clean of generated data).

## State & storage model

Everything is published as static files on `gh-pages` and read back over HTTPS
on the next run.

| Artifact | Shards | Sharded by | Purpose |
|---|---|---|---|
| `models-NNN.jsonl.gz` | 8 | `crc32(id) % 8` | Source-of-truth state (full record schema) |
| `models-NNN.parquet` | 4 | `crc32(id) % 4` | Browser query target (frontend columns only) |
| `backfill_state.json` | 1 | — | `{cursor, complete}` for the sweep |

**Why two shard counts.** The JSONL is server-side only (no per-query cost), so
8 shards give large growth headroom. The Parquet is queried by the browser —
every search fans out a Range request per shard per column — so it uses fewer
shards (4) to bound latency. Both use `crc32(id) % N` for deterministic,
stateless routing: no index/manifest, the hash *is* the routing table.

**Per-file limit.** GitHub Pages refuses to serve files > 100 MB. Sharding
keeps each file well under that limit as the catalog grows (~3M models):
JSONL ~15 MB/shard, Parquet ~25 MB/shard at full coverage.

**Migration.** Both readers fall back to the legacy single-file names
(`models.jsonl.gz`, `models.parquet`) if no shards exist, so the first run after
the sharding change self-migrates: read legacy → write shards → next run reads
shards.

## Record schema

JSONL records (`RECORD_KEYS`) carry the full set; Parquet emits only the
frontend subset.

| Field | In JSONL | In Parquet | Source / notes |
|---|---|---|---|
| `id` | yes | yes | `org/model-name` |
| `author` | yes | — | derivable from `id` |
| `url` | yes | — | `https://huggingface.co/{id}`; reconstructed client-side |
| `size_b` | yes | yes | param count ÷ 1e9 (float32) |
| `format` | yes | yes | gguf/awq/gptq/.../unknown |
| `license` | yes | yes | SPDX id(s) or `license_name` |
| `tags` | yes | — | raw tag list |
| `downloads` | yes | yes | int64 |
| `likes` | yes | yes | int64 |
| `created_at` | yes | yes | ISO-8601 UTC |
| `modified_at` | yes | yes | ISO-8601 UTC |
| `metrics_refreshed_at` | yes | — | when counters were last captured |

## Key design decisions

- **Two-request page fetch.** Forced by the Hub API dropping base fields under
  any `expand`. Necessary to keep authoritative `size_b` *and* real
  `downloads`/`likes`.
- **Deterministic hash sharding.** `crc32(id) % N` gives even distribution,
  stable writes (same id → same shard), and zero lookup state — readers and
  writers agree by computation.
- **Fewer Parquet shards than JSONL shards.** Optimizes the per-query fan-out
  the browser pays, since there's no partition pruning (the id filter is
  substring `ILIKE %...%`).
- **Trim Parquet to queried columns.** `url`/`author`/`tags`/
  `metrics_refreshed_at` live only in the JSONL state; the browser reconstructs
  `url` from `id`. The biggest remaining Parquet columns are `id` and the two
  timestamps.
- **Sweep-based self-healing.** Schema/parsing fixes propagate without a
  migration: the metrics sweep re-touches every record (~1 day cycle), so
  enriched `format`/`license` and corrected counters populate naturally.

## Operational notes

- **`backfill_state.json`** is the source of truth for fetch progress:
  `complete: true` means the full catalog has been walked once; thereafter the
  sweep cycles to refresh counters.
- **Counter drift after a bad fetch.** If a bug zeroes a field on swept records
  (as the expand quirk did to `downloads`/`likes`), the records self-heal as the
  sweep re-touches them (~1 day). A `--reset-sweep` flag (not yet implemented)
  would let you restart the sweep from newest to correct popular models first.
- **Size guard.** If a shard would exceed 100 MB (catalog growth), the CI job
  fails loudly rather than deploying an unservable file.
