# Spec Delta

## Purpose

Lets users download the current filtered query as a CSV or JSON file for
offline analysis, executing fully client-side over the already-loaded Parquet
data — turning the in-browser search into a data-extraction tool.

## ADDED Requirements

### Requirement: Export the active query as CSV or JSON
The frontend SHALL offer CSV and JSON export actions that re-execute the currently active filter query (same predicates and ordering) and download the result set as a file, generated entirely in the browser.

#### Scenario: CSV export
- **WHEN** the user triggers a CSV export with filters active
- **THEN** the system SHALL download a `.csv` file containing a header row followed by one row per result, with values escaped per standard CSV rules

#### Scenario: JSON export
- **WHEN** the user triggers a JSON export with filters active
- **THEN** the system SHALL download a `.json` file containing an array of objects, one per result, keyed by column name

#### Scenario: Export reflects filters, not the rendered page
- **WHEN** the user exports while the results table is showing the (capped) rendered page
- **THEN** the exported file SHALL contain the full matching result set for the active filters, not only the rows currently rendered

### Requirement: Export row cap with truncation notice
Exports SHALL be bounded by an explicit maximum row count (an export cap, independent of and larger than the render limit), and the UI SHALL tell the user when the export was truncated by the cap.

#### Scenario: Result set within the cap
- **WHEN** the active query matches fewer rows than the export cap
- **THEN** the export SHALL contain every matching row and no truncation notice

#### Scenario: Result set exceeds the cap
- **WHEN** the active query matches more rows than the export cap
- **THEN** the export SHALL stop at the cap and the UI SHALL indicate that results were truncated

### Requirement: Export is client-side only
Export SHALL be computed from the in-browser query engine over the registered Parquet shards; no external service SHALL be involved in producing the file.

#### Scenario: No export endpoint
- **WHEN** the user exports
- **THEN** the file SHALL be constructed in the browser (triggering only the existing Parquet HTTP range reads) and saved via a local download
