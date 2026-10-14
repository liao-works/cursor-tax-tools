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
