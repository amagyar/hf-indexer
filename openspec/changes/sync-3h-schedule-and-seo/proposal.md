# Proposal

## Why

Recent operational changes (3-hour schedule, dotfile-aware deploy, SEO surface)
were implemented directly without spec updates, leaving
`openspec/specs/deployment-pipeline/spec.md` factually wrong (hourly cron,
`frontend/*` copy) and the new crawlable SEO surface unspec'd. Sync the specs
so they remain the source of truth before further SEO or scheduling work.

## What Changes

- Retarget `deployment-pipeline` scheduled execution: hourly
  (`0 * * * *`) → every 3 hours (`0 */3 * * *`), backfill default
  50k → 150k per run.
- Spec the deploy assembly as dotfile-aware (`frontend/.` copy) plus
  `.nojekyll`, and the `update-index` concurrency guard
  (`cancel-in-progress: false`).
- Spec the single-page SEO surface: query-aligned `<title>`/OG/Twitter
  titles, `robots.txt` + `sitemap.xml`, real favicon/OG image, crawlable
  noscript/about/FAQ content, and `FAQPage` JSON-LD.
- Rename the `Hourly incremental pass` scenario wording (behavior unchanged).

## Capabilities

### New Capabilities

None — all changes attach to existing capabilities.

### Modified Capabilities

- `deployment-pipeline`: schedule cadence, deploy assembly contents, and
  concurrency behavior are changing at requirement level.
- `frontend-query-ui`: adding a crawlable SEO surface requirement to the
  existing single-page frontend.

## Impact

- `.github/workflows/update_index.yml` (already changed, uncommitted).
- `frontend/index.html`, `frontend/robots.txt`, `frontend/sitemap.xml`,
  `frontend/icon.svg` (already changed, uncommitted).
- `README.md`, `ARCHITECTURE.md` wording (already changed, uncommitted).
- No data-pipeline behavior change: incremental watermark + backfill cursor
  semantics are untouched.
