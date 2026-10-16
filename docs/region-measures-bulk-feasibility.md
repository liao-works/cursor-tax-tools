# Phase 3: GB/XI isolated measure evidence and HMRC Bulk feasibility

Status: DRAFT / SHADOW ONLY. No new production tariffs, rate certification,
automatic publish, delta apply, or desktop-DB installation.

## 1. Purpose

Previous GB audit against DBT v4.0.1626 identified 366 commodity leaf
codes without a type-103/geography-1011 rate. A pinned full-measure
comparison (366/366 requests, 0 source failures, 19,220 source rows)
found 359 active type-103 **geography-1080** candidates
(Channel Islands), and 7 with no returned expanded measures.
This does not authorize treating 1080 as 1011, treating no measure as
zero duty, or restoring historical legacy UK rates.

The new module src/target_duty/region_measures.py stores evidence
independently by region, code and original source ordinal. It preserves:

- region GB vs XI in database keys and all queries;
- source version and effective-day provenance;
- source-request status, raw response SHA-256 and exact record count;
- measure SID, geographical area, measure type, original duty expression,
  additional codes, conditions, excluded geographies and original JSON;
- scope as **GB gap-only** or **XI small audit sample**; no pretend 100%
  product-wide coverage and no certified XI Other interpretation.

Proof-of-concept commands:

    python -m unittest discover -s tests -p 'test_region_measures.py' -v
    python -m src.target_duty.region_measures build \
      --gb gb-gap-measures.db --output region-measures-shadow.db
    python -m src.target_duty.region_measures query \
      --database region-measures-shadow.db --region GB --code 0207412010

When an original XI audit JSON is available:

    python -m src.target_duty.region_measures build \
      --gb gb-gap-measures.db --xi xi-audit.json \
      --output region-measures-shadow.db
    python -m src.target_duty.region_measures query \
      --database region-measures-shadow.db --region XI --code 0101210000

Important: xi-review-gate.json is a derived human-review checklist;
**it is not the source XI audit**. The importer rejects it. No stored
XI measure is equivalent to UI Other or final payable duty yet.
Different GB and XI measures for the same code cannot overwrite each other.

## 2. HMRC Bulk File List — feasibility versus implementation

HMRC Developer Hub (beta v1.0, updated 2026-08-06):
https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/secure-data-exchange-bulk-download/1.0

Official descriptions currently expose the information types:

- TARIFF-ANNUAL: annual Taric3/national-measure full extract
- TARIFF-MONTHLY: historical monthly roll-ups of daily deltas
- TARIFF-DAILY: daily Taric3/national-measure delta, subject to retention

**Crucial limitation:** This feed expressly excludes some EU measures
and is NOT proved to be complete GB+XI data, compatible with DBT
GB versions, or sufficient to compute NI Other. A published schedule
is NOT an end-to-end freshness SLA. Do not replay these files on
the production database before obtaining credentials, seeing actual
payloads, proving deletions / replacement semantics and cross-source
identity rules.

The module src/target_duty/hmrc_bulk_feasibility.py ONLY checks offline
period continuity and local-byte hashes of an operator-normalized inventory.
It does not invent the HMRC API response schema, download URLs or delta
operation format, does not fetch files itself and never applies changes.
It cannot certify source authenticity merely from a SHA supplied by
the operator. The normalized inventory MUST have this format:

    {
      "kind": "hmrc-tariff-files-operator-normalized-v1",
      "source_semantics_verified": false,
      "files": [
        {
          "information_type": "TARIFF-ANNUAL",
          "period": "2026-01-01",
          "reference": "opaque-local-file-2026.xml",
          "sha256": "<SHA-256 of actual downloaded bytes>",
          "size_bytes": 12345
        }
      ]
    }

Each entry's "period" identifies an annual Jan 1 anchor, the first day
of the covered month, or the day of a daily delta. All are source
calendar dates, *not* assumed publication timestamps. The "reference"
must be a local safe basename, never a URL/secret/token. For a given
as-of, the strict plan requires an annual anchor, every completed
month's rollup, then every daily file in the current month. A missing
period, corrupted file or unverified local bytes fails continuity.

    python -m unittest discover -s tests -p 'test_hmrc_bulk_feasibility.py' -v
    python -m src.target_duty.hmrc_bulk_feasibility \
      --inventory inventory.json --as-of 2026-10-14 \
      --local-dir ./downloaded --output hmrc-bulk-feasibility.json

Result flags production_eligible=false, source_semantics_verified=false,
region_scope_verified=false, delta_application_implemented=false even
when the local period/hash check passes. This is a safety property.

## 3. Next acceptance gates

1. Implement complete GB all-measures ingestion (not just 366 gaps)
   with bounded downloads and stable source versions; reconcile
   distinct SID/ordinal records and source hash.
2. Obtain managed Trade Tariff XI credentials and verify the real
   XI response across diverse chapters, conditions, geography and
   additional codes; define the UI Other mapping explicitly.
3. Get live HMRC Bulk API credentials, record actual list and file
   schema without leaking signed download links; prove all required
   data types, expiry times and EU/NI coverage with source owners.
4. Design a typed delta parser and deletion/termination semantics
   using real signed samples; require contiguous checkpoints,
   idempotent replay, rollback and daily full-snapshot parity.
5. Build signed immutable candidate release, then stage desktop
   reader changes behind an explicit read-only preview flag.
6. Keep both repository PRs in Draft and production latest-data
   untouched until all gates pass.
