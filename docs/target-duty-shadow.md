# Target-only Third Country Duty pipeline (shadow mode)

Scope: **GB measure type 103, geographical area 1011 (ERGA OMNES)**,
active on requested `--as-of` date, all declarable 10-digit commodity codes.
It does not compute actual payable import duties (excludes preferential measures,
VAT, anti-dumping, quotas, and other special regimes).

- Fetch a *filtered* DBT official CSV, rather than a GB-sized full SQLite or HTML.
- Distinct codes only; ambiguous rates and additional-code/condition cases are
  `uk_rate_status=conditional`, **never silently converted to 0%**.
- Legacy NI values are explicitly `ni_rate_status=stale`; no XI parsing is
  trusted in this version. Source verification for NI is a separate gate.
- Old `tariffs` fields stay queryable by existing Liao Tools.
- Reject low coverage and low verified ratios before publishing anything.
- Workflow uploads only an Actions artifact and has `contents: read`. It
  **never modifies** the live `latest-data` Release or default branch.

Run:
```sh
python -m unittest discover -s tests -p test_target_duty.py -v
python -m src.target_duty.build --version latest --output target-duty.db
python -m src.target_duty.build --csv fixture.csv --version v4.0.1595 --min-records 1
```

**Next promotion gate:** reconcile GB sample/source-version against official API;
verify the meaning of XI 'Other' using official machine-readable measures; ship
NI-only output once established; publish immutable signed snapshots. Do not
mark legacy NI as verified.

Production scraper workflows remain unchanged in this branch.

## Real official-source reconciliation (2026-10-13; PR #6)

The shadow-build CI job now downloads the **current legacy Release via an immutable
versioned URL**, checks `file_size` + `sha256`, builds the **GB 103/1011-only**
snapshot and generates `target-duty-diff.json` plus a full review CSV.

First observed results, not final product acceptance:

| Category | Codes |
|---|---:|
| Legacy code set | 16,592 |
| Focused GB code set | 16,376 |
| GB unique, unambiguous target expressions | 16,197 |
| GB conditional / multiple candidates | 179 |
| Textually different but semantically equivalent formats | 14,526 |
| Different straightforward percentage | 375 |
| **Old percent vs new compound duty expression** | **535** |
| Other complex/missing expression differences | 468 |
| Legacy-only commodity codes | 512 |
| New-only commodity codes | 296 |
| Historical NI rates carried as **stale** | 15,761 |
| NI not available from legacy by code | 615 |

These categories are per-code, comparing old vs new (the two code sets differ),
and must not be interpreted as official tariff changes. Some examples:
`0.00%` vs `0%` is formatting only; `10.00%` vs
`10% + 77 GBP / 100 kg` is **not** equivalent and exposes a legacy
precision loss. Do not truncate official compound expressions for the desktop.

## XI/NI "Other" caveat

The official **public** XI V2 API can be queried for an audit sample. For
`0101210000`, the API returned a `103`/ERGA OMNES candidate but its
`resolved_duty_expression` was empty and it has conditions. Therefore the
current XI audit correctly leaves `rate=null` and `status=review_required`.
It is **not valid** to equate every XI `103` with the page label "Other".
Do not overwrite the old `north_ireland_rate` column from these candidates.

The authenticated production XI API requires `HMRC_CLIENT_ID` and
`HMRC_CLIENT_SECRET` in GitHub Actions secrets for the manual audit workflow.
The *public* canary is only a small schema check and may be rate-limited.

## Promotion gates (not met)

1. Confirm 512 legacy-only codes and 296 new-only codes individually or by
   versioned commodity mapping; never delete codes because of a missing 103.
2. Review all 375 simple percentage changes and the 535 new complex expressions;
   calculate a per-code effective-date provenance.
3. Verify NI "Other" semantics against authoritative XI measure/condition data
   across representative commodity categories and publish only when trusted.
4. Add immutable **signed** V2 manifest and implement explicit client channel
   switching/rollback before shipping focused DB to end users.
5. Keep existing Release `latest-data` and scheduled production scrapers
   unchanged until validation succeeds.

CI workflow `Focused third-country duty tests` creates and uploads shadow
artifacts. It never performs a production Release publish.
