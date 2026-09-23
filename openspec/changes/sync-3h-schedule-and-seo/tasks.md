# Tasks

## 1. Workflow changes (implemented in working tree, verify)

- [x] 1.1 Cron set to `0 */3 * * *` with backfill default/fallback `150000` — verify with `grep -n "cron\|150000" .github/workflows/update_index.yml`
- [x] 1.2 Concurrency guard `group: update-index, cancel-in-progress: false` present — verify same grep shows the block
- [x] 1.3 Deploy assembles via `cp -r frontend/.` plus `touch .nojekyll` — verify in the Deploy step

## 2. SEO surface (implemented in working tree, verify)

- [x] 2.1 Title/OG/Twitter aligned to `Hugging Face Model Indexer - Search Hugging Face Models` — verify with `grep -n "title>" frontend/index.html` (55 chars, still matches e2e `/Hugging Face Model Indexer/`)
- [x] 2.2 `robots.txt`, `sitemap.xml`, `icon.svg` served from site root — verify via local server: all three return 200
- [x] 2.3 Noscript/about/FAQ visible and `FAQPage` JSON-LD matches FAQ text — verify `python3 -c` HTML parse plus eyeball diff of answers

## 3. Docs (implemented in working tree, verify)

- [x] 3.1 No stale `hourly`/`50000` wording in README, ARCHITECTURE, frontend FAQ — verify with `grep -rn -i "hourly" README.md ARCHITECTURE.md frontend/index.html` returning nothing

## 4. Ship and confirm in production

- [ ] 4.1 Commit and push the working tree, then confirm the next `Update HF Model Index` run succeeds and triggers `pages build and deployment`
- [ ] 4.2 Confirm live `robots.txt`, `sitemap.xml`, and `icon.svg` return 200 on the Pages site
- [ ] 4.3 Submit sitemap in Search Console and Request Indexing for the root URL (title changed)
