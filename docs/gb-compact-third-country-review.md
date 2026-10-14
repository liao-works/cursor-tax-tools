# Phase 8 — GB compact, review-only third-country candidates

The GB full DBT expanded-measure feed has been verified at 1,123,765 measure
records across 16,735 distinct commodity codes, and 16,742 official active
leaf codes. Only 16,376 active leaf codes have a currently active type-103,
geography-1011 candidate. Seven Chapter 99 leaf codes have no expanded source
measures. These figures describe official source evidence, not automatically
payable rates.

This phase adds a compact **review-only** SQLite built from the already
validated full official DBT expanded-measures SQLite. The new schema is
intentionally incompatible with the desktop application's production
tariffs.db: there is no rate or north_ireland_rate column.

Module: src/target_duty/gb_compact_review.py
Tests: tests/test_gb_compact_review.py
CI: .github/workflows/full-gb-official-validation.yml

## Contract and source verification

The builder requires:
- Original full DBT SQLite and its matching JSON manifest; verifies full
  SQLite SHA-256 against the pinned manifest before deriving any compact rows.
- Independently pinned commodity catalogue with matching version and as-of.
- Full source integrity, original row counts, 103/1011 focused baseline
  parity, and exact source region/scope/source identifiers.

It exports:
- One status row for each official active GB commodity leaf.
- Every type-103/geography-1011 measure candidate for the leaf, including
  history/future rows with explicit effective_status, conditions, compound
  expressions, additional codes, exclusions and entire original source row.
- Type-103/geography-1080 measures as **non-GB Channel Islands context** to
  help explain otherwise missing GB candidates; these MUST NOT become
  geography-1011 or 0% assumptions.
- Explicit statuses for no active 103/1011 measure, non-GB geography only,
  and source catalogue leaves with no DBT expanded rows.
- Provenance: SHA-256 of the full source, source CSV SHA, version, date,
  all-source row counts, and safety flags.

All outputs carry:
  production_eligible=false
  rate_applicability_verified=false
  ni_other_verified=false

The database is created in the destination directory under a staging path,
validated, and atomically replaced only on success. A maximum size of
256 MiB is enforced by default, rather than publishing 1.5 GiB raw DBT
records to the end user. This limit is adjustable with --max-mb for
review-only artifacts, not an authorization for publication.

Example from pinned official build results:

    python -m src.target_duty.gb_compact_review \
      --full-db gb-full-measures-shadow.db \
      --catalogue gb-commodity-catalogue.db \
      --output gb-compact-103-review.db --max-mb 256

The new CI runs this after full DBT staging, checks source parity,
performs PRAGMA integrity_check and uploads the review SQLite and JSON
within the Actions **review artifact** (retention 14 days). It does not
replace tariffs.db, update latest-data, write a GitHub Release, merge
Draft PRs or edit the desktop frontend.

## Limitations and next acceptance gates

- Full official 103/1011 evidence != applicable GB tariff for every
  origin/product/condition; tariff calculation needs effective dates,
  suspensions, exclusions, additional codes and other applicable measures.
- NI XI is separately sourced; no XI source measure is imported or
  inferred from GB data.
- VAT, quota balances, and payable duty calculation are not covered.
- The entire consumer integration and new signed immutable version
  packager require an explicit *validated* source policy first.
- Managed XI full-chapter and all-code collection, official Other display
  semantics, durable storage and Bulk change replay remain pending.

This is deliberately a staging data optimization, NOT a production launch.
