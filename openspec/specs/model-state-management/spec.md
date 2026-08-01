# Model State Management

## Purpose

Fetch and maintain Hugging Face model metadata as a compressed JSONL state file,
keeping it current with the Hub while bounding per-run work. Covers state I/O,
incremental updates by `modified_at` watermark, iterative historical backfill
via a persisted pagination cursor, continuous popularity-counter refresh
(metrics sweep), and parsing of model format, license, and parameter size.

## Requirements

### Requirement: Fetch and persist model metadata as sharded compressed JSONL
The system SHALL fetch Hugging Face model metadata via the Hub REST API
(`https://huggingface.co/api/models`) using `requests`, and persist it to disk
as a set of gzip-compressed JSON Lines shard files (`models-000.jsonl.gz` ..
`models-NNN.jsonl.gz`), keyed by model `id`, so that each published file stays
under GitHub's per-file size limit as the catalog grows.

#### Scenario: Sharding by id
- **WHEN** the system writes a record
- **THEN** it SHALL place it in shard `crc32(id) % SHARD_COUNT` (default `SHARD_COUNT = 8`), so each shard holds approximately `total / SHARD_COUNT` records and a given id deterministically maps to one shard

#### Scenario: First run with no existing state
- **WHEN** the fetcher runs and no shard files (and no legacy `models.jsonl.gz`) exist remotely
- **THEN** the system SHALL initialize an empty state dictionary and bootstrap from the HF API

#### Scenario: Migration from legacy single-file state
- **WHEN** the fetcher runs, no shard files exist remotely, but a legacy `models.jsonl.gz` does
- **THEN** the system SHALL load it into the state dict and, on write, re-shard it across `SHARD_COUNT` files (one-time migration)

#### Scenario: Subsequent run with existing shards
- **WHEN** the fetcher runs and one or more shard files exist remotely
- **THEN** the system SHALL download every shard (with a cache-busting `?t=<timestamp>` query parameter), decompress and merge them in memory into a dict keyed by model `id`

#### Scenario: Write state back to disk
- **WHEN** new/updated models have been merged into the state dict
- **THEN** the system SHALL bucket records by shard and write each `models-NNN.jsonl.gz` (gzip-compressed) atomically to disk

### Requirement: Fetch base fields and expansions via two merged requests
The HF list endpoint drops base fields (`downloads`, `likes`, `createdAt`, `author`, `tags`) whenever ANY `expand` is specified, returning only `{_id, id, <sort field>}` plus the expansions. Since the system needs both the base fields and the `safetensors` / `gguf` / `cardData` expansions (for authoritative `size_b` and structured `license`), it SHALL issue two requests for the same page (identical sort + cursor) and merge them by id: base fields from the no-expand call, expansions from the expand call. `tags` is part of the default response and is NOT expanded.

#### Scenario: Base fields come from the no-expand request
- **WHEN** the system fetches a page
- **THEN** it SHALL issue a no-expand request (`full=false`, `sort=lastModified`, `limit`, optional `cursor`) carrying `downloads`, `likes`, `createdAt`, `lastModified`, `author`, and `tags`

#### Scenario: Expansions come from a second request and are merged by id
- **WHEN** the base page is fetched
- **THEN** the system SHALL issue a second request with the same sort/cursor/limit plus `expand=safetensors` + `expand=gguf` + `expand=cardData`, and merge `safetensors` / `gguf` / `cardData` into each base record by id

#### Scenario: Catalog shift between the two requests
- **WHEN** a model appears in the base page but not in the expansions page (the catalog shifted between calls)
- **THEN** the system SHALL leave that record's expansions unset (so `size_b` falls back to tag/regex heuristics) and log a warning

### Requirement: Incremental update by modified_at watermark
The system SHALL avoid rescanning already-known models each run via an incremental pass that iterates from newest (no cursor) and stops at the most recent `modified_at` currently in state.

#### Scenario: Hourly incremental pass
- **WHEN** the fetcher runs and existing state contains at least one model
- **THEN** the system SHALL compute the max `modified_at` watermark and iterate the Hub sorted by `lastModified` descending, collecting only models modified strictly after the watermark, stopping once it reaches entries at or before the watermark

### Requirement: Iterative backfill via persisted pagination cursor
The system SHALL progressively index older historical models using the HF API's resumable pagination cursor, persisted in `backfill_state.json`, so each run resumes exactly where the last stopped without re-iterating already-fetched models.

#### Scenario: Backfill batch bounded per run
- **WHEN** the fetcher runs and backfill is not yet complete
- **THEN** the system SHALL resume from the persisted cursor and fetch up to `--limit` more (older) models, then persist the new cursor for the next run

#### Scenario: Backfill completes -> continuous metrics sweep begins
- **WHEN** a backfill pass consumes a page after which the API returns no `next` cursor
- **THEN** the system SHALL mark backfill complete in `backfill_state.json` and reset the cursor to newest, so subsequent runs keep cycling through the catalog as a **metrics sweep** that refreshes each model's `downloads`/`likes` (rather than stopping)

#### Scenario: Metrics sweep cycles indefinitely
- **WHEN** backfill is already complete and the fetcher runs
- **THEN** the system SHALL resume the sweep from the persisted cursor, refresh up to `--limit` models, and on exhaustion reset the cursor to newest to begin the next cycle - so every model's popularity counters are refreshed roughly once per (catalog_size / limit) runs

