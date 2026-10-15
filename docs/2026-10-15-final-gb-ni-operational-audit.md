# Final four-dimension GB / XI data operational audit — 2026-10-15

**Decision: BLOCKED / NOT production ready.** This is a read-only evidence and workflow review, not tariff certification.

## A. Original data and candidate completeness: SOURCE-ONLY PASS

- GB DBT official `v4.0.1627` as-of 2026-10-14: 16,742 active commodity leaves,
  1,123,765 measure rows within the DBT expanded export, 16,376 type 103/1011
  candidates and 366 active commodities with no direct target candidate.
- NI/XI public source original response run [38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207):
  97/97 chapters, 16,728/16,728 original SHA-replayed responses,
  1,082,800 import-measure records, 0 missing, 0 failures, 0 duplicate codes.
- Condition source [38668119886](https://github.com/liao-works/cursor-tax-tools/actions/runs/38668119886):
  17,395 type 103/105 candidates, 16,630 codes, 98 codes without candidates,
  17,118 nonempty and 277 empty candidate expressions, 860 candidate conditions,
  305 exclusion geography relations, 16,970 candidates without exposed
  additional-code relation; 0 nested reference errors. **None are certified NI Other rates.**
- Independent local review: 23/23 source-fingerprint, cardinality, status, scope and
  immutable-release check assertions passed. Real-source coverage is not legal payable duty.

## B. Data acquisition timeliness: FAIL / refresh proof incomplete

- The production `latest-data` release has tariffs.db and metadata.json
  last asset update **2026-10-01T11:13:05Z**, a **14-calendar-day** lag
  from this 2026-10-15 audit date. It has not incorporated the 2026-10-14
  GB/NI source evidence reviewed here.
- GB/XI whole-source snapshots are dated 2026-10-14. Six selected public XI
  commodity responses queried as-of 2026-10-15 [38668603005](https://github.com/liao-works/cursor-tax-tools/actions/runs/38668603005)
  were unchanged, but this is NOT proof that all 16,728 XI responses remain current.
- Eight XI target measures have an effective end on 2026-10-15; further
  candidates end 2026-10-31. Current-day/full-date refresh is required.
- No proven continuously successful daily whole-source refresh and alert SLA.

## C. Acquisition stability: PARTIAL PASS / long-term unproven

- Four workers each completed exactly 4,182 original XI codes with zero errors
  and independently merged/audited successfully. Backoff, resume and SHA replay
  have offline tests. One complete ~4-hour run is not evidence of repeated
  long-term reliability, measured error budgets or real-world disaster recovery.
- GitHub Actions raw evidence retention is 30 days. Signed archive and S3
  Object Lock code/tests exist but no verified real offsite store, download
  pinned version and successful restore receipt was located. Loss after
  artifact expiry is a material preservation risk.
- Managed HMRC OAuth source evidence is not proved; only official public
  XI raw source coverage has been verified.

## D. Update and rollback stability: FAIL

- In production default branch `feat/parallel-scraper`, THREE monthly
  workflows (`scrape-tariff.yml`, `scrape-tariff-parallel.yml`,
  `scrape-tariff-parallel-improved.yml`) share cron `0 4 1 * *`, and
  each can write the `latest-data` release. There is no evidenced
  single designated publishing writer with an exclusive commit gate.
- Both parallel workflows allow `merge_databases.py --allow-partial`.
  The improved version masks final shard timeouts with `exit 0` and
  continues past a failed health check; `publish` is gated on the merge
  job's success rather than explicit fail-closed healthy full coverage.
- Desktop `liao-tools/src-tauri/src/commands/tax/downloader.rs` validates
  SHA-256, size, SQLite integrity, schema and row count, stages download and
  attempts rollback on ordinary rename failure. But DB and metadata are
  promoted in separate file operations; no tested crash recovery after
  interruption between renames has been established. These protections
  do not certify origin/UKIMS or payable GB/NI rates.

## Must-pass gates before any release

1. Establish one exclusive publisher, fail on every timed-out/failed shard,
   require zero missing source coverage and health validation with a hard exit;
   never publish from partial-source candidate proof.
2. Independently prove GB/NI payable duty semantics, resolving or safely
   deferring all GB 366 and XI 98 missing target candidates, XI 277 empty
   expressions, 16,970 unknown additional-code relations, country/UKIMS,
   certificates, conditions and effective-date exclusions.
3. Perform a full current-day XI catalogue + 16,728 response replay and
   verified GB newest DBT pin before marking current.
4. Demonstrate repeated whole-source cycles, injected HTTP 429/5xx, network
   loss/cancel/resume, anti-corruption and genuine offsite archive recovery.
5. Test desktop transactional update + startup rollback under process kill
   between DB and metadata promotion, across Windows and macOS.
6. Pass an independent signed operator review. Keep PR #6 Draft, do not
   overwrite `latest-data`, and do not change production `tariffs.db`.

## Automated audit policy

`src/target_duty/final_release_gate.py` and its failure injection tests
explicitly block promotion for missing/stale/partial source, unverified
payable duties, missing recovery proof or incomplete operator sign-off.
They do not perform a release even for hypothetical all-green test input.

Source PR: https://github.com/liao-works/cursor-tax-tools/pull/6
Desktop updater: https://github.com/liao-works/liao-tools/blob/master/src-tauri/src/commands/tax/downloader.rs
