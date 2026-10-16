# First exact-code GB / NI incremental review phase — 2026-10-15

**Release status: SOURCE ONLY, NOT PAYABLE / NOT PUBLISHED.**

## GB newest-version replay

Real official latest DBT version at 2026-10-15T07:00Z is **v4.0.1628**.
[Actual GB official newest replay run #38695821338](https://github.com/liao-works/cursor-tax-tools/actions/runs/38695821338)
failed closed at the source metadata/table gate because the required
`commodities-report` and `measures-on-declarable-commodities` tables are
not yet both published by DBT. Version **v4.0.1627** remains a historical
pinned baseline, not proof of current 1628 data.

The existing `dbt-newest-replay-shadow.yml` has been extended to output
`gb-changed-103-source-codes.csv` and the SHA proof after successful
replay of new DBT full data. That output compares the old and new compact
third-country 103/1011 and 103/1080 evidence (not the entire GB import
measure universe). A separate `full_source_delta_scope.py` compares ALL
original GB expanded-measure rows across two complete pinned DB snapshots,
including measures other than 103, but cannot run a truthful whole-source
delta until two comparable complete source snapshots exist.

## NI actual full-source candidate end-date scope

The NI 2026-10-14 SHA-verified full public source
[#38668119886](https://github.com/liao-works/cursor-tax-tools/actions/runs/38668119886)
contains 17,395 type 103/105 candidates across 16,630 commodity codes
from 16,728 original commodity response bodies.

[Full-source expiry-code queue #38695817246](https://github.com/liao-works/cursor-tax-tools/actions/runs/38695817246)
found **8 measures / 8 distinct source commodity codes** with
`effective_end=2026-10-15`:

- 0805102210, 0805102290, 0805102410, 0805102490
- 0805102810, 0805102890, 0805108010, 0805108090

All are source type 103/geography 1011 and are flagged for review,
not deletion, not new or zero duty. Their end date is **inclusive**:
the first next-day transition to check is **2026-10-16**.

[Actual eight-code public XI replay #38696217475](https://github.com/liao-works/cursor-tax-tools/actions/runs/38696217475)
fetched **8/8** official raw responses as_of 2026-10-15, with 0
failures. Replayed each JSON:API import-measure relationship and linked
103/105 candidate; compressed and SHA-saved all source bytes into a separate
SQLite review database (sha256:
`507ae48b38cc789dc523b7e04dd247e7a343948385f5dd4684040322661b568c`).

An independent direct comparison of the eight SHA-256 source response
fingerprints to the prior 2026-10-14 original full NI condition source
found **8/8 identical original response bytes**. All eight dated
candidate measure IDs still appear in 2026-10-15 responses, consistent
with the inclusive end date. **No actual source response change was seen**
in these eight codes on the 15th. Do not treat a date alert as a changed
rate. A new-date check on/after 2026-10-16 is the next necessary observation;
future scheduled activity is not presumed complete.

## New code and fail-closed rules

- `src/target_duty/xi_date_impact_queue.py`: exact candidate date-related
  impacted code list from SHA-pinned **all 17,395** linked condition rows,
  not a six-code sample. Distinguishes last valid calendar date vs
  after-source-end date.
- `src/target_duty/xi_targeted_delta_capture.py`: refetch only affected
  codes, preserve compressed original public API bytes/SHA, retain 103/105
  candidate IDs and date-boundary statuses, never write a partial success DB.
- `src/target_duty/gb_compact_impact_queue.py`: compare all commodity codes
  in old/new pinned compact 103 candidate proof, exact changed-code list
  with an explicit narrower source scope.
- `src/target_duty/full_source_delta_scope.py`: compare all goods codes
  from two full official GB DBT expanded-measure snapshots or two fully
  accepted NI merged raw-response snapshots. Identifies changed, added,
  and removed code source payloads, with original DB SHA proofs.
- Each module is read-only, all output `production_eligible=false`,
  no `latest-data` write, no `tariffs.db` install, and no payable NI Other.

## Blocked before production-ready incremental update

1. DBT official v4.0.1628 table/column inventory must become complete,
   and a newest full GB source replay and baseline diff must pass.
2. NI first post-expiry day must be checked using newly dated public XI
   source; the 15th is the last inclusive validity day.
3. NI canaries and known expiries are not an exhaustive changes feed:
   unknown future starts, trade restrictions, additional code, origin,
   certificates, UKIMS, preferences and other measures require full
   legal review or weekly whole-source comparison.
4. One independently approved writer, signed/immutable archive receipt,
   desktop crash-consistent transaction and startup recovery are not
   yet certified. Keep Draft PR #6 and production release blocked.
