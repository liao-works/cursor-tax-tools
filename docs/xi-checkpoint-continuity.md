# Phase 14 — Hash-linked NI checkpoint chain (SHADOW ONLY)

This phase adds continuity evidence for managed XI commodity batches. It
does **not** certify a legally payable duty, NI Other, official transaction
atomicity or signed origin provenance.

## Why this stage was necessary

Every XI individual raw JSON response already had a SHA-256 and source replay
checks, but the project did not have a durable explicit receipt connecting
**the previous verified cumulative checkpoint to the new cumulative state**.
A batch count alone cannot prove that old successful responses were retained
unchanged or that an old source failure was legitimately recovered.

New module `src/target_duty/xi_checkpoint_chain.py` has `seal` and `verify`:

- Validates original catalog/manifest bytes for managed XI, and checks the
  report's SHA-256 of the merged checkpoint DB against the actual file.
- Replays every stored original compressed HMRC commodity body and confirms
  corresponding SQLite measure rows and code/status counts.
- For a root checkpoint, records exact file hashes, origin/region/date and
  recorded success, failure and missing-code counts.
- For a later checkpoint, independently replays both previous/current DBs
  and demands all prior requests are retained. Existing successes must
  have identical status, original response SHA, byte count, capture
  timestamp and error marker. Old failures may become replay-verified
  success, but cannot silently disappear or be edited.
- Links the parent continuity JSON SHA-256, parent cumulative SQLite SHA
  and latest full source report SHA into the new review-only receipt.
- Detects modified parent root counts, unsafe production flags, mismatched
  merge report hashes, wrong catalogue date, disappearing codes, and
  altered previously successful requests. A previously emitted receipt
  may be re-sealed idempotently but cannot be silently overwritten with
  different data.

**Important trust boundary:** GitHub Actions review artifacts are not
cryptographically signed source-of-truth attestations. Hash links are
tamper-evident relative to an independently pinned and trusted root. A
malicious party capable of replacing both entire sources and chain files
can still forge unsigned evidence. The producer reports
`chain_authenticated_or_signed=false`, `production_eligible=false`,
`ni_other_verified=false`, and
`atomic_official_snapshot_verified=false` on every successful check.

## Workflow usage

Manual managed workflow `.github/workflows/xi-managed-coverage.yml`:

- Initial run leaves `prior_checkpoint_run_id` empty; it produces
  `xi-merged-shadow.db`, `xi-merged-shadow.json`, and root
  `xi-checkpoint-chain.json` after source replay.
- Every later run retrieves the previous run's THREE matching files,
  alongside the **same exact frozen catalogue** and manifest.
- After independent source drift checking and merging, `seal` rejects
  missing or modified previously successful evidence; `verify` separately
  reconstructs and checks the new chain receipt.
- All resulting artifacts remain audit-only with a 30-day Actions
  retention. A recovery checkpoint created *before* this stage without a
  chain receipt cannot be silently grandfathered in. Restart the review
  chain from an explicitly approved original snapshot if migration is
  needed.

Equivalent manually run CLI, for initial source-only checkpoint:

    python -m src.target_duty.xi_checkpoint_chain seal \
      --catalogue xi-full-catalogue-shadow.db \
      --manifest xi-frozen-manifest.json \
      --checkpoint xi-merged-shadow.db \
      --merge-report xi-merged-shadow.json \
      --output xi-checkpoint-chain.json

For the next batch, add:

    --previous-checkpoint previous/xi-merged-shadow.db \
    --previous-report previous/xi-merged-shadow.json \
    --previous-chain previous/xi-checkpoint-chain.json

To verify the current receipt, replace `seal` with `verify` and keep
the same arguments. An original root receipt must be independently
preserved externally when a future signed production chain is designed.

## Independent verification

`tests/test_xi_checkpoint_chain.py` covers successful recovery, old
success deletion and tampering, original root tampering, failure recovery
and unsafe source-scope claims. Real public XI canary merges five
independently retrieved commodity responses into a partial checkpoint,
then seals/verifies its own source-only root and preserves its proof in
the CI artifact. Public fixture scopes NEVER become a managed-full source
or NI Other rate, even if all public sample items are captured.

Streaming SHA-256 hashing was also added to `xi_merge.py`, preventing
whole cumulative checkpoint and shard files from being loaded into RAM
just to produce their evidence fingerprints.

Separately, `xi_acceptance --strict` now writes its incomplete-source
JSON diagnostics BEFORE exiting nonzero, so failures are inspectable.

## Remaining real data blockers

The managed 97-chapter HMRC crawl requires authorized credentials;
source version atomicity remains unproven by request-level hashing;
durable storage and signing need a separate authorization and design;
NI Other business interpretation, origin rules and payable duty remain
unverified. The newest GB DBT data gate is independent.

Collector Draft PR #6 and desktop documentation Draft PR #2 are kept
unmerged, and production tariffs.db/latest-data are unchanged.
