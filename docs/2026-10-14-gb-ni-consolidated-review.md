# GB / NI consolidated source-only review snapshot — 2026-10-14

**Assembled and independently checked; NOT READY FOR PRODUCTION.**

This is an inventory and official source *candidate* evidence package, **not**
a certified payable-duty database. It has one logical row per code over the
GB / XI public / legacy code union, plus source archive hashes, chapter
reconciliation, error and expiry ledgers. It intentionally does not override
the installed `tariffs.db` or `latest-data`.

## Actual evidence used

| Source | GitHub Actions run | As-of and status |
|---|---|---|
| GB pinned newest DBT v4.0.1627 compact official source evidence | [38575430393](https://github.com/liao-works/cursor-tax-tools/actions/runs/38575430393) | 2026-10-14, 16,742 active leaves; 16,376 GB 103/1011 *candidate* codes |
| GB full official commodity catalogue and 366 source-context requests | [38575430277](https://github.com/liao-works/cursor-tax-tools/actions/runs/38575430277) | 2026-10-14, 366 reviewed, 0 source fetch errors |
| Old desktop + candidate shadow rate diff | [38575430250](https://github.com/liao-works/cursor-tax-tools/actions/runs/38575430250) | 16,592 legacy codes, GB-only candidate shadow NOT production |
| XI complete PUBLIC goods nomenclature | [38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207) | 2026-10-14, 97/97 chapters, 16,728 active declarables; original catalogue SHA `f87f5c1e82b866fe8d47646257061ec2061cf7d291a23bcd065173f1ef67df1b` |
| XI separate five-chapter sample original raw measures | [38548308092](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308092) | 20 replay-verified responses, 910 sample import measures; **NOT** full XI source |

The GB pinned compact source sha is
`ea767486452a92eb8507e2384df244e66c10336e4f7cf2a26d12710a537f9d00`.
The complete DBT expanded 1,123,765-row original CSV is not itself included
in the downloaded compact proof artifact; preserve the limitation in any use.

## Independent, read-only assembly

The owner-facing downloadable bundle produced on 2026-10-14 includes:

- `gb-ni-integrated-review.db`: queryable SQLite with `commodity_coverage`,
  `gb_candidate_evidence`, `gb_target_gap_context`, `ni_sample_evidence`,
  `chapter_coverage`, `expiring_gb_candidates`, `metadata`.
- `all-commodity-coverage.csv`: **16,895 distinct codes**, GB / NI / legacy
  inventory flags, the GB type 103/1011 source expressions and conditions,
  *uncertified* legacy rates kept only as historical references.
- `NI-source-response-gaps.csv`: all 16,728 XI codes awaiting replayed
  **full-source** raw response acceptance; separate 20-code sample flagged
  without being silently promoted to a complete response.
- `GB-no-direct-103-1011.csv`: all 366 source-target candidate gaps, never
  assumed zero-rate.
- `legacy-code-mapping-required.csv`: 153 old codes outside the current
  active GB / XI listing. They require correct succession/split mapping.
- `GB-expiring-next-30d.csv`: 11 official GB candidate source measures
  ending 2026-10-31 as checked against the 2026-10-14 as-of snapshot.
- `chapter-coverage.csv` and `GB-NI-data-overview.xlsx`: operator review.
- `manifest.json`: SHA-256/size of every source and generated file;
  `assemble_review.py` and `verify_bundle.py` reproduce and independently
  check the output.

**Real final package invariants:** 60 SHA, database, row count, source scope
and conservative release-gate checks pass. SQLite source evidence:
16,919 GB candidate/context measure rows and 366 GB gap source records.
Exactly 16,742 GB current active and 16,728 XI public active codes are
accounted for. The 14 GB-only current codes are chapter 99. NI is a subset
of GB current commodity identifiers in this snapshot.

## Missing and unresolved business information

- All 16,728 NI current codes still require independently accepted original
  commodity measures from a **single frozen full-catalogue source**. A
  [four-worker public raw-source crawl](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207)
  was running when this reviewed package was assembled; no final accepted
  merged full-source checkpoint existed. Newly captured shards and results
  must be audited before filling this gap.
- Even with all raw XI responses, the official type 103 / 105, origin,
  geographical exclusions, UKIMS, additional codes, certificates, quantitative
  expressions and legal effective date must be tested before assigning an XI
  'Other' payable rate. A response with no candidate rows is not 0%.
- GB: **366** codes lack direct GB target candidate; **1,918** other candidate
  codes have compound/conditional/ambiguous expressions requiring
  business logic. **11** candidate measures end 2026-10-31.
- The 16,592-code legacy database must not be treated as fresh or source-
  verified. Official active but legacy-absent codes: **303 GB**, **289 XI**;
  old codes absent from current active inventory: **153**.

## Operator acceptance checklist

1. Finish all four full XI original raw response checkpoints.
2. Independently replay every response; compare the exact frozen catalogue
   digest and as-of date; emit 0 missing / 0 failed / no duplicate codes.
3. Recompute per-code GB/NI/legacy union and check future-expiring measures.
4. Verify origin/UKIMS/conditions and non-percentage rules for payable NI
   and GB rates. Mark unsupported expressions as unresolved, not 0%.
5. Compare candidate new desktop DB against installed old DB, including
   renumbered commodities, migration and rollback.
6. Only after real zero-gap and domain acceptance should publishing or
   merging be considered. Until then **PR #6 must remain Draft**.

This file documents an actual, locally assembled read-only evidence bundle,
not a published GitHub release or modification to production.
