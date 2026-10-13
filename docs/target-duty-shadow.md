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
