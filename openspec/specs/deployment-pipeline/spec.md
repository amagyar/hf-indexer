# Deployment Pipeline

## Purpose

Hourly GitHub Actions workflow that updates the model index and force-deploys the
static site (frontend + data) to the `gh-pages` branch, keeping `main` free of
generated data.

## Requirements

### Requirement: Hourly scheduled execution
The workflow SHALL run automatically every hour and support manual triggering.

#### Scenario: Cron schedule
- **WHEN** the workflow is deployed
- **THEN** it SHALL be triggered by `cron: '0 * * * *'` and also accept `workflow_dispatch`

### Requirement: CI job sequence
The workflow SHALL execute a fixed sequence: checkout `main`, install Python dependencies, run fetch, run Parquet build, then deploy.

#### Scenario: Ordered steps
- **WHEN** the workflow runs
- **THEN** the steps SHALL execute in order: checkout → setup Python 3.10 → `pip install -r scripts/requirements.txt` → `python scripts/fetch_updates.py` → `python scripts/build_parquet.py` → deploy

### Requirement: Force-deploy static assets to gh-pages
The deploy step SHALL NOT commit data to `main`. It SHALL assemble the site in a throwaway temp directory and force-push to `gh-pages`.

#### Scenario: Assemble site in temp directory
- **WHEN** the deploy step runs
- **THEN** the system SHALL create a temporary directory and copy `frontend/*`, the sharded `models-*.parquet`, the sharded state `models-*.jsonl.gz`, and `backfill_state.json` into it

#### Scenario: Force-push to gh-pages
- **WHEN** the temp directory is staged
- **THEN** the system SHALL initialize a fresh git repo there, commit, and force-push to the `gh-pages` branch using `${{ secrets.GITHUB_TOKEN }}`

#### Scenario: main branch stays clean
- **WHEN** the deploy completes
- **THEN** the `main` branch SHALL contain no `models-*.jsonl.gz` or `models-*.parquet` data files

### Requirement: Permissions for Pages deployment
The workflow SHALL have sufficient permissions to push to `gh-pages` using the built-in `GITHUB_TOKEN`.

#### Scenario: Token permission
- **WHEN** the workflow runs
- **THEN** it SHALL have `contents: write` permission to enable the force-push

### Requirement: Per-file size guard
The workflow SHALL fail before deploying if any generated data file would exceed GitHub Pages' per-file size limit.

#### Scenario: Size limit enforcement
- **WHEN** the build produces the sharded `models-*.jsonl.gz` or `models-*.parquet` files
- **THEN** the workflow SHALL measure each file and fail the job (non-zero exit) if any exceeds 100 MB, otherwise proceed to deploy
