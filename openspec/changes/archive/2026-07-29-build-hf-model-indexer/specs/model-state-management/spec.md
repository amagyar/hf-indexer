## ADDED Requirements

### Requirement: Fetch and persist model metadata as compressed JSONL
The system SHALL fetch Hugging Face model metadata via `huggingface_hub.HfApi` and persist it to disk as `models.jsonl.gz` (gzip-compressed JSON Lines), keyed by model `id`, to keep file size under GitHub's per-file limit.

#### Scenario: First run with no existing state
- **WHEN** the fetcher runs and the remote `models.jsonl.gz` returns HTTP 404
- **THEN** the system SHALL initialize an empty state dictionary and bootstrap from the HF API

#### Scenario: Subsequent run with existing state
- **WHEN** the fetcher runs and a remote `models.jsonl.gz` exists
- **THEN** the system SHALL download it (with a cache-busting `?t=<timestamp>` query parameter), decompress it in memory, and load entries into a dict keyed by model `id`

#### Scenario: Write state back to disk
- **WHEN** new/updated models have been merged into the state dict
- **THEN** the system SHALL serialize the dict to JSONL and write `models.jsonl.gz` (gzip-compressed) atomically to disk

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
- **WHEN** state is empty (remote `models.jsonl.gz` returns 404) and there is no backfill cursor
- **THEN** the system SHALL skip the incremental pass and run the backfill pass from newest (no cursor), collecting up to `--limit` models and persisting the resulting cursor

### Requirement: Popularity counter freshness
Because Hugging Face `downloads`/`likes` drift continuously and independently of `lastModified`, the system SHALL refresh them via the metrics sweep (not only the incremental pass) and SHALL stamp each refreshed record with a `metrics_refreshed_at` timestamp indicating when its counters were captured.

#### Scenario: Counters refreshed on every touch
- **WHEN** any pass (incremental, backfill, or sweep) writes a model record
- **THEN** the record SHALL carry `metrics_refreshed_at` set to the run's capture time

#### Scenario: Untouched records keep their last capture time
- **WHEN** a model is not visited during a run
- **THEN** its `metrics_refreshed_at` SHALL remain unchanged, surfacing how stale its counters are

### Requirement: Parse quantization from tags
The system SHALL derive each model's `quant` field from its tags array.

#### Scenario: Known quantization tag present
- **WHEN** a model's tags include one of `gguf`, `awq`, `gptq`, or `exl2`
- **THEN** the system SHALL set `quant` to that value

#### Scenario: No known quantization tag
- **WHEN** none of `gguf`, `awq`, `gptq`, `exl2` appear in the tags
- **THEN** the system SHALL set `quant` to `"unknown"`

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
Each JSONL record SHALL conform to the schema: `id`, `author`, `url`, `size_b`, `quant`, `tags`, `downloads`, `likes`, `created_at`, `modified_at`, `metrics_refreshed_at`.

#### Scenario: Record fields
- **WHEN** a model record is written to `models.jsonl.gz`
- **THEN** the record SHALL include all schema fields, with `id` as `"author/model"`, `url` as the full `https://huggingface.co/...` URL, dates as ISO-8601 UTC strings, and numeric fields (`downloads`, `likes`) as integers

### Requirement: Rate limit handling
The system SHALL handle Hugging Face API HTTP 429 responses gracefully without producing silently incomplete state.

#### Scenario: Transient 429
- **WHEN** the HF API returns HTTP 429
- **THEN** the system SHALL retry with bounded exponential backoff

#### Scenario: Persistent 429
- **WHEN** the retry cap is exhausted
- **THEN** the system SHALL fail loudly (non-zero exit) rather than write partial state silently
