## ADDED Requirements

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
- **THEN** the system SHALL create a temporary directory and copy `frontend/*`, `models.parquet`, and `models.jsonl.gz` into it

#### Scenario: Force-push to gh-pages
- **WHEN** the temp directory is staged
- **THEN** the system SHALL initialize a fresh git repo there, commit, and force-push to the `gh-pages` branch using `${{ secrets.GITHUB_TOKEN }}`

#### Scenario: main branch stays clean
- **WHEN** the deploy completes
- **THEN** the `main` branch SHALL contain no `models.jsonl.gz` or `models.parquet` data files

### Requirement: Permissions for Pages deployment
The workflow SHALL have sufficient permissions to push to `gh-pages` using the built-in `GITHUB_TOKEN`.

#### Scenario: Token permission
- **WHEN** the workflow runs
- **THEN** it SHALL have `contents: write` permission to enable the force-push
