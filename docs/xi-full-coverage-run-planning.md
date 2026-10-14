# Phase 11 — NI whole-source next-batch planning and completeness hardening

**Status:** all tools in this phase produce shadow evidence only.
`production_eligible=false`, `ni_other_verified=false`. The collector's
Draft PR must not be merged, and no installed desktop tariff database or
`latest-data` is changed.

## What was missing

A fixed numeric XI `start_index` allowed a manual operator to jump ahead
and leave gaps, and a prior successful code was skipped without the task
planner independently replaying every stored official response. The
separate final audit would detect some gaps, but would not automatically
direct operators back to the FIRST missing or failed commodity.

A second completeness risk existed in XI source parsing: invalid
nomenclature validity dates were silently treated as inactive; a chapter
or commodity response advertising more pages could be treated as a
complete record set. Either condition could under-count the true XI
catalogue. This phase now rejects the malformed/truncated source.

## New module: xi_run_plan.py

Given the exact original XI frozen catalogue and its SHA-256 manifest:

- Requires the independently managed-full listing, at least 90 chapters,
  matching as-of date and verified frozen SQLite bytes.
- Optionally reads a previous merged checkpoint and invokes independent
  `xi_acceptance.evaluate()` to replay every successful compressed source
  response and compare each measure with the stored SQLite representation.
- Calculates success, failed and missing code counts against the actual,
  sorted official XI active/declarable commodity catalogue.
- Picks the earliest pending index for the next bounded batch. Failed
  requests remain pending, so recovery does not skip them.
- Supports an explicit index override for controlled diagnostics, but
  **rejects any selected window with zero pending codes**.
- Emits one small JSON plan including original catalogue/checkpoint
  SHA-256, next start/end, candidates in the window and outstanding
  counts. Never marks NI rates payable.
- Has a separately gated `--public-canary` path for up to five
  partial fixture/public chapters, which cannot claim full XI coverage.

Example:

    python -m src.target_duty.xi_run_plan \
      --catalogue xi-full-catalogue-shadow.db \
      --manifest xi-frozen-manifest.json \
      --checkpoint previous/xi-merged-shadow.db \
      --batch-size 100 \
      --output xi-shard-plan.json

The emitted `next_shard.start_index` and `next_shard.limit` feed the
existing bounded managed XI crawler. If `next_shard=null`, all available
source responses have been collected, but this remains a SOURCE-only
milestone requiring full independent acceptance and country-/condition-
specific payable-duty validation.

## Managed Actions integration

`.github/workflows/xi-managed-coverage.yml` now defaults
`start_index=auto`. Every run downloads/verifies the original frozen
catalogue and optional previous merged checkpoint, replays all successes,
then determines the actual next window BEFORE asking HMRC for any further
commodity records. Explicit numeric override is still available.

Existing operator inputs `catalogue_run_id`, `prior_checkpoint_run_id`
and `sample_limit=10/40/100/500` remain. This is still an optional
manual workflow requiring authorized HMRC credentials and subject to
GitHub Actions artifact retention (30 days), not a durable automated
full crawl.

## Fail-closed chapter, validity and measures parsing

- `xi_catalogue.records()` now rejects missing/malformed validity start,
  end-before-start and otherwise invalid dates; it cannot silently omit
  a possibly active XI goods nomenclature from full coverage.
- `xi_catalogue.chapters()/records()` reject official JSON:API
  `links.next` or nonempty `meta.pagination.next`, instead of silently
  ignoring uncollected pages.
- `xi_measure_crawl.parse()` rejects a paginated commodity response or
  an `import_measures.meta.total_count` inconsistent with the explicit
  import measure relationships.
- `xi_acceptance.evaluate()` rejects unknown statuses and failed
  requests carrying measure rows, so a corrupted checkpoint cannot
  misrepresent source completeness.

The current official 2-chapter public sample (2026-10-14) was still
accepted after these checks. It held 863 nomenclature rows, 516 active
declarable XI codes, and only 5 fully replay-verified commodity responses.
The new plan correctly outputs next start index 5 and 511 missing codes
within the partial sample. This is **not** a managed full 97-chapter crawl.

## Acceptance and operations remaining

1. Add managed HMRC client credentials via secure repository secrets or
   authorized execution environment and run a **single complete** official
   XI chapter enumeration. Do not put credentials in commits or logs.
2. Preserve original frozen catalogue and checkpoint bytes throughout
   every bounded batch; do not reconstruct a new SQLite catalogue and
   assume the same indexes or hash.
3. Monitor missing, failed and success counts per batch and accept source
   coverage only when `xi_acceptance --strict` passes for the managed
   full catalogue, with zero pending code responses.
4. Source completeness is NOT proof of atomic HMRC snapshot, NI
   `Other` UI semantics, UKIMS conditions, quotas or applicable duty.
5. GB newest DBT source remains independently gated while the registered
   latest version has missing tables. Do not substitute GB data for XI.

See `docs/xi-frozen-managed-runbook.md` and
`docs/xi-other-acceptance.md` for prerequisites and consumer limits.
