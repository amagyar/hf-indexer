# Tasks

## 1. Pipeline: `has_weights` field

- [x] 1.1 Add `has_weights` to `RECORD_KEYS` and implement the parse rule in `scripts/fetch_updates.py` (true iff non-empty `safetensors` expansion, non-empty `gguf` expansion, `KNOWN_FORMATS` tag, or parseable size signal). Verify by running a fetch with `--no-backfill --limit` small against the API and inspecting a few written JSONL records for correct true/false values (empty README-only repos → false, known GGUF repos → true).
- [x] 1.2 Add `has_weights` to `PARQUET_COLUMNS` in `scripts/build_parquet.py` as a nullable boolean (null for records lacking the field, never false). Verify by running the builder on state that mixes swept and un-swept records and checking `pyarrow` reports the column as bool with nulls preserved.
- [x] 1.3 Rebuild Parquet locally and confirm the size guard still passes and shard growth is negligible (compare shard file sizes before/after).

## 2. Frontend: min-downloads and hide-empty filters

- [x] 2.1 Add the min-downloads numeric input and the hide-empty checkbox (default checked) to `frontend/index.html` in the existing filter form, and extend SQL construction in `frontend/app.js` with `downloads >= ?` (bound only when non-empty, integer-coerced) and `has_weights` predicate (hides only `false`, never null). Verify with a local query that the downloads threshold and the checkbox change result sets as expected.
- [x] 2.2 Implement the `DESCRIBE models` column feature-detect: when `has_weights` is absent from the published Parquet, render the checkbox disabled with a hint and omit its predicate. Verify by pointing the frontend at old-shape Parquet files and confirming queries still succeed.
- [x] 2.3 Add Playwright coverage in `tests/e2e/index.spec.js`: min-downloads filter narrows results; unchecking hide-empty widens them (or the checkbox is disabled when the column is absent). Verify `SITE_URL=http://localhost:8000 npm test` passes.

## 3. Frontend: per-row actions (copy command, block author)

- [x] 3.1 Add an actions cell to each rendered row in `frontend/app.js` with a copy button (`huggingface-cli download <id>` via `navigator.clipboard.writeText`, `try/catch` fallback, transient "copied" affordance). Verify in the browser that the clipboard receives the exact command string.
- [x] 3.2 Add a per-row block button that adds the row's author to the blocklist and triggers an immediate re-query. Verify blocking an org from a result removes all its models without a page reload.

## 4. Blocklist: persistence, modal, feedback

- [x] 4.1 Implement the blocklist store in `frontend/app.js`: `localStorage["hf-indexer:blocklist"]` JSON array, load-on-init, save-on-mutation, and per-entry `id NOT LIKE ?` SQL clauses with `_`/`%` escaped in the author prefix. Verify by reloading the page and confirming blocked authors stay excluded and no extra network requests fire.
- [x] 4.2 Add the toolbar button with active-entry count ("Blocked (N)") and the native `<dialog>` management modal (list entries, remove one, clear all). Verify each modal action round-trips through `localStorage` and the next query.
- [x] 4.3 Implement hidden-results feedback: when the blocklist is non-empty, issue one extra `count(*)` query with the blocklist clauses removed and render "N hidden by blocklist" (clickable → opens the modal). Verify the indicator appears when a blocked author matches the active filters and disappears when the blocklist is empty or nothing matches.
- [x] 4.4 Add Playwright coverage: block from a row → results update and count indicator appears; open modal → remove entry → models return. Verify `npm test` passes.

## 5. Export: CSV/JSON download

- [x] 5.1 Implement export in `frontend/app.js`: re-run the active query with `LIMIT 100000` (`EXPORT_ROW_CAP`), serialize CSV (RFC-4180 escaping, header row) and JSON (array of objects), download via `Blob` + object URL. Verify exported files open cleanly and match the active filters rather than the rendered page.
- [x] 5.2 Implement the truncation notice when the result count hits the cap. Verify with a filter matching >100k models that the notice shows and the file stops at 100k rows.
- [x] 5.3 Add Playwright coverage for the export download event and file contents (CSV header + row count). Verify `npm test` passes.

## 6. Integration checks and docs

- [x] 6.1 Run the full local pipeline end-to-end (fetch → build → serve → `npm test`) and confirm all new UI features work against freshly built Parquet with populated `has_weights`.
- [x] 6.2 Update `README.md` (features list, caveats: `.bin`-only legacy repos may classify as empty; export cap) and `ARCHITECTURE.md` (record schema and Parquet column list gain `has_weights`; blocklist/export notes in the frontend section). Verify docs match shipped behavior.
