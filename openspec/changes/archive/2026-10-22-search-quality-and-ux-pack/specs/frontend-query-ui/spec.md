# Spec Delta

## MODIFIED Requirements

### Requirement: Dark-mode filter UI
The frontend SHALL render a dark-mode interface with: text search (model ID), min/max size (float), format free-text search (with a `<datalist>` of common suggestions), license free-text search, `created_at` range (from/to date pickers), `modified_at` range (from/to date pickers), a minimum-downloads filter (numeric), a "hide empty repos" checkbox (default checked), a Search button, and a results table.

#### Scenario: Filter inputs present
- **WHEN** the page renders
- **THEN** the user SHALL see all filter inputs (text, min size, max size, format, license, created from/to, modified from/to, min downloads, hide-empty-repos checkbox), a Search button, and an empty results table

#### Scenario: Min-downloads filter applied
- **WHEN** the user sets a min-downloads value of N and searches
- **THEN** only models with `downloads >= N` SHALL be returned

#### Scenario: Hide empty repos hides only confirmed-empty models
- **WHEN** the hide-empty-repos checkbox is checked and a model's weight-presence field is null (not yet re-swept by the pipeline)
- **THEN** that model SHALL NOT be hidden by this filter; only models explicitly marked as having no weight files SHALL be hidden

#### Scenario: Hide empty repos disabled
- **WHEN** the user unchecks the hide-empty-repos checkbox and searches
- **THEN** models without weight files SHALL be included in the results

### Requirement: Results rendering with loading state
The frontend SHALL render query results into the DOM table, indicate loading while a query is in flight, and provide per-row actions to copy a download command for that model and to block its author from results.

#### Scenario: Loading indicator
- **WHEN** a query is in flight
- **THEN** the UI SHALL show a loading state and disable the Search button

#### Scenario: Render rows
- **WHEN** the query returns rows
- **THEN** the system SHALL render each row into the results table, mapping schema fields to columns

#### Scenario: Copy download command
- **WHEN** the user activates the copy action on a row whose model id is `org/model-name`
- **THEN** the clipboard SHALL contain `huggingface-cli download org/model-name` and the UI SHALL confirm the copy

#### Scenario: Block author from a row
- **WHEN** the user activates the block action on a row
- **THEN** the author of that model SHALL be added to the blocklist and its models hidden, per the author-blocklist capability
