# Phase 17 — NI SOURCE FIRST: remove archive blocker, broaden actual official catalogue

**This phase targets source completeness and queryable evidence, not archival
infrastructure or automatic payable rate publication.** Both PRs remain Draft.

## Confirmed real XI publicly available source

2026-10-14 partial source evidence obtained directly from the official XI API:

- The official XI `/chapters` endpoint enumerated **97** chapters.
- A 5-chapter public-only CI run independently imported **2,590**
  original nomenclature records and **1,652** active declarable commodity
  codes. Official bounded commodity requests returned **20** valid
  original measure responses and zero HTTP/source failures.
- The same partial inventory has **1,632 uncollected commodity responses**.
  Neither the 5-chapter sample nor 20 responses represent all NI.
- Original source SHA and requests are recorded in SQLite and
  independently replayed before use.

Live evidence: https://github.com/liao-works/cursor-tax-tools/actions/runs/38543310652

### The workflow data-first fix

`.github/workflows/xi-managed-coverage.yml` previously required HMRC
credentials AND signing key AND AWS OIDC role/S3 bucket just to complete
a managed source batch. This prevented source collection from being
operationally usable without infrastructure unrelated to acquiring data.

The workflow now has `archive_mode`: **none** (default), **signed**,
**offsite**. The default runs the managed HMRC enumerator, frozen
catalogue verifier, gap-aware fetch, source-response replay, all-chapter
coverage receipt and continuity seal. Signed and S3 archive steps run
only if explicitly selected. Source errors still fail the final source
gate; source artifacts upload even when a later optional step fails.

**Managed full source still requires official HMRC credentials**
`HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET`; this capability was not
executed for the whole NI catalogue without the required secrets.
The original exact catalogue/checkpoint artifacts must be retained
between batches, and no unattended release follows source completion.

To run the managed workflow after authorized registration, on the
collector feature branch, provide:

- Initial run: empty `catalogue_run_id` and `prior_checkpoint_run_id`,
  `start_index=auto`, `sample_limit=500`, `archive_mode=none`.
- Next run: use the initial run ID as `catalogue_run_id`, and last
  fully replay-verified run ID as `prior_checkpoint_run_id`; keep
  `start_index=auto`. Always reuse the EXACT originally frozen catalogue.
- Continue until the independently replayed managed-full coverage
  receipt reports no missing or source errors. `production_eligible=false`
  and NI `Other` applicability unverified, even then.

GitHub's `workflow_dispatch` requires the workflow to be registered
on the default branch before it is available in the Actions UI; do
**not** merge this tariff implementation merely to enable a button.
An authorized operator can run the same source CLIs directly in a
controlled runner or separately register a benign dispatcher subject
to normal repository review.

### Full public XI *catalogue* audit distinct from managed source

`xi_catalogue --public-full-audit` can enumerate all chapters of the
read-only public XI catalogue at a rate-limited 2.0 seconds between
requests. This creates SQLite labelled **public_full_untrusted**.
That label intentionally FAILS the managed/frozen source admission gate.
`--public-canary --max-chapters 5` retains the smaller probe behavior;
these two modes cannot be combined, nor can full-public audit skip
chapters. Full public catalogue availability or success is **not**
proof of HMRC managed total-duty measures or atomic data consistency.

CI: `.github/workflows/xi-public-full-catalogue-audit.yml` independently
checks the 97 official chapters, complete original nomenclature rows,
active declarable count, unknown declarability and per-file source
provenance. The job will not send thousands of per-code measure calls;
it is an intentionally rate-limited inventory-only audit.

### Lookup of already retrieved NI measures

New `src/target_duty/xi_source_lookup.py` returns up to 1,000 verified
10-digit commodity codes from a checkpoint. It replays the complete
original source responses, preserves 103/105 candidates, linked duty
bases, compound expressions, relevant conditions and additional codes.
Every result explicitly distinguishes:

- `source_not_collected`: code has no captured source response;
- `source_error`: recorded source failure;
- `source_response_no_import_measures`: HMRC returned an empty measure
  list, **not 0% customs duty**;
- `unfiltered_raw_source_candidates`: raw imported source evidence,
  requiring country-specific official filtering before any payable duty;
- `not_in_enumerated_active_declarable_xi_catalogue`: code outside
  original XI inventory/scope.

Examples:

    python -m src.target_duty.xi_source_lookup \
      --catalogue xi-five-chapter-catalogue.db \
      --checkpoint xi-five-chapter-measures.db \
      --min-chapters 1 --code 0101210000 \
      --output xi-review-lookup.json

    python -m src.target_duty.xi_source_lookup \
      --catalogue xi-full-catalogue-shadow.db \
      --checkpoint xi-merged-shadow.db \
      --codes-file selected-10-digit-codes.txt \
      --output xi-managed-review-lookup.json

The metadata and every commodity keep `payable_rate=null`,
`origin_specific_applicability_verified=false`,
`production_eligible=false`, and `ni_other_verified=false`. Exact
origin and additional conditions still need a separate business gate.

### Official bulk-source alternative to evaluate separately

HMRC's 2026-08-06 Bulk Data File List API documents `TARIFF-ANNUAL`,
`TARIFF-MONTHLY` and `TARIFF-DAILY` (annual Taric3/national measures
plus monthly/daily deltas):
https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/secure-data-exchange-bulk-download/1.0

It may reduce tens of thousands of individual XI commodity HTTP calls
once the bulk feed is authorized. **But** it explicitly excludes
certain EU measures, and it is not yet proven equivalent to the XI
public/managed API's exact origin-filtered 103/105 scope. Do not replace
the verified collector with it until schema, country/origin coverage,
delete/retention semantics and parity are independently established.

### Remaining blockers before deployable NI/GB duty rates

1. Authorized managed HMRC **full** 97-chapter inventory and every
   active-declarable commodity response, with no source failures;
2. Per-country XI 103/105, geographic 1011, conditional and UKIMS
   treatment and the **desktop legacy Other** business mapping;
3. GB compound/country additional code tax applicability; source CSV
   already covers official GB v4.0.1627 but payable rate not certified;
4. Compare proposed fully accepted dataset with installed desktop
   tariff DB and add production migration/rollback tests;
5. No merge/publish until the above all pass.