#### Scenario: Backfill disabled for a run
- **WHEN** the fetcher is invoked with `--no-backfill` (or `--limit 0`)
- **THEN** the system SHALL skip the backfill/sweep pass entirely and run only the incremental pass

#### Scenario: First run with empty state
- **WHEN** state is empty (remote shards and legacy `models.jsonl.gz` all return 404) and there is no backfill cursor
- **THEN** the system SHALL skip the incremental pass and run the backfill pass from newest (no cursor), collecting up to `--limit` models and persisting the resulting cursor

### Requirement: Popularity counter freshness
Because Hugging Face `downloads`/`likes` drift continuously and independently of `lastModified`, the system SHALL refresh them via the metrics sweep (not only the incremental pass) and SHALL stamp each refreshed record with a `metrics_refreshed_at` timestamp indicating when its counters were captured.

#### Scenario: Counters refreshed on every touch
- **WHEN** any pass (incremental, backfill, or sweep) writes a model record
- **THEN** the record SHALL carry `metrics_refreshed_at` set to the run's capture time

#### Scenario: Untouched records keep their last capture time
- **WHEN** a model is not visited during a run
- **THEN** its `metrics_refreshed_at` SHALL remain unchanged, surfacing how stale its counters are

### Requirement: Parse model format from tags
The system SHALL derive each model's `format` field from its tags array, returning the first known format in a fixed priority order: `gguf`, `awq`, `gptq`, `exl2`, `compressed-tensors`, `bitsandbytes`, `mlx`, `bitnet`, `onnx`.

#### Scenario: Known format tag present
- **WHEN** a model's tags include one of the known format values above (case-insensitive)
- **THEN** the system SHALL set `format` to the first matching value in the priority order

#### Scenario: No known format tag
- **WHEN** none of the known format values appear in the tags
- **THEN** the system SHALL set `format` to `"unknown"`

### Requirement: Parse license from cardData, then tags
The system SHALL derive each model's `license` field from the structured `cardData` metadata, falling back to tags.

#### Scenario: Structured license available
- **WHEN** `cardData.license` is a non-empty SPDX id (other than the placeholder `"other"`)
- **THEN** the system SHALL set `license` to that value

#### Scenario: Multi-licensed model
- **WHEN** `cardData.license` is a list of SPDX ids
- **THEN** the system SHALL set `license` to a comma-separated join of the non-empty ids (e.g. `"apache-2.0, mit"`)

#### Scenario: Placeholder `other` with a license_name
- **WHEN** `cardData.license` is `"other"` and `cardData.license_name` is a non-empty string
- **THEN** the system SHALL set `license` to `cardData.license_name`

#### Scenario: License tag fallback
- **WHEN** no usable `cardData.license` / `license_name` is present but the tags contain a `license:<id>` entry
- **THEN** the system SHALL set `license` to `<id>`

#### Scenario: License cannot be determined
- **WHEN** none of the above yield a license
- **THEN** the system SHALL set `license` to null

### Requirement: Parse parameter size from authoritative metadata first, then tags, then model ID
The system SHALL derive `size_b` (in billions of parameters, as a float) with the following priority: (1) the Hub's authoritative parameter count exposed via the `safetensors.total` or `gguf.total` expansion (`expand=safetensors` + `expand=gguf` on the list endpoint); (2) a `size:<n>b` tag; (3) a regex on the model id (`<n>b`, MoE `NxNb`); (4) null.

#### Scenario: Authoritative parameter count available
- **WHEN** the Hub returns `safetensors.total` or `gguf.total` for a model
- **THEN** the system SHALL set `size_b` to `round(total / 1e9, 2)` (this wins over tags and name heuristics)

#### Scenario: Size tag present (no authoritative count)
- **WHEN** no authoritative count is present but the tags contain a `size:<n>b` pattern (e.g. `size:7b`)
- **THEN** the system SHALL parse `<n>` and set `size_b` to that float value

#### Scenario: Size inferred from model ID
- **WHEN** neither an authoritative count nor a size tag is present but the model ID matches a pattern like `-<n>b` or `NxNb` (e.g. `8x7b`)
- **THEN** the system SHALL compute the size (for `NxNb`, multiply N × size; e.g. `8x7b` → `56.0`) and set `size_b`

#### Scenario: Size cannot be determined
- **WHEN** none of authoritative count, tags, or model ID yield a parseable size
- **THEN** the system SHALL set `size_b` to null

### Requirement: Schema compliance
Each JSONL record SHALL conform to the schema: `id`, `author`, `url`, `size_b`, `format`, `license`, `tags`, `downloads`, `likes`, `created_at`, `modified_at`, `metrics_refreshed_at`. (Legacy records may carry a `quant` key in place of `format`/`license`; the builder SHALL drop `quant` and backfill the canonical columns during Parquet build.)

#### Scenario: Record fields
- **WHEN** a model record is written to a `models-NNN.jsonl.gz` shard
- **THEN** the record SHALL include all schema fields, with `id` as `"author/model"`, `url` as the full `https://huggingface.co/...` URL, dates as ISO-8601 UTC strings, and numeric fields (`downloads`, `likes`) as integers

### Requirement: Rate limit handling
The system SHALL handle Hugging Face API HTTP 429 responses gracefully without producing silently incomplete state.

#### Scenario: Transient 429
- **WHEN** the HF API returns HTTP 429
- **THEN** the system SHALL retry with bounded exponential backoff

#### Scenario: Persistent 429
- **WHEN** the retry cap is exhausted
- **THEN** the system SHALL fail loudly (non-zero exit) rather than write partial state silently
