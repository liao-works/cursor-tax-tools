# Official GB commodity catalogue audit — Phase 1 (shadow only)

**Status:** Research + ingestion proof of concept. Do **not** merge, promote to
the production latest-data release, or use these outputs as verified duty rates.

## Why this is separate from 103/1011

The previous focused importer creates one row per active GB 103/1011 measure.
That selection is NOT a complete goods-nomenclature index. Codes that have
no active matching measure disappear, even if they remain in the official
nomenclature, and NI / anti-dumping / countervailing fields are not populated
by that focused importer.

This stage instead fetches the **independent DBT commodities table** for
the entire source version, without a measure-type or geographical filter.
It preserves every source row in SQLite, including historical dates, all
suffixes, original commodity SID, hierarchy and descriptions.

## Critical: suffix 80 is NOT sufficient proof of declarability

DBT documents that codes whose suffix is not 80 cannot be used for
declarations. It does NOT follow that every suffix-80 node is actually a
declarable leaf. This proof of concept calls active suffix-80 nodes
**candidates**, not verified declarable codes. Phase 2 must validate
against official measures-on-declarable-commodities, hierarchy and
HMRC API; then record proven declaration status separately.

Neither the active suffix-80 candidate count nor total catalogue rows
is a production coverage guarantee.

## Reproducible local commands

Python 3.11+, standard library only:

    python -m unittest discover -s tests -p 'test_catalog.py' -v
    python -m src.target_duty.catalog build --output gb-commodity-catalogue.db
    python -m src.target_duty.legacy --output legacy-tariffs.db
    python -m src.target_duty.build --output focused-gb.db --legacy-db legacy-tariffs.db
    python -m src.target_duty.catalog compare --catalogue gb-commodity-catalogue.db \
      --legacy legacy-tariffs.db --focused focused-gb.db \
      --output gb-catalogue-coverage.json

To replay a captured official CSV:

    python -m src.target_duty.catalog build --csv downloaded.csv \
      --as-of 2026-10-14 --min-active 10000 --output catalog.db

Workflow .github/workflows/gb-catalogue-audit.yml runs on PR changes or
workflow_dispatch. It preserves the exact downloaded CSV, SHA-256, the
separate catalogue SQLite and coverage JSON/CSV as an Actions artifact.
The feature branch is not the default branch: no default-branch schedule
is installed. No write permission or production mutation.

## Report semantics

- legacy_not_focused is the previously observed missing-old-codes population,
  NOT proof that a code is currently valid.
- active_suffix80_missing_focused indicates possible GB measure gaps OR
  a hierarchy/non-declarable candidate. It is NOT a new tariff rate.
- focused_not_active_suffix80 may be source version, as-of or parsing drift.
- A legacy-only code is retained as evidence, not revived as current.
- Missing 103 never implies 0% or deletion.
- Each report has production_eligible=false.

## Phase 2 prerequisites for production

1. Verify declarable commodity codes against official enumerated measures
   and nomenclature hierarchy; distinguish structural from declarable nodes.
2. Preserve each source measure SID, type, geography, additional code,
   conditions, dates and compound expression. No premature flattening.
3. Model GB and XI separately; XI 103 candidates are not necessarily UI Other.
4. Block promotion on unexplained missing valid codes or applicable measures.
5. Establish verified incremental feed, replay checkpoints, and periodic
   complete reconciliation. Confirm Bulk API data scope before depending on it.
6. Sign immutable snapshots, implement desktop rollback and manual accuracy
   acceptance before swapping the production database.

## Sources

- DBT Data API: https://data.api.trade.gov.uk/
- DBT commodity table: /v1/datasets/uk-tariff-2021-01-01/versions/{version}/tables/commodities/data
- HMRC Trade Tariff API V2: https://api.trade-tariff.service.gov.uk/uk/api

Offline fixtures alone do not prove the live official API is available;
inspect the latest real CI artifact before claiming production coverage.
