# Spec Delta

## ADDED Requirements

### Requirement: Crawlable single-page SEO surface
The frontend SHALL expose a crawlable SEO surface on its single URL so search engines index one accurate result without executing the DuckDB-WASM query path.

#### Scenario: Query-aligned titles
- **WHEN** the page head renders
- **THEN** `<title>`, `og:title`, and `twitter:title` SHALL all carry the same query-aligned title (`Hugging Face Model Indexer - Search Hugging Face Models`), and the canonical URL SHALL point at the Pages root

#### Scenario: Crawler metadata files
- **WHEN** the site deploys
- **THEN** `robots.txt` (allow-all with sitemap pointer) and `sitemap.xml` (single canonical URL) SHALL be served from the site root alongside a real favicon/OG image (not a `data:,` placeholder)

#### Scenario: Content without JavaScript
- **WHEN** the page is rendered without JavaScript
- **THEN** the user or crawler SHALL still see a `<noscript>` summary, an About section, and a visible FAQ covering formats, licenses, and catalog freshness, with outbound links to the Hub catalog and the source repository

#### Scenario: Structured data
- **WHEN** the page head renders
- **THEN** it SHALL embed `WebApplication` and `FAQPage` JSON-LD whose answers match the visible FAQ text
