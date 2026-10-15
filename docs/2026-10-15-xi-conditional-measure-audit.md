# NI full-source conditions, origin exclusions and legal evidence, 2026-10-15

## Scope and non-production safety

The public XI source was collected on 2026-10-14 in Actions run
https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207
All 16,728 active commodity codes had independently replayed raw JSON:API responses.
That is public-source completeness, NOT HMRC managed OAuth attestation, a single
atomic official snapshot, or proof of NI Other payable duties.

Source-derived expression run 38664629818 retrieved 17,395 measure type 103/105
candidates across 16,630 codes; 17,118 had non-empty expressions, 277 did not.
Never replace an empty expression or a missing measure with a 0% rate.

## Original-linked condition detail extraction

Implementation: src/target_duty/xi_condition_evidence.py
Real full-data Actions workflow: .github/workflows/xi-condition-source-review.yml
Source run: https://github.com/liao-works/cursor-tax-tools/actions/runs/38668119886

Read-only output records source response SHA-256 and measure SHA-256, original
measure dates, 103/105 type, duty expression, geographical-area refs and its
child member IDs, excluded countries, complete measure condition attributes
(including document code, certificate, guidance, action), condition permutation
groups and nested measure-condition references, duty components, legal acts,
and footnotes. Missing optional nested references produce explicit flags.
Additional-code status is not_exposed, source_explicit_none or source_referenced.
In particular a missing relationship must NOT be treated as proof of no
additional code.

All output rows have a blank payable rate and production_eligible=false.
Rows are keyed by source commodity code and original measure ID.
Source rows must be replayed and counted from actual artifacts before any
claims about complete structured evidence. Actions artifacts expire after
30 days, so long-term archival needs a separate approved destination.

## Real examples and legal interpretation

The 2026-10-14 raw source includes measures using document Y155, measure
condition combinations, exceptions for excluded geographical areas and
duty-related legal acts, e.g. source code 0713101000. These may materially
change applicable duties even when the base expression looks simple.

GOV.UK Windsor Framework eligibility and at-risk distinctions:
https://www.gov.uk/guidance/internal-market-movements-from-great-britain-to-northern-ireland

Official measure-component and condition model:
https://uktrade.github.io/tariff-data-manual/documentation/data-structures/measure-conditions.html
https://uktrade.github.io/tariff-data-manual/documentation/data-structures/measure-components.html

An ISO origin is mandatory to interpret country-specific measures. OTHER
is not an ISO origin; a direct source exclusion is NOT proof of zero duty.
UKIMS authorization alone does not prove a goods movement not-at-risk,
and not-at-risk alone does not establish the actual payable rate.

## Release blockers

- Confirm actual real-run derived row counts, hashes and missing nested references.
- Verify certificate action/permutation semantics with real legal source rules.
- Check explicit origin, movement, UKIMS and at-risk states and any reliefs.
- Refresh after 2026-10-14 for subsequent effective-date changes.
- Cross-check multiple actual government tariff pages and legal acts.
- Resolve GB complex duty and legacy-code migrations separately.
- Keep Draft PR #6, do not overwrite latest-data or production tariffs.db.

## Actual final evidence acceptance — confirmed from run 38668119886

The full source-only Actions run succeeded. Separately downloaded and checked
the candidate-details artifact: output 17,395 rows, 17,395 unique
(code, measure_id) keys, 16,630 distinct codes and an exact CSV SHA-256 match.
All 16,728 frozen raw source responses were replayed successfully.

Source reality: 860 candidate measures have conditions, 860 have condition
permutation groups, 305 have exclusion geography, and 0 source references
were unresolved. Additional-code relation is NOT EXPOSED in 16,970 rows,
and 425 rows contain an actual additional-code reference. This is a key
source API visibility limitation, NOT evidence that 16,970 measures lack
additional-code eligibility requirements.

Official-source CSV digest:
6efc3a13a3ab0f1d48d20ff267e13f8a6f02a9f5ca605429422e813225d398e4

All rows retained blank payable NI Other duty, production_eligible=false.

## Real latest-day source drift spotcheck

The independent source drift canary fetches six selected official public XI
commodity responses with as_of=2026-10-15, compares 103/105 candidate
identities/expressions/conditions to the original frozen 2026-10-14 response.
Run: https://github.com/liao-works/cursor-tax-tools/actions/runs/38668603005
Changes to JSON alone are distinguished from actual candidate changes.
Any HTTP or replay error is retained as an explicit failed canary. Even a
successful six-code comparison cannot establish all 16,728 current cases.

Regulatory reference for the EU/UK duty branching:
https://www.gov.uk/guidance/how-to-make-sure-the-correct-duty-is-applied-to-goods-you-bring-into-northern-ireland-from-countries-outside-of-the-eu-and-uk

### Actual live 2026-10-15 date canary acceptance

Run 38668603005 succeeded. Six original frozen public XI source codes
(0101210000, 0101299000, 0207412010, 0713101000, 0713109000,
0805102210) were queried against as_of=2026-10-15 with the public API.
All six fetched without error. All six original response SHA-256 values
matched the 2026-10-14 frozen responses and the 103/105 candidate measure
set, effective dates, duty expressions and condition counts did not drift.
Semantic candidate changes: 0 of 6; this DOES NOT establish currency
for all 16,728 XI commodities.

All 17,395 original NI condition/legislation evidence rows were independently
downloaded and checked for a unique (code, measure_id) key and exact CSV
SHA-256 parity (6efc3a13a3ab0f1d48d20ff267e13f8a6f02a9f5ca605429422e813225d398e4).
Source claims remain non-atomic, public-only, and not payable duty certified.
