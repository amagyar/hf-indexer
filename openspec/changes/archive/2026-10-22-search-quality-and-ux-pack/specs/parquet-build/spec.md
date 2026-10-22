# Spec Delta

## MODIFIED Requirements

### Requirement: Build typed Parquet shards from sharded JSONL state
The system SHALL convert the sharded `models-NNN.jsonl.gz` state into a set of `models-NNN.parquet` shards using `pandas` (which reads gzip natively) and the `pyarrow` engine, emitting only the frontend-facing columns (`id`, `size_b`, `format`, `license`, `has_weights`, `downloads`, `likes`, `created_at`, `modified_at`). Parquet shards are distributed by `crc32(id) % PARQUET_SHARD_COUNT` (default `4`) so each published file stays under GitHub Pages' per-file size limit.

#### Scenario: Read sharded compressed JSONL
- **WHEN** the builder runs and one or more `models-NNN.jsonl.gz` shards exist
- **THEN** the system SHALL load every shard into a typed pandas DataFrame, falling back to the legacy single `models.jsonl.gz` if no shards are present

#### Scenario: Write Parquet shards
- **WHEN** the DataFrame has been assembled
- **THEN** the system SHALL bucket rows by `crc32(id) % PARQUET_SHARD_COUNT` and write each `models-NNN.parquet` (zstd compression)

#### Scenario: Frontend-only columns
- **WHEN** the builder assembles the schema
- **THEN** it SHALL emit only `id`, `size_b`, `format`, `license`, `has_weights`, `downloads`, `likes`, `created_at`, `modified_at`; `url`, `author`, `tags`, and `metrics_refreshed_at` are NOT published (the frontend reconstructs `url` from `id`)

#### Scenario: Missing `has_weights` backfilled as null
- **WHEN** some records lack `has_weights` because they have not yet been re-swept since the field was introduced
- **THEN** the builder SHALL emit the column with null for those records (never false), so downstream consumers can distinguish unknown from confirmed-empty

### Requirement: Enforce column types for DuckDB optimization
The system SHALL assign explicit, DuckDB-friendly types to columns before writing Parquet.

#### Scenario: Numeric and temporal typing
- **WHEN** the builder assembles the schema
- **THEN** `size_b` SHALL be `float32`, `downloads` and `likes` SHALL be integers, `has_weights` SHALL be boolean (nullable), and `created_at`/`modified_at`/`metrics_refreshed_at` SHALL be timestamps

#### Scenario: String columns
- **WHEN** the builder assembles the schema
- **THEN** `id`, `author`, `url`, `format`, and `license` SHALL be UTF-8 strings, and `tags` SHALL be a list of strings
