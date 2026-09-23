# Spec Delta

## ADDED Requirements

### Requirement: Scheduled execution every 3 hours
The workflow SHALL run automatically every 3 hours and support manual triggering.

#### Scenario: Cron schedule
- **WHEN** the workflow is deployed
- **THEN** it SHALL be triggered by `cron: '0 */3 * * *'` and also accept `workflow_dispatch`

#### Scenario: Backfill budget per run
- **WHEN** the workflow runs on schedule
- **THEN** it SHALL fetch up to `150000` models in the backfill/metrics-sweep pass, preserving the pre-change daily sweep rate across the wider interval

### Requirement: Serialized update runs
The workflow SHALL never run two update jobs concurrently; overlapping runs would double-fetch the same backfill cursor and race the `gh-pages` force-push.

#### Scenario: Concurrency queue
- **WHEN** a scheduled or manual run starts while a previous update run is still in flight
- **THEN** the system SHALL queue the new run behind the in-flight one (`concurrency: group: update-index`, `cancel-in-progress: false`) instead of executing concurrently

## MODIFIED Requirements

### Requirement: Force-deploy static assets to gh-pages
The deploy step SHALL NOT commit data to `main`. It SHALL assemble the site in a throwaway temp directory and force-push to `gh-pages`.

#### Scenario: Assemble site in temp directory
- **WHEN** the deploy step runs
- **THEN** the system SHALL create a temporary directory and copy `frontend/.` (including dotfiles), the sharded `models-*.parquet`, the sharded state `models-*.jsonl.gz`, and `backfill_state.json` into it, and touch `.nojekyll` so Pages serves large static assets as-is

#### Scenario: Force-push to gh-pages
- **WHEN** the temp directory is staged
- **THEN** the system SHALL initialize a fresh git repo there, commit, and force-push to the `gh-pages` branch using `${{ secrets.GITHUB_TOKEN }}`

#### Scenario: main branch stays clean
- **WHEN** the deploy completes
- **THEN** the `main` branch SHALL contain no `models-*.jsonl.gz` or `models-*.parquet` data files

## REMOVED Requirements

### Requirement: Hourly scheduled execution
- **Reason**: Cadence relaxed from hourly to every 3 hours to reduce deploy churn and tolerate GitHub's best-effort cron delays; the incremental watermark plus backfill cursor make skipped runs self-healing.
- **Migration**: Replaced by `Scheduled execution every 3 hours` above; no consumer action required.
