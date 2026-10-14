# Phase 4 — complete GB expanded DBT source and independent XI coverage

**Status: validated GB DBT expanded-measure source, XI live chapter canary and raw
capture prototype. Every output is SHADOW ONLY. No production replacement.**

## GB full source: independently verified 2026-10-14

Official immutable dataset version: v4.0.1626

The first real workflow succeeded:
https://github.com/liao-works/cursor-tax-tools/actions/runs/38506522092

| Metric | Verified value |
| --- | ---: |
| Raw DBT expanded measure records staged | 1,123,765 |
| Distinct codes with expanded measures | 16,735 |
| Independent current GB commodity leaves | 16,742 |
| Current GB leaf codes without expanded measures | 7 |
| Existing focused 103/1011 codes reproduced in full export | 16,376 / 16,376 |
| Source version mismatch or parse failure | 0 |

Those seven no-measure codes are 9930240000, 9930270000, 9930990000,
9931240000, 9931270000, 9931990000 and 9950000000. Preserve these as
NO_OFFICIAL_EXPANDED_MEASURE_ROW_UNVERIFIED, not zero duty or expired.

The full source's CSV SHA-256:
914a3c9227a859ccaa7fffb80a32207bb1d66586b6c5a459edb1e68eaa97703b.

Implementation: src/target_duty/gb_full_measures.py

- Pins the full DBT version before downloads, and rejects latest aliases.
- Streams every expanded measure in CSV to isolated canonical GB SQLite.
- Preserves original source fields and all expressions, conditions, dates,
  geographies and additional codes.
- Compares active GB 103/1011 code coverage against the separately downloaded
  same-version focused baseline; fails on missing baseline codes.
- Preserves official active leaves absent from the measure export as unresolved.
- Never swaps production tariffs.db or emits an "applicable GB rate".
- DBT scope does not include other duties such as VAT or quota balance.

Reproduce with pinned catalogue DB + metadata JSON:

    python -m src.target_duty.gb_full_measures \
      --version v4.0.1626 --as-of 2026-10-14 \
      --catalogue gb-commodity-catalogue.db \
      --focused focused-gb.db \
      --output gb-full-measures-shadow.db

## XI: separate, real official nomenclature source

The official V2 API spec advertises:
- GET /xi/api/chapters (enumerate actual chapters)
- GET /xi/api/goods_nomenclatures/chapter/{chapter_id}
  (read all chapter items with independent declarable=true and dates)
- GET /xi/api/commodities/{id}
  (date-specific import measures with linked measure types/geographies)

Specification: https://github.com/trade-tariff/trade-tariff-api-docs/blob/main/source/openapi.yaml

Implemented:
- src/target_duty/xi_catalogue.py
  enumerates XI chapters separately, preserves raw nodes, validity and
  explicit HMRC declarability. Real public canary (first 2 chapters)
  succeeded with 863 classification records, 516 active declarable.
- src/target_duty/xi_measure_crawl.py
  uses that independent XI set as candidate codes, captures raw V2 JSON:API
  responses compressed with SHA-256, stores ALL referenced import measures,
  and saves an incremental checkpoint by code. On incomplete relationships
  or requests, errors remain visible and retryable. It does not choose
  payable duties or pretend type 103/1011 means a verified Other bucket.

A public canary checks five rate responses only. This must not be scaled
to the entire XI dataset without managed HMRC developer credentials and
appropriate request limits.

Example production-*review-only* CLI with managed OAuth:

    HMRC_CLIENT_ID=... HMRC_CLIENT_SECRET=... \
      python -m src.target_duty.xi_catalogue \
        --as-of 2026-10-14 --output xi-catalogue-shadow.db

    HMRC_CLIENT_ID=... HMRC_CLIENT_SECRET=... \
      python -m src.target_duty.xi_measure_crawl \
        --catalogue xi-catalogue-shadow.db --as-of 2026-10-14 \
        --limit 100 --min-interval 2 --output xi-measures-shadow.db

Repeat the second CLI until the checkpoint reports remaining_codes=0,
but only after rate-limit, data-consistency, and XI type/condition
acceptance checks. The feed is not proved to be atomically versioned
across chapter requests; checkpoint date alone cannot prove official
transaction-level snapshot consistency.

## Why NI "Other" is still not certified

There is no verified rule yet equating UI "Other" with type 103/geo 1011
on all XI commodity codes. Origin, UKIMS at-risk status, commodity
additional codes, conditions and EU/UK tariff interaction may change
how the visible or payable tariff is calculated. Without proof of the
UI selection algorithm and official reconciliation samples, the system
must return review_required even when a raw 103/1011 record exists.

## Required next gates

1. Obtain managed HMRC credentials; fetch the FULL XI independently
   enumerated commodity catalogue, then ALL XI import measures.
2. Compare official XI UI Other examples spanning origin, conditional,
   compound, additional-code and special duty cases; derive
   a tested selection policy with explicit unsupported cases.
3. Validate source timestamp/version continuity for XI chapter snapshots;
   support changed codes, deletion and retry without stale promotion.
4. Validate HMRC Bulk real file payload schema, region coverage, deletion/
   replacement semantics and idempotent replay against new complete snapshots.
5. Move only explicitly verified target GB/XI views into signed immutable
   release packages with desktop rollback capability.

**Both repo PRs remain Draft. No production/latest-data workflow modified.**
