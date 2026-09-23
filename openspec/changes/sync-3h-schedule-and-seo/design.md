# Design

## Context

See `proposal.md` (Why). Current state: the 3-hour cron, 150k limit,
concurrency guard, dotfile-aware deploy, and the SEO surface are already
implemented in the working tree (uncommitted); `openspec/specs/` still
describes the old hourly behavior. Constraints: GitHub cron stays
best-effort regardless of cadence; the fetcher's watermark + cursor resume
is the only durability mechanism; `gh-pages` is rebuilt by force-push every
run, so anything not under `frontend/` is wiped.

## Goals / Non-Goals

- Goals: bring specs in line with the implemented behavior; keep the daily
  sweep rate at the wider interval; prevent overlapping sweeps; give crawlers
  a stable single-URL surface.
- Non-Goals: no fetcher logic changes (cursor/watermark untouched); no new
  indexable URLs or custom domain; no E2E schedule change (stays 6-hourly).

## Decisions

- **3h interval + 150k limit (vs. keep hourly or 6h).** Hourly exceeds
  GitHub's realistic cron reliability and churns `gh-pages` for Google;
  6h would need 300k sweeps (~30min+, heavier 429 exposure). 3h × 150k
  preserves the pre-change daily sweep rate with ~11–15min runs.
  Alternative (3h × 50k, slower sweep) rejected: counters would go stale
  ~3x and the proposal promises rate preservation.
- **Queue, don't cancel (`cancel-in-progress: false`).** A cancelled sweep
  loses its cursor progress and re-fetches; queuing bounds overlap cost to
  one waiting run. Alternative (cancel) rejected for wasted API budget.
- **Dotfile-aware copy + `touch .nojekyll` (vs. checking in `.nojekyll`).**
  Keeps the source tree free of deploy-only artifacts; the workflow owns
  Pages behavior. Matches the existing pattern of assembling everything in
  `TMP_DIR`.
- **SEO via static content, not pre-rendering.** DuckDB-WASM results stay
  client-only; crawlers get noscript/about/FAQ + JSON-LD on the same URL.
  Full pre-render or extra pages rejected per the single-page decision.

## Risks / Trade-offs

- [Burst 429s on 300-call sweeps] → `MAX_RETRIES = 5` backoff already
  exists; runs stretch rather than fail; next run resumes via cursor.
- [GitHub still skips 3-hourly slots] → self-heals via watermark/cursor;
  freshness degrades gracefully, no data loss.
- [`github.io` site-name mislabel persists] → accepted; only a custom
  domain fixes it, explicitly out of scope.
- [Title loses `3M/filter` keywords] → accepted; targets the two real
  queries (`huggingface indexer`, `search huggingface models`); keywords
  remain in description/FAQ.

## Migration Plan

1. Commit working tree (workflow + frontend + docs).
2. Next scheduled run (or `workflow_dispatch`) deploys `robots.txt`,
   `sitemap.xml`, `icon.svg`, `.nojekyll` to `gh-pages` automatically.
3. Submit sitemap in Search Console; Request Indexing (title changed).
4. Rollback: revert cron/limit lines; already-published data files are
   unaffected since state lives in `gh-pages` artifacts.

## Open Questions

None — custom domain and multi-page content are deferred decisions, not
unknowns for this change.
