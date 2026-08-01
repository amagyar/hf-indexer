# Frontend Query UI

## Purpose

Static, dark-mode frontend that initializes DuckDB WASM in the browser, registers
the Parquet over HTTP Range, and offers text / size / format / license /
date-range filtering with a results table. All query execution is client-side.

## Requirements

### Requirement: DuckDB WASM initialization
The frontend SHALL initialize `@duckdb/duckdb-wasm` from a pinned CDN (JsDelivr) with correct worker instantiation in the browser.

#### Scenario: WASM bootstrap
- **WHEN** the page loads
- **THEN** the system SHALL instantiate DuckDB via its worker and expose a connection for querying before any user interaction

#### Scenario: Initialization failure surfaced
- **WHEN** DuckDB fails to initialize (e.g. CDN unreachable)
- **THEN** the system SHALL display an error state to the user rather than silently failing

### Requirement: Register Parquet over HTTP Range
The frontend SHALL register `models.parquet` via HTTP protocol so DuckDB issues Range requests instead of downloading the whole file, falling back to a full buffer download if HTTP registration fails.

#### Scenario: Register file URL
- **WHEN** DuckDB is initialized
- **THEN** the system SHALL call `db.registerFileURL('models.parquet', <absolute_url>, DuckDBDataProtocol.HTTP, false)` (URL resolved against `document.baseURI`, since the worker runs inside a blob) and create a view `CREATE VIEW models AS SELECT * FROM read_parquet('models.parquet')`

#### Scenario: Buffer fallback
- **WHEN** HTTP registration or the view creation fails
- **THEN** the system SHALL fetch the whole Parquet once and register it via `db.registerFileBuffer`, then create the view

### Requirement: Dark-mode filter UI
The frontend SHALL render a dark-mode interface with: text search (model ID), min/max size (float), format dropdown (known formats + `unknown`), license free-text search, `created_at` range (from/to date pickers), `modified_at` range (from/to date pickers), a Search button, and a results table.

#### Scenario: Filter inputs present
- **WHEN** the page renders
- **THEN** the user SHALL see all filter inputs (text, min size, max size, format, license, created from/to, modified from/to), a Search button, and an empty results table

### Requirement: Parameterized SQL query construction
The frontend SHALL build parameterized SQL queries from the active filters.

#### Scenario: Apply filters
- **WHEN** the user submits filters
- **THEN** the system SHALL construct SQL (e.g. `SELECT * FROM models WHERE size_b >= ? AND format = ?`), with `format` matched by equality and `license` matched case-insensitively by substring (`license ILIKE ?`), using bound parameters for non-empty filters only

#### Scenario: Date-range filters are inclusive of the whole day
- **WHEN** the user supplies a `created_at` or `modified_at` from/to date
- **THEN** the system SHALL bound the column to `[YYYY-MM-DDT00:00:00Z, YYYY-MM-DDT23:59:59Z]` so a single date includes the entire UTC day, emitting a parameterized predicate only for populated date fields

#### Scenario: Date params cast for the prepared-statement binder
- **WHEN** a date predicate is compared against a `TIMESTAMP WITH TIME ZONE` column
- **THEN** the system SHALL wrap each date parameter in `CAST(? AS TIMESTAMPTZ)` so the comparison type-checks under the strict WASM prepared-statement binder

### Requirement: Results rendering with loading state
The frontend SHALL render query results into the DOM table and indicate loading while a query is in flight.

#### Scenario: Loading indicator
- **WHEN** a query is in flight
- **THEN** the UI SHALL show a loading state and disable the Search button

#### Scenario: Render rows
- **WHEN** the query returns rows
- **THEN** the system SHALL render each row into the results table, mapping schema fields to columns
