# Phase 10 — GB third-country candidate changes across verified DBT snapshots

**Status: audit-only. No code in this phase authorizes production publication.**

## Purpose

Once DBT exposes complete tables for a version newer than the last validated
`v4.0.1626`, a successful import alone is insufficient. A correct new snapshot
must be compared with the prior independently verified compact GB 103/1011
evidence. Catalogue additions, missing commodities, target-measure removals,
and changes to expressions or conditions are real review tasks.

Implementation: `src/target_duty/gb_review_diff.py`
Tests: `tests/test_gb_review_diff.py`

The command requires **both original compact SQLite files AND their original
matching JSON audit manifests**; it checks each DB SHA-256, SQLite integrity,
foreign-key integrity, region/scope flags, full DBT source provenance,
active commodity counts, and recorded per-code evidence status.
It refuses to interpret geography `1080` as GB geography `1011`.

Comparison is invariant to row ordering and stores no invented payable rates.
It compares the actual active type-103/1011 evidence per common commodity:
measure SID, original duty expression, effective state/start/end, additional
codes, conditions and excluded areas. It separately reports:

- Added and removed active-leaf commodity codes.
- Gain or loss of active type-103/1011 source measures per code.
- Changes to active candidate expressions, dates and conditions.
- Inactive/other-geography evidence changes and unresolved status changes.
- Old/new exact source version, as-of date and compact SHA-256.

**High-risk gate:** removed commodity, lost active target measure, or
changed currently-active candidate causes `high_risk_change_present=true`.
`--fail-on-high-risk` fails the CLI AFTER writing a source-review JSON report.
Even with no detected differences, `manual_business_review_required=true`,
`payable_rate_verified=false`, `ni_other_verified=false` and
`production_eligible=false`. Empty/missing data is NEVER interpreted as 0%.

### Usage

    python -m src.target_duty.gb_review_diff \
      --before baseline/gb-compact-103-review.db \
      --after gb-newest-compact-review.db \
      --output gb-newest-versus-baseline.json --fail-on-high-risk

For a historical-only schema/integrity smoke test, operators can compare
the file to itself with `--self-check`, which is explicitly labeled
`self_check_only=true` and DOES NOT certify a version update.

### CI integration

`.github/workflows/full-gb-xi-foundation.yml` runs the difference and
tamper unit tests. The pinned full GB CI replays the real, previously
verified v4.0.1626 compact against itself as a non-version-acceptance
smoke test; this exercises the entire SQLite/manifest parser against
actual DBT data without claiming an update.

`.github/workflows/dbt-newest-replay-shadow.yml` requires manual
`baseline_run_id` identifying a prior accepted pinned GB Actions run.
It retrieves the original `gb-full-source-proof-RUN_ID` artifact from
the same repository, replays the NEW official latest DBT metadata and
complete source, builds new compact GB review, then invokes this comparison.
Any material loss/changed active candidate halts the QA run and retains
a review JSON artifact (the artifact uploader uses `if: always()`).
Current newest official `v4.0.1627` has 0 tables in DBT's actual source
inventory, so this manual job should be blocked at its first gate.

### Important limitations

- These differences are changes to **official candidate evidence**, not
  a verdict about lawfully payable import duty.
- Different `as_of` dates can produce changes even without upstream data
  modifications. The report explicitly flags this.
- No digital signing: hashes provide local integrity and provenance
  relationships but do not authenticate a maliciously modified manifest.
- GH Actions review artifacts expire; a durable, signed baseline needs
  separate storage/publishing architecture.
- XI requires separately enumerated HMRC data, complete source capture
  and country-/condition-specific `Other` semantics. GB rates do not
  substitute for XI.
- The original production `tariffs.db` and latest-data remain untouched.
