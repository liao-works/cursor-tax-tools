# NI Other accuracy and completeness — Phase 5 acceptance gate

**Status:** review-only; \`production_eligible=false\`. Do not update latest-data,
merge Draft PRs, or treat source coverage as a verified payable duty.

## Source-code findings: "OTHER" has two non-equivalent meanings

Official frontend implementation (current implementation reviewed 2026-10-14):

- https://github.com/trade-tariff/trade-tariff-frontend/blob/main/config/grouped_measure_types.yml
  classifies **both measure types 103 and 105** as \`third_country_duties\`.
- https://github.com/trade-tariff/trade-tariff-frontend/blob/main/app/models/duty_calculator/steps/import_details.rb
  uses \`country_of_origin = "OTHER"\` for the Rest-of-World-to-Northern-Ireland
  route, but preserves the **real origin country code** separately in
  \`other_country_of_origin\`.
- https://github.com/trade-tariff/trade-tariff-frontend/blob/main/app/helpers/duty_calculator/commodity_helper.rb
  resolves the selected real code and passes
  \`filter[geographical_area_id]\` (plus Meursing when present) to the XI
  official commodity endpoint.
- https://github.com/trade-tariff/trade-tariff-frontend/blob/main/app/models/measure_collection.rb
  groups measures for overview and handles third-country candidates separately
  from tariff preferences, additional codes and other customs duties.

Therefore **OTHER is not a numeric tariff rate and is not necessarily the
same as 103/1011**. A correct country-specific calculation needs a concrete
origin and may depend on UKIMS status, destination route, goods, date, certificate,
additional code, exclusions, preference eligibility and other conditions.
A 103/1011 candidate in raw data alone MUST NEVER become a certified NI Other.

New safe function:
\`src/target_duty/xi_acceptance.py::interpret_other_origin\`. It resolves
an "OTHER" selector **only after** an actual ISO two-letter country is provided,
then reports \`origin_specific_official_filter_required\` and \`duty_rate=null\`.
It cannot produce a payable rate or lie about geography.

## Independent XI catalogue source and raw measure audit

- \`src/target_duty/xi_catalogue.py\` enumerates official XI chapter IDs
  and stores each item's exact HMRC \`declarable\` flag, start/end dates
  and original JSON. It stores \`listing_scope = managed_full | partial_or_fixture\`
  and the enumeration count to prevent an incomplete canary from satisfying
  the full-source gate.
- \`src/target_duty/xi_measure_crawl.py\` stores raw official commodity
  JSON:API responses, compressed response SHA-256, all related import measure
  nodes and a bounded per-code checkpoint. Missing \`import_measures\` is an
  error, NOT an empty measure list. Transient 429/5xx failures are retried,
  and the managed OAuth token is renewed on 401. Errors never pass silently.
- \`src/target_duty/xi_acceptance.py\` replays every stored XI JSON response
  from its SHA-256 verified bytes, reparses import measures, cross-checks
  ordinal, measure ID, type, geography, compound expression, effective dates
  and original JSON. It fails if any stored evidence is inconsistent or if
  source catalogue/code scope differs. Missing or failed commodity requests
  remain review items.

The acceptance gate requires an **actual managed full chapter listing**,
all active XI declarables to be fetched, and no unknown declarability.
Even after that gate passes it remains evidence-only: the official source
is not known to be one atomic historical snapshot and Other tariff business
semantics are not yet certified.

## Real source evidence as of 2026-10-14

- GB DBT full expanded: 1,123,765 measures across 16,735 codes, all
  16,376 GB type 103/1011 baseline codes reproduced, 7 special Chapter 99
  codes with no expanded measures.
- XI **public canary only**: first 2 chapters, 863 nomenclature rows,
  516 active declarable; 5 XI codes -> 287 raw import measures, 0 request failures.
  The remaining 511 codes in those two chapters and all remaining chapters
  were NOT yet collected by this canary.

Source evidence:
- https://github.com/liao-works/cursor-tax-tools/actions/runs/38506522092
- https://github.com/liao-works/cursor-tax-tools/actions/runs/38506995944

## Managed-only test workflow

A separate, opt-in workflow exists:
\`.github/workflows/xi-managed-coverage.yml\`.
It runs only via \`workflow_dispatch\` and requires GitHub Actions secrets
\`HMRC_CLIENT_ID\` and \`HMRC_CLIENT_SECRET\` issued for the managed
Online Trade Tariff API. If absent, the workflow stops before any data call.

It enumerates the full XI chapter list (rate limited), collects only
10/40/100/500 commodity responses (explicit operator selection),
and emits an evidence DB plus \`xi-managed-coverage-gate.json\`.
**It does not implement a production-scale all-codes checkpoint service**.
For a truly complete all-XI crawl, use persistent checkpoint storage,
request budgeting, measured throughput and snapshot-to-snapshot consistency.
Avoid scraping the public canary endpoint for an unbounded full import.

Standalone gate:

    python -m src.target_duty.xi_acceptance \
      --catalogue xi-full-catalogue-shadow.db \
      --measures xi-managed-measures-shadow.db \
      --min-chapters 90 \
      --output xi-managed-coverage-gate.json

\`--strict\` rejects incomplete collection. A passing review gate never
marks \`production_eligible=true\`.

## Next source acceptance matrix

| Scenario | Expected behavior |
| --- | --- |
| "OTHER" selected without actual origin | blocked: request country |
| A real origin selected | query XI official country-specific measure filter |
| Both 103 and 105, additional code or contradictory effective rates | preserve all, manual review |
| Meursing or complex compound expressions | preserve full expression and unit/currency |
| Quota, suspension, trade defence and country exclusions | keep separate, do not declare payable duty |
| Active Channel Islands 1080 measure in GB | never copy to universal UK or NI default |
| Chapter missing, effective period ambiguous, malformed API response | fail closed, audit status |
| Re-run after 429, 5xx, 401 expiration | bounded retry, checkpoint status |
| Source version/mode changes | never silently overwrite prior accepted snapshot |

No consumer migration is authorized by this phase. Both source and desktop
PRs remain Draft and existing production snapshots remain unchanged.
