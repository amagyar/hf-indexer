# Design

## Context

See proposal.md — Why. The system today is a static frontend querying 8-column
Parquet shards via DuckDB-WASM (`frontend/app.js`), fed by
`fetch_updates.py` (JSONL state, two-request page merge carrying the
`safetensors`/`gguf` expansions) and `build_parquet.py`
(`PARQUET_COLUMNS` → 4 zstd shards). The sweep re-touches the full catalog
roughly once per day, which is the built-in migration path for schema
additions. All new UI must stay vanilla HTML/CSS/JS with no build step and no
new network dependencies.

## Goals / Non-Goals

**Goals:**

- Ship five approved features with zero backend changes and zero new external
  dependencies.
- Make the `has_weights` pipeline change safe to deploy in any order relative
  to the frontend.

**Non-Goals:**

- VRAM estimation, clone/original detection, model-family grouping (rejected
  in review).
- Per-file data (filenames, quant levels, file sizes) — would require
  per-model fetches; explicitly out of scope.
- Any server-side blocklist, sync, or telemetry.

## Decisions

### D1: `has_weights` derived at fetch time, not build time

The authoritative weight signals (`safetensors` / `gguf` expansions) exist
only transiently in the fetcher — they are discarded after `size_b` is
computed and never stored in the JSONL. Deriving at parse time (where the raw
signals live) is strictly more accurate than the build-time approximation
(`size_b IS NOT NULL OR format != 'unknown'`), at the cost of a schema
addition that the sweep propagates naturally.

Signal rule: `has_weights = true` iff any of — non-empty `safetensors`
expansion, non-empty `gguf` expansion, a `KNOWN_FORMATS` tag, or a parseable
size signal (tag/regex). Else false.

*Alternative considered:* deriving in `build_parquet.py` from existing JSONL
fields — no schema change, but indistinguishable from the client-side
approximation and misclassifies the same edge cases.

### D2: Null means "not yet swept", never "empty"

Records not yet re-touched carry no `has_weights`; the builder emits null for
them (never false). The frontend's hide-empty filter hides only `has_weights
= false`, so unknown records are never hidden during the ~1-day propagation
window. This is the same pattern the `quant` → `format`/`license` rename used
("Missing canonical columns backfilled").

### D3: Frontend feature-detects the column (deployment-order-independent)

Before building the hide-empty predicate, the frontend runs `DESCRIBE models`
and checks for `has_weights`. If absent (stale published Parquet, local dev
with old shards), the checkbox renders disabled with a hint instead of
emitting SQL that would 500 the prepared binder. This makes the frontend
deployable before the next pipeline run lands the column — and vice versa.

### D4: Hide-empty checkbox defaults to ON

The point of the feature is noise removal, and D2 guarantees only
confirmed-empty repos hide. Users who want the raw catalog uncheck one box.

*Alternative considered:* default OFF (zero surprise for existing users) —
rejected because it ships the feature effectively disabled for the audience
that asked for it. Easy to flip before release.

### D5: Blocklist = localStorage array + parameterized `starts_with` clauses

Storage: `localStorage["hf-indexer:blocklist"]` as a JSON array of author
strings (exact `org/` prefix semantics). Query: for each blocked author, add
`NOT starts_with(id, ?)` with `author + '/'` as a bound parameter — a literal
prefix match, so there are no LIKE wildcards (`_`, `%`) to escape in author
names. UI: a per-row 🚫 action; a toolbar button ("Blocked (N)") opening a
native `<dialog>` modal listing entries with per-item remove and clear-all.
The native dialog keeps the no-dependency constraint.

Hidden-results feedback: when the blocklist is non-empty, run one extra
`SELECT count(*)` counting rows that match the active filters AND any blocked
author prefix (`starts_with(id, ?) OR …`), and show "N hidden by blocklist"
(clickable → opens the modal). Counting the hidden rows directly — rather
than comparing totals with and without the blocklist clauses — stays correct
even when the visible result set exceeds the 500-row render cap. When the
blocklist is empty the extra query is skipped entirely.

### D6: Export re-runs the query with an export cap, serialized in JS

Export executes the same WHERE/ORDER BY with `LIMIT 100000`
(`EXPORT_ROW_CAP` constant) instead of the render limit, then serializes in
JavaScript: CSV built with a small escaper (quote doubling, RFC-4180-style),
JSON via `JSON.stringify` of the row array. Download via `Blob` + object URL +
`<a download>`. Truncation notice shown when the result count hits the cap.

*Alternative considered:* DuckDB-WASM `COPY (…) TO 'result.csv'` — avoids JS
serialization but requires output-file registration plumbing in the worker and
buys nothing at these sizes (~100k small rows ≈ low tens of MB).

### D7: Copy command via Clipboard API with fallback

`navigator.clipboard.writeText("huggingface-cli download " + id)` with a
transient "copied" affordance on the row action. GitHub Pages and localhost
are secure contexts, so the API is available; a `try/catch` fallback to a
temporary selected-text copy keeps it working elsewhere.

### D8: Min-downloads follows the existing parameterization pattern

Numeric input → predicate `downloads >= ?`, bound only when non-empty, with
the value coerced to integer (the WASM prepared-statement binder is strict;
the date filters already wrap params in `CAST` for the same reason).

## Risks / Trade-offs

- [`.bin`-only legacy repos classify as `has_weights = false`] → Accepted:
  the list API exposes no file listing without per-model fetches; such repos
  are rare and shrinking. Documented in README caveats.
- [Blocklist with many entries grows SQL length and predicate count] →
  Accepted: user-curated lists are naturally small; bound per-entry cost to
  one `NOT LIKE`. No hard cap for now.
- [Export of 100k rows holds the result set in memory] → Accepted: DuckDB
  arrow tables stream; we slice into JS only at the cap. ~tens of MB worst
  case, within desktop browser budgets.
- [Extra count query doubles blocklist-active query cost] → Mitigated by
  skipping it when the blocklist is empty (the overwhelmingly common case).
- [`<dialog>` element support] → Baseline in all evergreen browsers since
  2022; acceptable for this project's audience.

## Migration Plan

1. Land `fetch_updates.py` + `build_parquet.py` changes — next scheduled run
   (≤ 3h) publishes the `has_weights` column; sweep fills values over ~1 day
   (nulls until then, which the frontend tolerates by design).
2. Land frontend changes — safe at any time relative to step 1 thanks to D3.
3. Rollback: revert frontend for UI features; reverting the pipeline simply
   drops the column again (D3 makes the frontend tolerate that too).
