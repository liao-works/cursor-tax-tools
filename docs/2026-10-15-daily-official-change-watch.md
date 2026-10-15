# Daily official GB / NI change detection connected to weekly full source — 2026-10-15

## Design and source constraints

Source-only schedule is **daily 00:30 UTC / 08:30 Asia/Singapore** via
`.github/workflows/daily-official-source-watch.yml`; weekly GB and XI full
replays remain **Sunday 01:00/02:00 UTC**. GitHub scheduled workflows
activate only when these changes are merged to the repository default branch.

Daily XI fingerprints are limited samples, **not complete change coverage**.
GB version checks do not by themselves capture future effective-date
changes. Therefore every unscoped upstream change, any candidate effective
end, an API/schema failure or a source baseline older than seven days
produces an explicit review/full-replay signal, never a payable duty update.

## Actual official check and its legal limitations

- First authenticated-origin (official PUBLIC source, not managed OAuth)
  six-code response watch: [Actions #38684846942](https://github.com/liao-works/cursor-tax-tools/actions/runs/38684846942).
- Actual DBT upstream newest version newly **v4.0.1628**, but the official
  tables / CSVW are **not yet exposed** (`newest_version_registered_without_tables`).
  Previous independently pinned GB candidate is `v4.0.1627`. The version
  advanced but cannot be used for a full import until source tables exist.
  The watcher must detect the new version even when the schema is unavailable.
- All six frozen NI public response SHA-256s matched their 2026-10-14
  counterparts when fetched as-of 2026-10-15. Only six samples passed;
  NI 16,728-code whole-source current-day evidence remains unavailable.
- The SHA-pinned Oct-14 full NI candidate evidence records eight 103/105
  candidate measures ending **2026-10-15** and 47 ending **2026-10-31**.
  A clean GB version response cannot suppress these date-boundary reviews.
- Original NI frozen baseline built from independent full-source artifact
  [38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207)
  and signed-off condition evidence [38668119886](https://github.com/liao-works/cursor-tax-tools/actions/runs/38668119886)
  is committed as `ops/xi-daily-canary-baseline-20261014.json` with
  source catalogue SHA, 17,395 candidates and six exact raw response hashes.
  This fallback is reviewed evidence ONLY; it expires for watch purposes
  after seven days (forces full replay request).

## Automatic compact weekly-source baseline selection

After a **successful, complete** NI weekly raw replay, the audit step
`src/target_duty/xi_daily_baseline.py` reads its accepted original merged
SQLite and source manifest; requires zero missing, complete catalogue
parity, SHA-pinned selected responses and correct official as-of date.
It writes `xi-daily-watch-baseline.json` containing original response
fingerprints, known 103/105 expiry dates and immutable merged DB hash.
The weekly workflow archives this compact manifest separately under
`xi-daily-watch-baseline-<run_id>` (in addition to 30-day raw shards).

GB weekly full source exports a separate tiny
`gb-daily-watch-pin-<run_id>` artifact containing the official pinned
version, as-of date and accepted full-source review receipt.

The daily job discovers the **latest successful default-branch** weekly
full-source run containing the appropriate compact artifact and downloads
only that small artifact; missing/unregistered weekly workflows fall
back to independently reviewed 2026-10-14 pins and trigger aging alarms.
**No multi-hundred-megabyte raw-response download is required daily.**

## Guardrails and operations

- Checks official GB metadata and CSVW schema. A newly registered version
  without tables is recorded as both NEW_VERSION and SOURCE_NOT_READY.
- NI public responses are checked against six pinned original hashes;
  changes are raw-byte signals requiring targeted validation/full replay.
- Candidate expiry ledger is checked cumulatively from last frozen source
  so missing one day's scheduled watch cannot erase a legal change.
- Any unavailable API, invalid commodity identity, partial XI canary
  response or out-of-date baseline is review-required; API failures
  fail the GitHub job after archiving evidence.
- `release_permitted=false`, `ni_other_payable_verified=false`,
  `production_database_mutated=false` on every result.
- Read-only source artifact retention currently 30 days. Operator
  notifications, repeated collection, offsite restore and desktop
  crash-recovery acceptance remain release blockers.

## Remaining implementation before automatic incremental tariff installation

An official delta scope for every changed commodity, fully qualified
origin/UKIMS/certificates/additional-code rules and a separately signed
single-writer update transaction are still required. Therefore
**this stage detects and triages change signals but does not publish**.
The weekly full source capture is never skipped because no daily change
was observed.
