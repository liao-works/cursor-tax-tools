# Actual GB / XI data completeness audit — 2026-10-14

**Result: SOURCE_INCOMPLETE; PRODUCTION_BLOCKED.** This is a real-artifact
audit, not fixture testing. Every check below was independently executed over
unpacked GitHub Actions SQLite databases on 2026-10-14.

## Source evidence (exact Actions runs)

| Source | Run | Scope |
|---|---|---|
| XI 97 chapter public catalogue | [38548308079](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308079) | PUBLIC_FULL_UNTRUSTED, 0 measure responses |
| XI five chapter sample | [38548308092](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308092) | 20 SHA-verified source responses |
| GB newest pinned full candidate ledger | [38548308291](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308291) | v4.0.1627, 16,742 official leaves |
| GB official leaf catalogue and 366 gaps | [38548308080](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308080) | 366 actual gap requests, 0 source errors |
| Old vs proposed GB shadow DB | [38548308123](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308123) | real 16,592-code legacy snapshot |
| GB expanded source replay | [38548308098](https://github.com/liao-works/cursor-tax-tools/actions/runs/38548308098) | reports 1,123,765 expanded measures, not all raw CSV archived |

## Independent checks — 30 passed

- GB official active suffix-80 leaf set independently reconstructed from parent-child hierarchy: **16,742**. Exactly matches all codes in the GB compact source database and its 16,742-row export.
- **16,376** active GB 103/1011 candidates; **366** explicitly unresolved (359 geography 1080-only in target-focused check, 7 missing expanded rows). A further **1,918** candidate codes have complex/ambiguous source expressions. No 0% inferred.
- XI public inventory: **97/97** chapters, **25,847** nomenclature rows, **16,728** active declarable distinct codes, 0 unknown declarability, 0 malformed codes. PUBLIC_ONLY: not managed/full source certification.
- Cross-region codes: all **16,728 XI** active declarable codes appear in the GB active leaf set; the **14 GB-only codes** are chapter 99: `9905000000, 9919000010, 9919000020, 9919000030, 9919000040, 9919000050, 9919000060, 9930240000, 9930270000, 9930990000, 9931240000, 9931270000, 9931990000, 9950000000`.
- XI five-chapter sample: **1,652** active declarables, **20** collected responses, **1,632** missing there, and 15,076 more XI public-catalogue codes outside those five chapters. Independently decompressed, checksum-verified and re-parsed each of the 20 response bodies; all **910** persisted import-measure rows matched their original included JSON:API nodes. **16,708 of 16,728** XI codes have no captured original measure response in the available evidence. Approximate coverage **0.1196%**.
- Old/new desktop reference DBs: old **16,592**, new GB-only **16,376**, overlap **16,080**. Exactly **512** legacy-only and **296** new-only codes. Recomputed from SQLite rate text and status, not from report summary: 14,526 format-equivalent, 375 changed percentages, 535 new compound duty expressions, 468 other complex/missing reviews, 176 ambiguous GB target status.
- Shadow GB internal candidate status: 16,197 `verified`, 179 `conditional` (internal evidence classification, **not** certified payable duty). NI carryover: 15,761 `stale`, 615 `pending`.
- Reviewed available original archive DB SHA-256s against their exact manifests; SQLite integrity, foreign keys, source count and XI raw SHA/replay passed.

All counts refer to the 2026-10-14 as-of evidence. The recorded GB v4.0.1626 → v4.0.1627 comparison reports zero source candidate changes on this date, but the **entire original 1,123,765-row expanded CSV was not present in the downloaded artifacts** and thus has not been independently replayed by this audit.

## Blocking conditions (not optional)

1. No authenticated **managed XI full-catalogue + all 16,728 raw commodity measures** checkpoint artifact was available for independent source acceptance. A successful 97-chapter *public nomenclature* audit is not that artifact.
2. Authenticate official XI per-origin type 103/105, UKIMS, geography/exclusions, certificate/additional-code/compound expressions. The legacy UI 'Other' is **not** equivalent to a single 103 candidate.
3. Resolve GB 366 nonstandard codes and 1,918 complex/ambiguous candidates before calling their rate payable.
4. Run final GB/XI/legacy union matrix against an independently verified managed-full XI checkpoint on the **same as-of date**, then desktop migration and rollback testing.

**Do not merge this Draft PR, change `latest-data`, or replace installed
`tariffs.db`.** No production-eligible XI Other duty was created.

A self-contained independent audit script, its JSON evidence report, chapter
counts and cross-region 14-code CSV were also generated as conversation
attachments for the owner. The original GitHub Actions artifacts remain the
authoritative bytes for these specific checks.

## Follow-up: field-level staleness, commodity-code deltas and upcoming expiries (2026-10-14)

Independent SQLite audit of original latest-data legacy reference and
2026-10-14 DBT v4.0.1627 / XI public full catalogue artifacts:

- Released legacy reference has **16,592** rows. Its `last_updated` field
  records **14,539** rows in **2026-01** and **2,053** in **2026-10**;
  maximum row update time **2026-10-01 11:07:16**. This is source update
  staleness evidence, NOT proof an individual legal tariff expired.
- Production reference missing values: **105 GB rate**, **456 NI rate**,
  **16,592 other_rate** (entire column), and **4 descriptions**. The
  legacy database lacks legal measure-effective-period fields, so expiry
  cannot safely be inferred for each old duty expression.
- Reconcile against ALL active GB official leaves, not just the target
  103/1011 subset: **303** current GB leaf codes absent from legacy;
  **153** legacy codes absent from current GB leaf catalogue. XI public
  current active inventory has **289** codes absent from legacy, and
  **153** legacy codes not found in XI active inventory. Some changes may
  be split/successor codes; missing status is not automatically a scrape bug.
- For all **16,919** stored GB active target/context evidence records in
  the pinned compact DB, effective start/end was parsed using official
  `#NA` as an open-ended marker: **0** records ended before 2026-10-14,
  **0** have future effective starts and **0** malformed dates. This does
  not verify legal validity of the legacy DB or omitted non-target measures.
- **11 GB 103/1011 candidate measures end 2026-10-31**:
  0702001007, 0702001099, 0702009100, 0702009907, 0702009999,
  0805102210, 0805102290, 0805102410, 0805102490, 0805102810,
  0805102890. Recheck on / after 2026-11-01, not indefinite carryover.
- **1,918** GB candidate codes contain complex or ambiguous duty evidence
  which is not a certified payable rate, in addition to **366** missing
  direct GB 103/1011 candidates. Never assign `0%` by default.
- New XI public full raw-measure capture [38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207)
  had 97/97 chapter catalogue completed; all four raw source worker jobs
  were in progress and **no complete merged zero-gap acceptance artifact**
  was present at the time of this follow-up audit. The prior 20-response
  five-chapter sample must not be counted as a new complete source snapshot.

**Decision remains BLOCKED:** all new raw source data are review only and
must not replace production `tariffs.db` or `latest-data`.
