# Phase 16 — OIDC S3 Object Lock vault + disaster restore (SHADOW ONLY)

**Status**: The S3 adapter and fault-injection tests are implemented. **No
real bucket credentials, role assumption, upload, or disaster restore
have been verified yet**. It must not be represented as an active offsite
backup until the operator runs the managed workflow and retains a
successfully audited object/version receipt outside GitHub Actions.

## What was added

- `src/target_duty/xi_s3_vault.py` exposes `store` and `recover`.
- `tests/test_xi_s3_vault.py` simulates AWS S3 responses while using a
  **real locally Ed25519-signed** XI 97-chapter managed-source fixture.
- `.github/workflows/xi-managed-coverage.yml` obtains short-lived AWS
  credentials through GitHub OIDC and the configured IAM role. No AWS
  access/secret key is checked into the repository or added to GitHub.
- This job runs only by manual `workflow_dispatch`, after independent
  raw XI source replay, checkpoint continuity sealing, operator Ed25519
  signing and immediate local signature verification.

### Fail-closed upload contract

1. Require an expected 12-digit AWS bucket owner and full ARN of an
   approved KMS key from that same account; reject bad configuration.
2. Require S3 bucket versioning `Enabled` and Object Lock `Enabled`.
3. Accept a regular locally signed archive up to 5 GiB; larger source
   archives are deliberately blocked until a vetted conditional
   multipart upload implementation exists.
4. Put to the content-addressed key
   `xi-managed-reviews/<first-two-sha-hex>/<whole-sha256>.zip`.
   Use `--if-none-match '*'` to reject existing objects (no overwrites),
   `--checksum-sha256`, `--server-side-encryption aws:kms`, an exact
   KMS ARN, `--object-lock-mode COMPLIANCE`, and an explicit retention
   date. The managed workflow currently requests **365 days**.
5. Require non-null object VersionId returned by S3. Retrieve the exact
   version's `head-object` and verify storage bytes, KMS, object lock
   and retention deadline. An encrypted-only or unlocked object fails.
6. Re-download the EXACT pinned VersionId, verify full local SHA-256,
   then independently run `xi_archive.restore()` with a separately
   trusted operator Ed25519 public key, source-response replay and
   parent-checkpoint continuity. Only then output the vault receipt.

The resulting JSON identifies bucket, owner, key, exact object version,
original archive hash, KMS key ARN, retention and deep restore status;
it explicitly reports `production_eligible=false`,
`ni_other_verified=false`, `atomic_official_snapshot_verified=false`
and `offsite_replication_proven=false`.

### Recovery after GitHub Actions artifacts expire

Save the `xi-s3-version-restore-proof.json` pointer in a **separate
restricted, long-lived, independently backed-up location**, along
with the approved historical operator PUBLIC key. This source receipt
is NOT digitally signed itself; nevertheless any ZIP it points to must
pass the approved archive signature before its contents are trusted.

    python -m src.target_duty.xi_s3_vault recover \
      --receipt xi-s3-version-restore-proof.json \
      --trusted-public-key /trusted/xi-ed25519-public.pem \
      --destination /recovery/xi-snapshot-001

`recover` rechecks bucket versioning and lock, the exact VersionId,
mandatory encryption metadata, the downloaded ZIP SHA-256, the
operator Ed25519 signature, and the entire checkpoint's original
stored HMRC raw responses before placing a recovered directory.
It never replaces an existing recovery destination.

### Manual managed workflow configuration

GitHub repository VARIABLES (not application data):

- `XI_S3_ARCHIVE_ROLE_ARN` — IAM role configured for GitHub OIDC.
- `XI_S3_REGION` — region hosting the vault, e.g. `ap-southeast-1`.
- `XI_S3_ARCHIVE_BUCKET` — reviewed bucket with versioning+Object Lock.
- `XI_S3_BUCKET_OWNER_ID` — expected AWS account ID.
- `XI_S3_KMS_KEY_ARN` — full customer-managed KMS key ARN.
- `XI_ARCHIVE_ED25519_PUBLIC_KEY_PEM` — separately approved public key.

GitHub repository SECRETS (already required by earlier stages):

- `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET`: managed HMRC API.
- `XI_ARCHIVE_ED25519_PRIVATE_KEY_PEM`: operator signing secret.

Restrict IAM role trust to the **exact repository and feature branch**
(or a separately approved protected environment and workflow). Never
trust arbitrary pull-request forks or broad `repo:*` subjects. Keep
write/read S3 permissions restricted to the one key prefix and
this exact bucket owner, with `s3:GetBucketVersioning`,
`s3:GetBucketObjectLockConfiguration`, `s3:PutObject`,
`s3:PutObjectRetention`, `s3:GetObject`, `s3:GetObjectVersion`,
`s3:GetObjectRetention`, and `kms:GenerateDataKey`, `kms:Decrypt`
as narrowly applicable. Do NOT grant `s3:DeleteObject`,
`s3:BypassGovernanceRetention`, general-list or unrelated bucket access.
Test the policy and KMS grants before any real run.

Object Lock COMPLIANCE can prevent early deletion, including by
administrators. Choose retention/cost/region policies consciously;
365 days is a concrete review-pilot default, not infinite retention.
Do not keep the ONLY copy of the receipt pointer in a 30-day Actions
artifact. For stronger disaster resilience configure independent
cross-account/region replication and periodically restore versions;
neither is proved by these tests.

### Tests and independent evidence

The S3 emulator is intentionally in-memory/file-backed, NOT real AWS.
It enforces If-None-Match, source content SHA and required request flags.
Regressions cover valid SSE-KMS + Object Lock + version pin + independent
restore; versioning disabled; lock disabled; changed KMS/head metadata;
expired/short retention; corrupted downloaded bytes; missing/bad
owner/KMS; tampered object pointers; and attempts to reuse an immutable key.

Official public XI canary remains unsigned and is NOT uploaded to S3.
Only the managed signed workflow is authorized to write to this review
vault once correctly configured. It still cannot certify payable XI
`Other`, an official atomic HMRC snapshot or a production desktop rate.

Source: `docs/xi-signed-source-archive.md` and
`docs/xi-checkpoint-continuity.md`.
Both GitHub PRs remain Draft. No production tariff DB or main branch merge.