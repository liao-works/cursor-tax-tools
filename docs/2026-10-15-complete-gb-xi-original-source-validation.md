# Full GB / Northern Ireland source-data acceptance — 15 October 2026

> **Verdict:** GB commodity and candidate-source inventory PASS, NI 97-chapter
> public commodity inventory PASS, NI full original import-measure raw-source
> coverage **PASS**. **GB and NI payable Third Country/Other duty accuracy is
> NOT certified. PR must remain Draft, production release blocked.**

## Important status change

Earlier source-only bundle and PR summaries said NI full measures were
pending. This is now **superseded** by the actual completed
[NI four-shard public-source run 38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207)
(completed 2026-10-14 17:03:47 UTC).

Four worker artifacts each contain **4,182** original commodity responses.
The completed merge/acceptance job reported **16,728/16,728 replayed
responses, 0 missing, 0 failed, 0 duplicate, 1,082,800 replayed original
import measures**. Published assets stay unchanged.

Independent local validation additionally downloaded the *four original
source shard archives directly*, sidestepping the 842 MB merged-archive
single-file retrieval limit:

- Source `as_of`: **2026-10-14**; validation date **2026-10-15**
- Frozen public catalogue SHA-256:
  `f87f5c1e82b866fe8d47646257061ec2061cf7d291a23bcd065173f1ef67df1b`
- 16,728/16,728 raw compressed responses checked against exact original
  SHA-256; all belong to official XI catalogue, no overlapping codes,
  response code and import-measure relationship count match persistence.
- Original import measures: 1,082,800, none with already-expired or
  future-start effective dates *relative to 2026-10-14*.
- Independently checked exact individual stored measure links and raw JSON for
  a deterministic 336-code sample of **21,488** measure records. The CI
  acceptance separately replayed the full 1,082,800 measure set.
- Original public source completeness is verified; managed OAuth source
  attestation, atomic snapshot and payable NI Other duty **remain false**.
- This source includes both `commodity` and valid `heading` response types
  for active declarable codes. A `heading` response is not a missing record.

## Critical XI duty expression projection bug

The existing normalized `measures.duty_expression` field, read from
`measure.attributes.resolved_duty_expression`, is **empty for all
1,082,800 imported measure rows** in this full run. This is **not** proof
of duty-free treatment. The original HMRC JSON:API responses still contain
linked `duty_expression` resources with duty expressions:

| Source facts from full actual XI raw responses | Count |
|---|---:|
| Type 103 third-country candidate measures | 16,870 |
| Type 105 third-country candidate measures | 525 |
| Total type 103/105 candidates | 17,395 |
| Candidate codes with one or more 103/105 measures | 16,630 |
| XI active codes without 103/105 candidates | **98** |
| Linked original `duty_expression.base` non-empty | **17,118** |
| Linked expression genuinely empty | **277** |
| Candidate measures with condition relationships | **860** |
| Candidate measures with geographical area 1011 | 16,858 |
| Candidate measures with geography RU / BY | 290 / 235 |
| Certified NI Other payable rates | **0** |

Source expression projection MUST be fixed or separately versioned, without
silently changing the meaning of the already-replayed archival checkpoint.
Each candidate also requires country-of-origin/UKIMS/geography exclusion,
certificate, measure-condition, additional-code, compound and legal validity
resolution before a user-facing payable rate can be considered accurate.
Missing 103/105 is not equivalent to 0% duty.

## GB original source and old desktop database

Actual pinned GB DBT version `v4.0.1627`, updated 2026-10-14.
The 2026-10-14 shadow source has **16,742** current active leaves, with
**16,376** direct 103/1011 candidate codes; **366** other current codes
lack direct target candidates (359 1080-context, 7 without target expanded
rows). Among direct candidates **1,918** further current codes need
complex/ambiguous source interpretation. Total unique GB codes needing
review: **2,284**. **11** GB candidate source measures expire
2026-10-31.

NI candidate time window, 2026-10-15 through 2026-11-15: **56** 103/105
source measure records expire; **8 end on 2026-10-15**, **47 on
2026-10-31**, **1 on 2026-11-15**. Re-query effective sources at release
time. The 2026-10-14 data are not a proven timeless legal snapshot.

The independent union across original GB, XI and installed legacy code sets
contains **16,895** unique codes: GB current 16,742; XI current 16,728;
legacy 16,592. 303 current GB codes and 289 current XI codes are absent
from legacy, and 153 old codes are absent from current active sets. There
are 14 GB-only chapter-99 codes. Old production `latest-data` assets had
last been updated 2026-10-01 in checked release metadata.

## New, read-only operator handoff

An independently verified *new* unified **15 MB SQLite source-review
database** and lightweight ~3.4 MB operator ZIP have been attached to the
ChatGPT conversation, including:

- Full 16,895-code GB/XI/legacy current source coverage;
- 16,728 original XI response SHA digests + import measure/103/105 counts;
- 17,395 source-only XI original linked expression candidates (uncertified);
- Review queue and soon-expiring NI source candidates;
- Independent replay receipts, SHA-256 package manifest and verification script.

**The ZIP deliberately excludes the 2+ GB original XI raw SQLite shards.**
Those originals remain GitHub Actions artifacts of run 38567997207 and
should be securely retained before the 30-day artifact expiry. No source
evidence has been marked payable or published to `latest-data`.

## Required before any production approval

1. Fix XI duty-expression extraction using the linked original resources and
   add regression fixtures covering rates, conditions, mixed/compound rules,
   heading responses, and explicit empty/no-target cases.
2. Implement origin/UKIMS/legality-specific payable duty classification, with
   reviewed independent expected values for GB and XI, including the 98
   XI no-103/105 candidates, 277 empty source expressions and 860 conditions.
3. Resolve GB 366 no-direct and 1,918 complex candidates, and legacy code
   split/merge mappings.
4. Check expiring measures against fresh official sources for the release
   date and validate installation, migration and rollback on the desktop app.
5. Preserve PR #6 as **Draft**; **do not merge, publish or overwrite any
   production tariff database** until business-level acceptance is complete.
