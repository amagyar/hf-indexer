## ADDED Requirements

### Requirement: Build typed Parquet from JSONL state
The system SHALL convert `models.jsonl.gz` into `models.parquet` using `pandas` (which reads gzip natively) and the `pyarrow` engine.

#### Scenario: Read compressed JSONL
- **WHEN** the builder runs and `models.jsonl.gz` exists
- **THEN** the system SHALL load all records into a typed pandas DataFrame

#### Scenario: Write Parquet
- **WHEN** the DataFrame has been assembled
- **THEN** the system SHALL write `models.parquet` using the pyarrow engine with `zstd` compression

### Requirement: Enforce column types for DuckDB optimization
The system SHALL assign explicit, DuckDB-friendly types to columns before writing Parquet.

#### Scenario: Numeric and temporal typing
- **WHEN** the builder assembles the schema
- **THEN** `size_b` SHALL be `float32`, `downloads` and `likes` SHALL be integers, and `created_at`/`modified_at` SHALL be timestamps

### Requirement: Builder runs after fetch in CI
The Parquet builder SHALL be invoked only after `fetch_updates.py` has produced a fresh `models.jsonl.gz`.

#### Scenario: CI ordering
- **WHEN** the GitHub Actions workflow executes
- **THEN** the build_parquet step SHALL run after the fetch_updates step and read the freshly written `models.jsonl.gz`
