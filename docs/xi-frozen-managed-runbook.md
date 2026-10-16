# XI frozen managed-catalogue runbook (review only)

## Why this exists

An independently re-downloaded XI catalogue can have different SQLite
bytes/timestamps. Shards from separate runs must reuse the **exact original
catalogue file** and frozen SHA-256 manifest. A matched code count is not
sufficient.

The new source module src/target_duty/xi_frozen_catalogue.py supports
freeze, verify and plan. It rejects partial/public chapter listings,
mismatched source dates, invalid commodity codes, changed SQLite bytes
and invalid fixed shard bounds.

The XI crawler supports --skip-successes-from: it validates the previous
merged checkpoint's exact catalogue hash, date and region, skips successful
codes, and retries failed codes. The XI assembler independently validates
*every* source response (even duplicates) and rejects contradictory
successes. --resolve-failures explicitly allows only later, verified
successful responses to replace failed requests.

## Managed manual GitHub Actions

Workflow: .github/workflows/xi-managed-coverage.yml
Secrets: HMRC_CLIENT_ID, HMRC_CLIENT_SECRET (never write them to logs).

Initial run: blank catalogue_run_id and prior_checkpoint_run_id,
start_index=0, sample_limit=40. The job enumerates the official XI
chapters once, writes a frozen catalogue and collects a bounded batch.

Subsequent run: catalogue_run_id=<initial run ID>,
prior_checkpoint_run_id=<last successful checkpoint run ID>,
start_index=40, sample_limit=100. The job reuses exactly the same
catalogue artifact and prior checkpoint; only uncaptured codes in the
new fixed index window are queried. Errors remain explicit and do not
turn into zero tariffs.

Two separate artifacts are uploaded for each successful run:

- xi-managed-catalogue-RUN_ID: frozen original SQLite and SHA manifest
- xi-managed-checkpoint-RUN_ID: shard, cumulative verified SQLite, audit

Artifacts expire after 30 days (or the repository policy's limit):
this supports bounded cross-run recovery, **not permanent storage**.
Longer-term unattended collection requires a separately approved
encrypted durable store with independent integrity checks.

A workflow_dispatch file created only on this unmerged Draft feature
branch may not be registered in GitHub's Actions UI until the workflow
exists on default. Do not merge the tariff branch merely to enable the
button. The equivalent Python CLIs can run in an authorized environment
from the feature branch without publishing.

## CLI example

    python -m src.target_duty.xi_catalogue --as-of 2026-10-14 --output xi-full-catalogue-shadow.db
    python -m src.target_duty.xi_frozen_catalogue freeze --catalogue xi-full-catalogue-shadow.db --manifest xi-frozen-manifest.json
    python -m src.target_duty.xi_frozen_catalogue plan --catalogue xi-full-catalogue-shadow.db --manifest xi-frozen-manifest.json --start-index 0 --limit 100
    python -m src.target_duty.xi_measure_crawl --catalogue xi-full-catalogue-shadow.db --as-of 2026-10-14 --start-index 0 --limit 100 --output xi-current-shard.db
    python -m src.target_duty.xi_merge --catalogue xi-full-catalogue-shadow.db --as-of 2026-10-14 --shards xi-current-shard.db --output xi-merged-shadow.db

For a later shard using *the same exact catalogue bytes*:

    python -m src.target_duty.xi_measure_crawl --catalogue xi-full-catalogue-shadow.db --as-of 2026-10-14 --start-index 100 --limit 100 --skip-successes-from previous/xi-merged-shadow.db --output xi-current-shard.db
    python -m src.target_duty.xi_merge --catalogue xi-full-catalogue-shadow.db --as-of 2026-10-14 --resolve-failures --shards previous/xi-merged-shadow.db xi-current-shard.db --output xi-merged-shadow.db
    python -m src.target_duty.xi_acceptance --catalogue xi-full-catalogue-shadow.db --measures xi-merged-shadow.db --output xi-managed-coverage-gate.json

All outputs remain production_eligible=false and
other_semantics_verified=false. No full managed XI retrieval,
UI Other legal mapping, atomic source version consistency or
production snapshot release is claimed in this phase.
