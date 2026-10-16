# Phase 12 — auditable XI per-chapter source completeness receipt

**Review-only; not a NI 'Other' payable duty.** No current live HMRC
managed 97-chapter scan has been claimed or deployed.

## Verified code changes

- `src/target_duty/xi_coverage_receipt.py`: requires the original
  frozen managed XI catalogue/manifest and cumulative checkpoint.
  Replays every captured official JSON:API commodity response and
  verifies SHA-256 and persisted measure rows using `xi_acceptance`.
  Then reconciles every chapter's active declarable commodities:
  successful responses, source failures, missing responses and
  example pending codes. Includes chapters with zero active commodities.
- Source coverage only passes when managed-full chapter inventory is
  complete, all source records are replay-verified, and zero missing or
  failed codes remain across all chapters.
- Public partial/fixture canary can produce the same transparent chapter
  diagnostics but **can never pass managed full-source coverage**,
  including when all codes inside that tiny canary have been checked.
- Even on a truly complete, simulated managed 97-chapter fixture, the
  receipt retains `production_eligible=false`, `ni_other_verified=false`,
  `payable_duty_verified=false`,
  `atomic_official_snapshot_verified=false`.
- `--strict` writes the chapter evidence report and then exits nonzero
  unless FULL managed-source completion is independently demonstrated.

## CI and manual managed workflow

`tests/test_xi_coverage_receipt.py` simulates all 97 chapters with
legitimate empty import measures (empty != 0% duty), and tests failures,
missing codes, tampered source bytes, changed frozen manifest and the
public-canary isolation policy.

Both foundation offline CI and bounded real XI 2-chapter public canary
run per-chapter coverage validation. The live public canary previously
contained 863 nomenclature records, 516 active declarable codes, and
5 independently replayed source responses; it must show **511 missing**
and never claim all-NI coverage.

The operator-only `.github/workflows/xi-managed-coverage.yml` now
reconciles per-chapter coverage after every batch and stores the source
receipt with original evidence. When the planner determines there are
no pending codes, the workflow has a final-audit-only branch: it uses
the prior replay-verified checkpoint and does not make new HMRC requests.
The independently replayed strict source gate then executes. An ordinary
incomplete or failed batch retains its receipt for the next bounded run.

**Critical remaining gates**

1. Managed HMRC credentials and approval to run bounded source scans
   are still required; these tests do not establish full 97-chapter
   **live** source coverage. Workflow dispatch visibility on feature-only
   files may require separate, approved default-branch registration.
2. GitHub Actions review artifacts are held for a limited time (30 days
   subject to repository policy), not durable archival provenance.
3. XI country-specific `OTHER`, origin filtering, conditions, additional
   codes, UKIMS, quotas, and historical data version atomicity are
   not certified by all-source completion.
4. GB current DBT version and source freshness are a separate blocked
   official-v4.0.1627 readiness gate. None of these source receipts
   allow writing the desktop production tariff database.

Example source-only report:

    python -m src.target_duty.xi_coverage_receipt \
      --catalogue xi-full-catalogue-shadow.db \
      --manifest xi-frozen-manifest.json \
      --checkpoint xi-merged-shadow.db \
      --strict --output xi-chapter-coverage-receipt.json

Producer and consumer PRs remain Draft. No tariff publish or merge.
