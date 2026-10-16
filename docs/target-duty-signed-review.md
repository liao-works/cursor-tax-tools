# Signed target-duty review contract (V2)

The unsigned `target-duty-preview.zip` is for local data reconciliation.
The optional **signed review** adds publisher identity verification using Ed25519.
Neither artifact is a production-ready tariff release.

## Exact ZIP wire format

File names in ZIP (exact, no extras): `manifest.json`,
`manifest.sig` (64 raw Ed25519 bytes), `target-duty.db`.

1. Source constructs the usual `shadow-preview` manifest, with official GB
   103/1011 quality counts and NI explicitly `stale`/`pending`.
2. Signing replaces `channel` with `signed-shadow-review` and sets
   `signed=true`, `production_eligible=false`, `signature_alg=ed25519`
   and a non-secret `key_id`.
3. The exact bytes of `manifest.json` (UTF-8 JSON; currently sorted-key compact
   format) are signed. The signature does **not** cover ZIP metadata.
4. The signed manifest contains the expected SQLite size and SHA-256. The
   desktop verifies signature first, then the data digest, SQLite integrity,
   record count and GB/NI statuses. This prevents an attacker from providing
   their own signing key in the untrusted manifest.
5. **No automatic data installation occurs**, regardless of signature validity.

## Provisioning and manual execution

- Generate a unique 32-byte Ed25519 private seed **outside the repository**
  with your approved secret-management process; store its hex encoding as
  Actions secret `TAX_V2_ED25519_PRIVATE_SEED_HEX`. Never commit it.
- Derive its corresponding 32-byte public key and compile it into the
  desktop binary using `TAX_V2_TRUSTED_PUBLIC_KEY_HEX` at build time.
  Secret and public key must be generated as a pair.
- Execute workflow `Signed target duty review (manual, no release)` on the
  development branch with a stable `key_id` such as `liao-review-2026`.
- Download Actions artifact and extract `target-duty-signed-review.zip`.
  The desktop verification UI is available only when its pinned public key
  is built in. Verification is strictly read-only.
- GitHub Actions job has only `contents: read`: no release mutation,
  no merging, and no credentials injected in the desktop runtime.

## Threat model / release blockers

- SHA-256 alone proves integrity against the supplied manifest, **not**
  authenticity. Ed25519 with a pre-trusted public key establishes the signer.
- The CI fixture key (tests only) must **never** be used as a production signer.
- Valid signatures can still cover wrong, stale, conditional or incomplete
  business data. NI `Other` certification, effective-date and conflict gates,
  revocation/rotation handling, monotonic release selection, and recovery
  mechanics are **not yet complete**.
- Production trust must use a separately managed key and explicitly signed
  `channel=production` protocol; this signed-review protocol **rejects**
  `production_eligible=true` and must not be generalized by removing this gate.
