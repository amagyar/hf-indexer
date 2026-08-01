# Parquet Build

## Purpose

Convert the gzip-compressed JSONL state into a typed, zstd-compressed Parquet
file optimized for DuckDB WASM HTTP Range reads in the browser.

## Requirements

### Requirement: Build typed Parquet shards from sharded JSONL state
The system SHALL convert the sharded `models-NNN.jsonl.gz` state into a set of `models-NNN.parquet` shards using `pandas` (which reads gzip natively) and the `pyarrow` engine, emitting only the frontend-facing columns (`id`, `size_b`, `format`, `license`, `downloads`, `likes`, `created_at`, `modified_at`). Parquet shards are distributed by `crc32(id) % PARQUET_SHARD_COUNT` (default `4`) so each published file stays under GitHub Pages' per-file size limit.

#### Scenario: Read sharded compressed JSONL
- **WHEN** the builder runs and one or more `models-NNN.jsonl.gz` shards exist
- **THEN** the system SHALL load every shard into a typed pandas DataFrame, falling back to the legacy single `models.jsonl.gz` if no shards are present

#### Scenario: Write Parquet shards
- **WHEN** the DataFrame has been assembled
- **THEN** the system SHALL bucket rows by `crc32(id) % PARQUET_SHARD_COUNT` and write each `models-NNN.parquet` (zstd compression)

#### Scenario: Frontend-only columns
- **WHEN** the builder assembles the schema
- **THEN** it SHALL emit only `id`, `size_b`, `format`, `license`, `downloads`, `likes`, `created_at`, `modified_at`; `url`, `author`, `tags`, and `metrics_refreshed_at` are NOT published (the frontend reconstructs `url` from `id`)

### Requirement: Enforce column types for DuckDB optimization
The system SHALL assign explicit, DuckDB-friendly types to columns before writing Parquet.

#### Scenario: Numeric and temporal typing
- **WHEN** the builder assembles the schema
- **THEN** `size_b` SHALL be `float32`, `downloads` and `likes` SHALL be integers, and `created_at`/`modified_at`/`metrics_refreshed_at` SHALL be timestamps

#### Scenario: String columns
- **WHEN** the builder assembles the schema
- **THEN** `id`, `author`, `url`, `format`, and `license` SHALL be UTF-8 strings, and `tags` SHALL be a list of strings

### Requirement: Normalize to a canonical column schema
The system SHALL emit a stable, canonical column set matching the fetcher's `RECORD_KEYS`, regardless of how many records have been re-swept after a schema change.

#### Scenario: Legacy `quant` column dropped
- **WHEN** the JSONL contains records carrying a legacy `quant` key (from before the `quant` -> `format` rename)
- **THEN** the builder SHALL drop the `quant` column during normalization rather than emit it to Parquet

#### Scenario: Missing canonical columns backfilled
- **WHEN** a canonical column (e.g. `format` or `license`) is absent from some records because they have not yet been re-swept
- **THEN** the builder SHALL add the column (null for un-swept records) and emit it in the canonical order

### Requirement: Builder runs after fetch in CI
The Parquet builder SHALL be invoked only after `fetch_updates.py` has produced a fresh set of sharded `models-NNN.jsonl.gz` files.

#### Scenario: CI ordering
- **WHEN** the GitHub Actions workflow executes
- **THEN** the build_parquet step SHALL run after the fetch_updates step, read the freshly written `models-NNN.jsonl.gz` shards, and emit `models-NNN.parquet` shards
