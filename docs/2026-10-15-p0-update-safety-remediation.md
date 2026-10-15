# P0 remediation: fail-closed legacy releases and read-only freshness, 2026-10-15

**Scope:** Draft PR #6 only. No production asset, release tag or default-branch
workflow has been updated by these branch commits. This is NOT a live-fix claim.

## Three unsafe production writers: quarantined in review branch

Files: `.github/workflows/scrape-tariff.yml`,
`.github/workflows/scrape-tariff-parallel.yml`,
`.github/workflows/scrape-tariff-parallel-improved.yml`.

All three monthly `0 4 1 * *` cron triggers removed; no release creation/edit
steps, no GitHub contents write permission, manual diagnostic only.
The two parallel flows no longer include `--allow-partial`. The improved
flow reports retry-exhausted HTTP timeout as an explicit nonzero failure,
rather than masking the failure with `exit 0`.
All three now fail on a failed database health check; parallel workflows also
propagate failed/partial source and merge jobs as an unsuccessful review
rather than implying a valid candidate database. Single-source path refuses
scrape reports with nonzero failed items.

Static enforcement: `src/target_duty/legacy_publish_guard.py` reads ALL
three real workflow files. CI rejects returning monthly schedules, write
permission, release actions, `--allow-partial`, masked timeouts, and
masked health/scrape failures. Failure injection tests are in
`tests/test_legacy_publish_guard.py`.

**This deliberately suspends automated publication**, it does not establish
a replacement safe production publisher. A dedicated independent full-source
sign-off and explicit operator-approved single-writer job must be implemented
and proven before automatic releases can resume.

## Read-only current production freshness and consistency

New `src/target_duty/production_freshness_watch.py` and
`.github/workflows/production-tariff-freshness-watch.yml`:

- Schedule daily, default 00:00 UTC (08:00 Singapore) once merged into
  default branch; `contents: read`, no production write or release step.
- Fetch live `latest-data` metadata and SQLite + immutable `data-...` release;
  verify SHA-256 parity, byte lengths, SQLite PRAGMA integrity_check,
  required columns, exact record count, and paired release-asset timestamps.
- Fail if either release asset exceeds 48 hours old, release pair timestamps
  are divergent, binary bytes are not the immutable version or the
  manifest, or the official release is unreachable.
- Store the exact report as an Actions artifact even when the check fails.
  An Actions failed run alone is not proof a person will get an external alert:
  notification routing and on-call acknowledgment remain outstanding.
- Daily check watches the **installed production release**, not source API
  completeness or legally applicable tariffs. It never certifies NI Other.

First real audit-only branch workflow:
https://github.com/liao-works/cursor-tax-tools/actions/runs/38671735400
An old 2026-10-01 production release is expected to fail the 48-hour
freshness threshold; that is a meaningful alert, not a CI publishing bug.

## Remaining release-blocking work

- Have reviewed P0 changes actually merged to default; until then old
  schedulers and release-writing paths are still active.
- Prove full fresh NI source coverage for a new date and legally correct
  GB/NI origin, additional codes, certificates, EU/UK route, end dates.
- Design/review a *single* update job with signed operator-approved
  full-source acceptance and immutable versioned release receipts.
- Test Tauri desktop crash consistency with power/process-loss between
  database and metadata file operations; Windows component tests do not
  establish this guarantee.
- Prove repeated collection and durable offsite restore, configure
  notifications for stale release detection and establish freshness SLA.

Data release intentionally BLOCKED. PR remains Draft.

## Real 2026-10-15 production release check: FAILED FOR STALENESS AS DESIGNED

Source-only read-only workflow [38671735400](https://github.com/liao-works/cursor-tax-tools/actions/runs/38671735400)
actually retrieved `latest-data` JSON+DB and immutable `data-36851279616` DB.
Independent download receipt reported:

- `db_and_immutable_match=true`: latest-data and pinned binary bytes match.
- SQLite `PRAGMA integrity_check=ok`, production records: **16,592**.
- Actual DB SHA-256 `2fbddc432bc77654dd2f57a24c5e9d379c4e2fb653d0c7f648c726b0ddeed92e`.
- Release asset age **327.38 hours** at 2026-10-15T02:36:06Z, exceeding 48-hour limit.
- `fresh=false`, explicit GitHub workflow `failure`; 0 release writes.

CI [38671815094](https://github.com/liao-works/cursor-tax-tools/actions/runs/38671815094)
passed all legacy-publisher quarantine and release SHA/paired-asset
integrity failure-injection tests on the updated source branch.
**These are PR-branch changes only. The default branch still needs merge.**
