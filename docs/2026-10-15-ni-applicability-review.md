# NI third-country applicability source-review audit — 2026-10-15

**Run [38665698058](https://github.com/liao-works/cursor-tax-tools/actions/runs/38665698058): SUCCESS; source review only, NOT NI payable duty.**

This audit reuses exactly the 2026-10-14 PUBLIC XI source evidence and
17,395 third-country source candidate expressions from successful
[expression replay 38664629818](https://github.com/liao-works/cursor-tax-tools/actions/runs/38664629818).
No new API capture, release, or desktop database change.

## Actual all-code evidence contract

- 16,728 SHA-pinned original source responses; 1,082,800 import measure rows.
- 17,395 source third-country 103/105 measures, across **16,630**
  codes. **98** codes have no direct 103/105 candidate.
- Both independent source-coverage CSV and candidate-expression CSV hashes
  are compared to their separately produced JSON proof manifests.
- Exact `code, measure_id` uniqueness; per-code candidate totals must equal
  source-level measure relationship counts; date, type, and positive count
  invariants fail closed.
- Every one of the 16,728 output rows has
  `ni_other_payable_rate=""`, `production_eligible=false`,
  `country_of_origin_specified=false`, and
  `ukims_at_risk_and_reliefs_verified=false`.
- The output review table SHA-256 is
  `893106b1eda80e0393aa16645b7bccb19dfbac66dc8a2a68293c273bfc53848c`.
  Re-downloaded GitHub artifact was independently checked.

## Source-only review counts (overlap is expected)

| Review flag / disposition | Codes |
|---|---:|
| One simple generic baseline candidate, still requires country of origin | 13,709 |
| No 103/105 candidate | 98 |
| No generic 103/1011 baseline (includes some above) | 609 |
| Generic 105/1011 competing with baseline | 523 |
| Multiple generic 103/1011 baselines | 208 |
| Specific-origin/territory measures, not a universal Other rate | 302 |
| Certificate / condition relationship | 774 |
| Geographical exclusion relationship | 291 |
| Missing expression for a generic 103/105 candidate | 263 |
| Compound or specific candidate expression | 1,889 |
| Measures ending between source date 2026-10-14 and 2026-10-15 inclusive | 8 |

The raw expression export separately has 277 empty expressions across
the entire 17,395 candidate measure set. That total is distinct from
263 **codes** where a generic 103/105 baseline or competitor has a blank
expression. These are different units, not contradictory figures.

The 8 candidates have `effective_end=2026-10-15`; do **not** claim they
were already expired at the start of October 15. These require effective
date refresh before making claims for October 16 onward.

## Final guardrails

The 2026-10-14 source snapshot is not current-day certification on
October 15. It is a raw official **public**, not managed OAuth-proven
atomic snapshot. The derived expression candidate output does not yet
include the linked `additional_code` identifiers, so its absence in
the CSV must not be interpreted as absence of additional-code restrictions.

Most critically `OTHER` is an origin-country selection sentinel, NOT
a literal country. HMRC requires UK/EU duties, preferential treatment,
country of origin, UKIMS at-risk status, trade remedies, certificates,
and applicable reliefs to be taken into account for NI duty:
https://www.gov.uk/guidance/how-to-make-sure-the-correct-duty-is-applied-to-goods-you-bring-into-northern-ireland-from-countries-outside-of-the-eu-and-uk

**Acceptance:** raw-source candidate coverage and audit artefacts PASS.
**BLOCKED:** verified real-world NI Other payable rates, any automatic
conversion of source percentage to duty, all production releases and
merging Draft PR #6.

## Next implementation target

1. Replay original raw XI responses to derive `additional_code`,
   measure condition documents/actions, geography membership/exclusions,
   and traceable source citations per candidate.
2. Require explicit destination, origin ISO code, import effective date,
   UKIMS status, EU/UK duty applicability, origin preference proof, and
   other relief/trade-remedy context before final payable rate selection.
3. Independently compare calculations against official per-origin
   tariff pages on representative commodities and boundary dates.
4. Produce a new source date snapshot and reconcile changed measures.
5. Preserve immutable old assets and a rollback plan; do not update
   `latest-data` unless all review gates pass.
