# Phase 13 — NI cross-batch source-drift guard and checkpoint replay

**Source-only feature; does not calculate or publish NI Other duties.**

## Why the previous implementation needed a stronger gate

The NI catalogue and each shard are pinned to an explicit `as_of` date,
but independently timed HMRC requests need not originate from a single
atomic transaction. The official service can correct or update previously
returned data for that date while a long-running scan is in progress.
Even a complete 97-chapter receipt cannot prove atomic source consistency.

Two caller-level bypasses were also identified and closed:

- Standalone `xi_measure_crawl.collect(..., skip_successes_from=...)`
  previously checked the old checkpoint schema/status but could skip
  an `ok` row whose original response SHA-256 or persisted measures
  were corrupt, unless the separate planner was used first.
- Resuming against its own preexisting `output` database also skipped
  locally successful rows without independently replaying each response.

The collector now always calls `xi_acceptance.evaluate()` before
reusing success statuses from either the prior merged checkpoint or
the existing local checkpoint, **even without the CI planner**.
Malformed source, changed source hash and measure-row edits fail before
any additional HMRC fetch. This does not make a maliciously edited
manifest authentic; see future signed source artifacts.

## xi_drift_guard.py

Before extending a previously frozen, replay-verified checkpoint, choose
at most three deterministic anchors among earlier successful XI codes:
first, middle and last. Refetch official HMRC XI measures for exactly
the original `as_of` and compare canonical JSON source evidence.
The canonical semantic fingerprint ignores JSON object key order and
`included` list order, but preserves the complete commodity relationship
graph, measure types, geography, duty expressions, associated
conditions, exclusions and additional-code objects.

Every anchor retains both old/new raw-response SHA-256 and old/new
canonical semantic SHA-256, plus an explicit outcome:

- `stable`: canonical source evidence matches.
- `source_drift_detected`: official material changed for the same `as_of`.
- `source_fetch_or_parse_error`: an expected anchor could not be retrieved
  and parsed completely.

Any changed or failed anchor makes `resume_source_stability_gate_pass=false`.
The managed CLI's `--require-stable` emits the full JSON review receipt
and then exits nonzero, blocking new source collection. It never overwrites
old response bodies to force a match.

Example:

    python -m src.target_duty.xi_drift_guard \
      --catalogue xi-full-catalogue-shadow.db \
      --manifest xi-frozen-manifest.json \
      --checkpoint previous/xi-merged-shadow.db \
      --as-of 2026-10-14 --count 3 \
      --require-stable --output xi-source-drift-review.json

An operator can inspect unexpected source differences and launch a
NEW managed full-catalogue snapshot; do not silently merge changed
historic data into the old pinned snapshot. If a response fails, retry
the gate only after source availability is restored.

## CI and real canary

- `tests/test_xi_drift_guard.py` covers reordered JSON, changed rates,
  linked conditions, source failures, old response tampering, bad
  dates, and public/managed scope isolation.
- `tests/test_xi_measure_crawl.py` covers tampered standalone prior
  success and tampered local-success resumed checkpoints.
- The 2-chapter official public canary re-fetches two existing
  same-date commodity responses and runs `--require-stable`.
- `xi-managed-coverage.yml` runs the 3-anchor guard immediately before
  collecting a new shard, ONLY when a prior checkpoint exists. Fully
  collected snapshots can still perform read-only final acceptance
  without calling HMRC again.
- Full acceptance outputs preserve `atomic_official_snapshot_verified=false`,
  even when all sampled anchors are stable and all raw requests complete.

**Limitations:** three stable samples can *detect* source drift but cannot
prove that every other commodity or HMRC backend record is unchanged.
Only a published, verifiable atomic source version (or audited bulk delta
sequence with exact snapshot continuity) could support that stronger
claim. The managed 97-chapter real source capture, durable encryption,
NI Other business mapping and downstream signed data release remain
unverified and are not authorized for publication.

Collector PR #6 and consumer review-only PR #2 remain Draft.
