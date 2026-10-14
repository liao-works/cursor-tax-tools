# Phase 9 — DBT newest-version readiness and fail-closed GB data refresh

Status on 2026-10-14: newest official version v4.0.1627 exists in DBT's
version catalogue, but is NOT yet independently usable for this importer.
Every candidate output remains review-only; GB/XI production duty values
are not altered or published.

## What was confirmed by real official API calls

The official GET /v1/datasets/uk-tariff-2021-01-01/versions?format=json
successfully returned 1,947 versions and identified **v4.0.1627** as the
highest semantic version. This is newer than the last verified pinned
full-data import **v4.0.1626**.

However the official v4.0.1627 /tables?format=json returned
a valid {tables: []} response — **zero tables**. The same API for the
pinned v4.0.1626 version returned an extensive table list including
commodities-report and measures-on-declarable-commodities. Separately,
the old /versions/latest/tables/commodities-report/data?format=csv alias
returned HTTP 404. The combined evidence points to a newly registered
version whose tables are not yet exposed, NOT proof that the latest
published tariff is the previous pinned version.

Verified run: https://github.com/liao-works/cursor-tax-tools/actions/runs/38524823315

## Implementation

src/target_duty/dbt_version_discovery.py

- Lists ALL official dataset versions; validates numeric SemVer and chooses
  the highest (not list order nor lexicographic sorting).
- Reads the actual newest version's official tables metadata; rejects
  partial/empty versions. It NEVER silently chooses an older version.
- Confirms the two required import tables and fetches official CSVW
  metadata to compare the required commodity/measure columns.
- Supports official CSVW table identification by relative url, not id;
  ignores supplementary ODS/report views. Verified against DBT's actual
  v4.0.1626 CSVW response.
- Always sets production_eligible=false, publish_eligible=false and
  freshness_proven=false for metadata-only discovery. A preflight pass
  only permits subsequent full-source row count/parity checks.
- Explicitly reports the upstream registered-but-no-tables state
  newest_version_registered_without_tables.

src/target_duty/catalog.py and src/target_duty/build.py now call
resolve_requested_version for ONLINE --version latest requests. A latest
request therefore resolves to a concrete pinned version only after
metadata/table/CSVW checks pass. If v4.0.1627 is still empty, it fails
with an actionable source-readiness message and does not fetch v4.0.1626
as the supposed latest. Explicit --version v4.0.1626 remains supported
for reproducible historical/audit-only imports.

## GitHub Actions

.github/workflows/dbt-version-discovery.yml

- Runs offline tests for broken inventories, malformed SemVer, cross-origin
  redirects, missing required columns, CSVW URL/ODS data and 404s.
- Verifies the CSVW parser against v4.0.1626 official source metadata.
- Captures the actual newest version/tables report as an artifact, then
  FAILS the final gate while the newest version is incomplete.
- The red gate is INTENTIONAL when the official source is not ready. Do
  not suppress it or relabel a pinned historical replay as fresh.

.github/workflows/dbt-newest-replay-shadow.yml

- Operator-triggered only; no schedule and no release/write permissions.
- Blocks until the newest official version has compatible tables + columns.
- Downloads independently pinned latest commodity catalogue, focused
  103/1011 baseline and FULL official expanded-measures CSV.
- Builds the full review evidence and compact target-only GB SQLite,
  checks all active leaves and focused candidate parity.
- Rechecks the official newest version at the end to block a build
  that straddled a source version change.
- Archives only audit and compact review artifacts, never latest-data.

Manual workflow_dispatch may not appear for a workflow that exists only
on an unmerged feature branch. Use the feature-branch CLIs in an authorized
environment or have repository maintainers register a separate review-only
default-branch dispatcher; do not merge tariff data changes merely to
enable a button.

## Remaining blockers

1. DBT must publish v4.0.1627's required tables and compatible columns.
2. Replay all v4.0.1627 rows with completeness and 103/1011 parity proof;
   metadata-only checks are NOT enough to certify freshness.
3. Northern Ireland XI still needs an approved managed full crawl and
   precise original business Other semantics; raw 103/105 is not a rate.
4. No signed consumer-ready data pack or production desktop importer is
   authorized by this phase.

Both producer PR #6 and consumer documentation PR #2 remain Draft;
the production tariff DB and published latest-data remain unchanged.
