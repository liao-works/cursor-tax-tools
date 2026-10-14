# Phase 2 — GB all-measure gap audit (shadow only)

Status: review evidence, NOT a new production data source. No release,
default-branch change, automated rate repair or desktop write is permitted.

## Scope and invariant

The pinned GB official commodities-report leaf set is compared with the same
pinned version's GB 103/1011 filtered source. On 2026-10-14, version v4.0.1626
exposed 16,742 active suffix-80 leaves and 16,376 focused-rate commodities:
366 leaf codes had no active focused third-country record.

These 366 are catalogue-coverage gaps, not automatically 366 tariff errors.
The implementation individually queries DBT
measures-on-declarable-commodities by commodity__code with NO measure type
or geography restrictions; query-simple and selected official columns avoid
downloading the whole UK tariff. Raw response SHA-256 and every returned
measure row are retained in the audit-only SQLite.

Every result is classified separately:

| Status | Interpretation |
| --- | --- |
| active_103_1011_conflicts_with_focused | Severe: same-version filtered view disagrees |
| active_103_other_geography | A type-103 measure exists for another geography |
| active_other_measure_types | There are currently effective non-103 measures |
| historical_measures_only | Measures exist, none effective at as_of |
| unresolved_effective_date | At least one measure cannot be dated reliably |
| no_measures_in_official_export | Valid empty response, not an invented 0% |
| source_error | A request/schema/CSV error occurred, MUST remain unresolved |

Combinations of categories can exist within an individual commodity.
The per-code CSV gives the headline reason; the SQLite retains every individual
measure for further comparison, including conditions and compound expressions.
Do not infer NI Other, 0% duty or a payable rate from any classification.

## Running a pinned replay

    python -m unittest discover -s tests -p 'test_gap_measures.py' -v
    python -m src.target_duty.gap_measures \
      --catalogue gb-commodity-catalogue.db \
      --focused focused-gb.db \
      --version v4.0.1626 --as-of 2026-10-14 \
      --workers 3 --limit 1000 --output gb-gap-measures.json

Outputs are gb-gap-measures.json, gb-gap-measures.csv and
gb-gap-measures.db. Source errors and truncated limits make complete=false and
cause the CLI exit status to fail. GitHub Actions archives the evidence even
when the audit is incomplete. The 1000-code maximum prevents an unbounded
request surge; the validated worker count is 1-4. Data from a partial run
must never be used to change the production database.

## GB and XI responsibilities

The current measure audit reads only GB/DBT data. It must not populate XI
and does not validate the NI UI label Other. HMRC XI managed API calls and
business-specific XI selection rules are a separate required acceptance gate.
Future canonical measure staging must distinguish source region, source
version, measure SID, geographical coverage, validity and conditions. Compound
duty expressions must remain intact, with no guessing.

## Incremental source feasibility (not implemented here)

HMRC Bulk Data File List (beta v1.0, updated 2026-08-06) advertises
TARIFF-ANNUAL, TARIFF-MONTHLY and TARIFF-DAILY. These refer to Taric3 and
national measures, with specified EU-measure exclusions; they MUST NOT be
assumed identical in scope or semantics to the DBT GB table and managed XI API.
The documented bootstrap pattern is annual + elapsed monthly + current month
daily deltas, then daily deltas. No production ingestion is safe until API
credentials, sample files, deletion semantics, ordering/checkpoints and full
rebuild parity are verified. Beta source does not itself establish a 1-hour
end-to-end freshness SLA.

Official reference:
https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/secure-data-exchange-bulk-download/1.0

## Acceptance gates before production

1. Complete 100% of the gap-code queries with no unclassified errors.
2. Independently resolve every same-version active 103/1011 contradiction.
3. Preserve non-103 measures, compound duties and conditions without loss.
4. Validate all catalogue leaf classifications against a second official source.
5. Prove XI Other semantics independently, not by copying GB 103.
6. Establish authenticated change-feed checkpoints and full snapshot parity.
7. Test signed immutable release and reversible desktop installation.

Production latest-data, both draft PRs and the desktop installed tariff database
remain unchanged by this workflow.
