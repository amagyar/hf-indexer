# author-blocklist Specification

## Purpose
Lets users hide all models from specific authors/organizations directly in the
browser — persisted locally, managed via a modal, with visible feedback — so
spammy publishers disappear from search without any server-side state.

## Requirements

### Requirement: Block an author from the results table
The frontend SHALL offer a per-row block action in the results table that, when activated, immediately hides every model whose id belongs to that author (`<author>/` prefix) from the current and subsequent queries, without a page reload.

#### Scenario: Blocking from a result row
- **WHEN** the user activates the block action on a row whose model id is `org/model-name`
- **THEN** the author `org` is added to the blocklist and all models with ids starting `org/` disappear from the rendered results immediately

#### Scenario: Blocked author excluded from new queries
- **WHEN** a blocklist entry exists for `org` and the user runs any subsequent search
- **THEN** no model from `org` SHALL appear in the results

### Requirement: Blocklist persists locally only
The blocklist SHALL be stored in the browser's `localStorage` and SHALL NOT be transmitted anywhere — no network request, analytics, or published file SHALL carry blocklist data.

#### Scenario: Persistence across sessions
- **WHEN** the user reloads the page or returns later in the same browser
- **THEN** the blocklist SHALL be loaded from `localStorage` and applied to queries

#### Scenario: No server involvement
- **WHEN** blocklist entries are added, removed, or applied
- **THEN** no request beyond the existing Parquet range reads SHALL be issued

### Requirement: Blocklist management modal
The frontend SHALL provide a toolbar button that opens a modal listing all blocked authors, where each entry can be removed individually and the whole list can be cleared.

#### Scenario: Opening the modal
- **WHEN** the user activates the toolbar blocklist button
- **THEN** a modal SHALL open listing every blocked author

#### Scenario: Removing an entry
- **WHEN** the user removes an author from the modal
- **THEN** that author's models SHALL be visible again in subsequent queries and the entry SHALL disappear from `localStorage`

#### Scenario: Clearing all entries
- **WHEN** the user activates "clear all" in the modal
- **THEN** the blocklist SHALL become empty and no models SHALL be hidden by it

### Requirement: Blocklist state visibility
The toolbar blocklist button SHALL display the number of active entries whenever the blocklist is non-empty, and the UI SHALL surface that results are being hidden by the blocklist.

#### Scenario: Active count shown
- **WHEN** the blocklist contains N > 0 entries
- **THEN** the toolbar button SHALL display N

#### Scenario: Hidden-results feedback
- **WHEN** the current query's unfiltered result set contains models hidden by the blocklist
- **THEN** the UI SHALL indicate that hidden results exist and provide a path to the management modal from that indication
