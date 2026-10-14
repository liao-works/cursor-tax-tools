# GB/XI full-source completeness: operational runbook

**2026-10-14 — Data-first review only.** No desktop \`tariffs.db\` mutation;
no \`latest-data\` release, no merge of collector/consumer Draft PRs.
This procedure aims to prove **raw official source inventory and response
coverage**, not duty payable for any origin/conditions.

## Current verified / unverified evidence

- GB: pinned official DBT \`v4.0.1627\` expanded-measure replay covered
  16,742 active declarable GB leaves, with 16,376 active 103/1011 candidate
  codes. The remaining 366 have explicit non-applicable/unresolved contexts
  (359 only geography 1080 in focused review, 7 chapter-99 leaves with no
  expanded rows). They are not automatically 0% duties.
- NI: official public XI chapter endpoint enumerated 97 chapters, but
  **no entire authenticated managed XI catalogue + all raw commodity
  response checkpoint has been executed and accepted**. A 5-chapter canary
  is NOT a 97-chapter source snapshot.
- The old desktop \`tariffs.db\` is a historical reference, **not** an
  authority that a particular XI \`Other\` rate is correct.

## Authorized full XI capture with durable checkpoint

1. Register for the official Trade Tariff API and obtain authorized managed
   credentials through https://hub.trade-tariff.service.gov.uk/.
   Provide \`HMRC_CLIENT_ID\` and \`HMRC_CLIENT_SECRET\` through an approved
   private runtime environment; never write them to logs, Git or artifacts.
2. Use a durable, private **new directory** outside the installed desktop
   database location (the default public GitHub Actions artifacts expire).
   Have enough space for raw compressed full responses. Use a fixed
   \`--as-of\` date on initial run and never replace the original frozen
   catalogue, \`xi-frozen-manifest.json\`, or checkpoint with a new batch's
   catalogue.
3. Run this **on an authorized machine or runner** (not on a public web
   canary). The code performs rate-limited managed chapter enumeration, a
   frozen SHA-verified catalogue, up to 500 codes per batch, per-code
   durable checkpoints, complete independent replays, first-middle-last
   historical source drift detection, and progress receipts.

    export HMRC_CLIENT_ID="...from secure environment..."
    export HMRC_CLIENT_SECRET="...from secure environment..."
    python -m src.target_duty.xi_full_capture \
      --work-dir /secure/xi-managed-20261014 \
      --as-of 2026-10-14 \
      --batch-size 500 --max-batches 50 \
      --min-interval 2.0 --drift-interval 2.0

4. If the process is interrupted, re-run with the **same** \`--work-dir\`
   and same date (or omit the date on resume). Every cached successful
   response must replay with its original SHA and stored measure rows
   before use. A source error persists until actually retried. A batch
   without new progress stops rather than hammering HMRC indefinitely.
5. Inspect \`xi-full-capture-progress.json\`. The only possible complete
   raw-source outcome is
   \`status=source_coverage_verified_business_pending\`, with
   \`verified=expected\`, \`missing=source_errors=0\`, full managed scope,
   zero unknown declarability, and an independent replay of all raw
   response bytes. It still reports:
   \`production_eligible=false\`, \`ni_other_verified=false\`,
   \`atomic_official_snapshot_verified=false\`.

### Compare actual GB/XI source inventories with desktop baseline

With a SHA-verified official GB compact review DB **and its matching
original .json sidecar** plus the read-only legacy database:

    python -m src.target_duty.gb_xi_coverage_matrix \
      --legacy-db legacy-tariffs.db \
      --gb-review-db gb-newest-compact-review.db \
      --xi-catalogue /secure/xi-managed-20261014/xi-full-catalogue-shadow.db \
      --xi-checkpoint /secure/xi-managed-20261014/xi-merged-shadow.db \
      --output-csv gb-xi-legacy-source-matrix.csv \
      --output-report gb-xi-legacy-source-matrix.json \
      --require-complete

Alternatively supply \`--gb-review-db\` and \`--legacy-db\` to the
full-capture CLI; it will automatically export the same matrix on
completion (or retain an incomplete diagnostic matrix).

The matrix is one row per unioned commodity code and explicitly records
four **distinct scopes**: old desktop membership, GB active official leaf,
GB source 103/1011 candidate status, and NI declarable/source-response
status. It binds original DB SHA-256 fingerprints and reports region date
mismatches. **Differences between the GB and XI commodity catalogues are
not presumed errors**, and absent measures are NOT filled as 0%.

An official XI response with zero import measures is a successfully
retrieved *source response*, not proof of 0% tariff or no other customs
cost. A GB target measure present in a compact ledger is raw evidence,
not a certified applicable rate.

## Strict non-release decision

Production remains BLOCKED even with a fully replayed managed XI source.
A separate business-validation step must certify actual countries of
origin, official filtered 103/105 measures, measure exclusions,
conditions/additional codes, UKIMS status, quotas and compound duties.
GB unresolved 366 codes require explicit interpretation. Then compare
fully accepted country/date-specific rates to installed legacy
\`tariffs.db\`, test migration/rollback and Windows desktop end-to-end
queries. No workflow here is authorized to publish or replace a tariff
snapshot.

Sources:
- https://www.trade-tariff.service.gov.uk/news/stories/uk-trade-tariff-developer-portal
- https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/secure-data-exchange-bulk-download/1.0

The HMRC annual/monthly/daily bulk feed excludes some EU measures and
has **not** been proven equivalent to NI country-specific tariff filters.
Do not silently switch data sources.

## Real full-source launch attempt — 2026-10-14

A bounded four-worker managed acquisition workflow is implemented in
`.github/workflows/xi-full-managed-one-shot.yml`, with a separately
triggered source-only launch marker `ops/xi-full-managed-launch.txt`.
The 97-chapter offline multi-shard/merge/strict-acceptance regression
passed in [foundation CI 38563303577](https://github.com/liao-works/cursor-tax-tools/actions/runs/38563303577).

**Actual attempted run:**
[GitHub Actions 38563372005](https://github.com/liao-works/cursor-tax-tools/actions/runs/38563372005).

**Actual result: BLOCKED BEFORE FIRST API REQUEST.** The `catalogue`
job failed the authorization preflight with the exact diagnostic
"Missing HMRC_CLIENT_ID / HMRC_CLIENT_SECRET. No API requests made".
Both parallel collection and final replay acceptance were skipped.
The real 16,728-code XI raw-measure gap is NOT closed.

To resume legitimately, the repository operator must obtain official
Trade Tariff managed API OAuth client credentials, then configure
**GitHub repository Actions secrets** under
Settings → Secrets and variables → Actions:

- `HMRC_CLIENT_ID` — the OAuth client ID (not a data/API sample token)
- `HMRC_CLIENT_SECRET` — the OAuth client secret

**Never send these values in a chat, issue, commit, workflow input or log.**
The workflow reads them only as GitHub Actions secrets. On the Draft
feature branch, re-trigger by updating
`ops/xi-full-managed-launch.txt` after the secrets are present. If
manually dispatching the workflow, GitHub normally requires it to have
been registered on the default branch; keep production changes out of
the default branch unless explicitly approved.

At four workers with per-worker `--interval=2.0`, expected request
pressure is nominally at most ~2 commodity requests per second across
the four runners. Actual upstream limits/errors are still binding:
all source responses must be checkpointed, replayed and independently
accepted. Raw data collection may fail on quota, source drift or
response parsing; source completeness cannot be assumed from job start.
No workflow may mark NI Other payable duty or publish `latest-data`.
