# Proposal

## Why

The Hub catalog contains a large share of low-signal repositories (empty
README-only repos, zero-download spam uploads), and the current UI has no way
to cut through that noise or to take results out of the browser. This change
adds the highest value-to-effort search-quality and UX features identified in
review — all client-side or build-time, preserving the project's no-backend,
no-tracking philosophy.

## What Changes

- **Min-downloads filter** — a new numeric filter (`downloads >= N`) in the
  filter form; the strongest cheap anti-spam lever (frontend-only).
- **Local author blocklist** — users can hide all models from a specific
  author/org via a per-row block action in the results table; a toolbar button
  opens a management modal (list blocked authors, remove entries, clear all).
  Persisted entirely in `localStorage`; active block count and hidden-results
  feedback are surfaced in the UI (frontend-only).
- **Broken/empty model detection** — a new `has_weights` boolean derived at
  fetch time (where the authoritative `safetensors`/`gguf` expansion data
  lives), stored in the JSONL state and published as a Parquet column. The UI
  gains a "hide empty repos" checkbox (pipeline change; propagates catalog-wide
  via the existing metrics sweep in ~1 day, no backfill migration).
- **Copy download command** — a per-row action copies
  `huggingface-cli download <repo_id>` to the clipboard (frontend-only).
- **Export results (CSV/JSON)** — download the active filtered query as CSV or
  JSON. Export re-runs the query without the UI's 500-row render limit (with an
  explicit high cap), so data analysts get the full result set, not the
  rendered page (frontend-only).

## Capabilities

### New Capabilities

- `author-blocklist`: Client-side hiding of models by author/org — per-row
  block action, `localStorage` persistence, modal management UI, and
  hidden-results feedback. No server involvement.
- `result-export`: Client-side export of the active filtered query as CSV or
  JSON files, bounded by an explicit export cap rather than the render limit.

### Modified Capabilities

- `frontend-query-ui`: The filter UI requirement gains a min-downloads filter
  and a "hide empty repos" checkbox; the results-rendering requirement gains
  per-row actions (block author, copy download command).
- `model-state-management`: Record schema gains a `has_weights` boolean derived
  at parse time from the `safetensors`/`gguf` expansions.
- `parquet-build`: The frontend-facing column set grows to include
  `has_weights`, with null-tolerant backfill for un-swept legacy records.

## Impact

- `scripts/fetch_updates.py` — new `has_weights` field in `RECORD_KEYS` and
  parse logic.
- `scripts/build_parquet.py` — `PARQUET_COLUMNS` gains `has_weights`
  (boolean-typed, null-backfilled).
- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` — new filter
  inputs, blocklist modal + per-row actions, copy-command button, export
  buttons; SQL construction extended (downloads predicate, `has_weights`
  predicate, blocklist `NOT LIKE` clauses, uncapped export query).
- No changes to the deployment pipeline, shard counts, or API request volume.
- Parquet size impact is negligible (one boolean column).
