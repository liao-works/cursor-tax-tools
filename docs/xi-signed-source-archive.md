# Phase 15 — Portable signed XI source checkpoint archive

**REVIEW-ONLY:** This is not an authenticated official HMRC atomic snapshot, a
legally applicable Northern Ireland Other duty rate, or a production data pack.
Production `tariffs.db`, release, and latest-data remain untouched.

## What is delivered

`src/target_duty/xi_archive.py` builds a portable ZIP containing a FIXED
allowlist of the raw HMRC XI catalogue, exact frozen catalogue manifest,
cumulative replay-verified checkpoint SQLite, original merge JSON, and the
hash-linked checkpoint receipt. On cross-run child archives it additionally
includes the immediately previous database, report and chain receipt.

The archive includes a canonical JSON receipt enumerating every member's
SHA-256 and uncompressed byte length. **Managed-full archives require an
Ed25519 signature** from an independent operator key and a separately
trusted public key for restore. Private keys are NEVER embedded in ZIP
contents and should never be committed to Git or CI logs. Signatures
authenticate the OPERATOR's archive receipt, not the original HMRC backend.

`restore` refuses incorrect signatures, missing/extra archive entries,
duplicate paths, absolute or parent-traversal paths, ZIP symlinks, encrypted
entries, unreasonable decompressed sizes, tampered member bytes and attempts
to replace an existing destination. It restores into a staging directory,
rechecks all original source bodies through independent SQLite replay and
recomputes the checkpoint chain BEFORE atomically moving the recovered
directory into the chosen destination.

Public/fixture canaries are deliberately unsigned, carry
`source_scope=partial_or_fixture`, and are STRICTLY unable to impersonate
managed-full signed archives. Every successful result still reports
`production_eligible=false`, `ni_other_verified=false` and
`atomic_official_snapshot_verified=false`.

## Operator key creation and registration

Create an Ed25519 keypair on a controlled operator machine, outside the
repository. Protect the private key, restrict filesystem permissions,
and keep a separately pinned public key for independent restore.

    umask 077
    openssl genpkey -algorithm Ed25519 -out xi-ed25519-private.pem
    openssl pkey -in xi-ed25519-private.pem -pubout -out xi-ed25519-public.pem

Configure the review-only managed GitHub Actions workflow with:

- Actions SECRET: `XI_ARCHIVE_ED25519_PRIVATE_KEY_PEM` (multiline PEM;
  managed in your organization's secure key store).
- Repository VARIABLE: `XI_ARCHIVE_ED25519_PUBLIC_KEY_PEM` (trusted public
  key PEM, pinned/approved independently).
- Existing managed HMRC secrets `HMRC_CLIENT_ID` and `HMRC_CLIENT_SECRET`.

Manual workflow `xi-managed-coverage.yml` signs after ALL replay,
per-chapter source coverage and checkpoint chain checks. It immediately
restores the new signed archive with the trusted public key and replays
the stored raw source before uploading any archive to Actions artifacts.
The temporary signing PEM is written under `$RUNNER_TEMP` with restrictive
permissions and deleted via shell trap. It is never uploaded.

**No managed signed archive has yet been produced in a live Actions run**;
the GitHub workflow was not dispatched with managed HMRC credentials and
operator signing secrets. This configuration must not be marked working
merely because the code and offline tests pass.

## CLI

Initial managed snapshot (the checkpoint and chain must already exist):

    python -m src.target_duty.xi_archive pack \
      --catalogue xi-full-catalogue-shadow.db \
      --frozen-manifest xi-frozen-manifest.json \
      --checkpoint xi-merged-shadow.db \
      --merge-report xi-merged-shadow.json \
      --chain xi-checkpoint-chain.json \
      --signing-private-key /secure/xi-ed25519-private.pem \
      --output xi-managed-signed-checkpoint.zip

For a child archive, add ALL of `--previous-checkpoint`,
`--previous-report`, and `--previous-chain` pointing to the immediately
preceding replay-verified cumulative checkpoint files.

Recover into a NEW EMPTY target path:

    python -m src.target_duty.xi_archive restore \
      --archive xi-managed-signed-checkpoint.zip \
      --trusted-public-key /trusted/xi-ed25519-public.pem \
      --destination ./restored-xi-source

Never use `--public-canary` for a managed file. An unsigned fixture archive
is only suitable for local QA and cannot be promoted by changing CLI flags.

## Durable storage still requires external configuration

Archives still expire from GitHub Actions based on repository retention
policies (currently configured 30 days). The signed ZIP can be **safely
exported to independent durable storage**, but merely generating the ZIP
does not make storage permanent. A production-quality offsite vault must
provide encrypted storage at rest (e.g. SSE-KMS), object versioning /
retention or immutability policy, restricted IAM access, independent
root/public-key storage, periodic restore drills and alerts when
replication fails. Do not keep the only copy in the Actions run.

Private keys should ideally live in KMS/HSM or an equivalent managed
signing service rather than a long-lived Actions secret. The current
workflow uses a secret PEM for a **controlled manual shadow pilot**,
not a production key-management standard. Key rotation requires
retaining the public keys of historical archives.

CI tests in `tests/test_xi_archive.py` include a temporary Ed25519 keypair,
valid managed root and child archives, wrong/missing keys, changed ZIP
members, traversal entries, public fixture downgrade rejection and
restored raw SQLite parity. The official two-chapter XI public canary
packs and replays an UNSIGNED partial archive; it is not a live managed
signature test.

## Acceptance boundaries

Managed source coverage, atomic source version, entire NI original
country selection and Other semantics, official UKIMS conditions,
GB latest-data freshness, payable duty, producer/consumer release signing
and installed desktop data updates remain separate gates.

Source code and workflow are in Draft collector PR #6; consumer contract
remains in documentation-only Draft PR #2. No merge or product release.
