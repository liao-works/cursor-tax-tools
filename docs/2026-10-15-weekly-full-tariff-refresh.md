# GB and NI periodic full tariff-source refresh — 2026-10-15

> **Stage-2 merge boundary (2026-10-15):** The Sunday schedules below describe the **target cadence**, NOT a currently active cron. The Stage-2 full GB and XI workflows have only `workflow_dispatch` enabled. Weekly automation remains blocked until both workflows pass real, full, recent source collection and a separate schedule-activation PR is reviewed. Daily source-only watch is the only scheduled new source workflow.

**New operating policy: daily incremental detection PLUS fresh weekly complete-source replay.**
Weekly refresh is mandatory even when no official version change is detected.
These jobs update independent **review-only shadow sources**, never the
production desktop database or GitHub `latest-data` release.

## Weekly schedule (Asia/Singapore)

| Region | GitHub workflow | UTC cron | Singapore local | Full-source scope |
|---|---|---|---|---|
| GB | `.github/workflows/weekly-gb-full-source.yml` | `0 1 * * 0` | Every Sunday 09:00 | Resolve newest official DBT pin, download all commodity catalogue data and complete DBT expanded-measures CSV, parse into new isolated SQLite |
| NI/XI | `.github/workflows/xi-public-full-measures.yml` | `0 2 * * 0` | Every Sunday 10:00 | Capture 97 chapters / all then-current declarable codes using four shards, merge and SHA-replay EVERY original response |

The earliest target Sunday date was **2026-10-18**, but the full GB/XI schedules are deliberately **not activated** in Stage 2.
GitHub Actions scheduled events only execute workflow definitions on the
**repository default branch**, and start time can be delayed by GitHub.
Draft branch changes DO NOT activate weekly full source retrieval until merged.

## Important implementation guarantees

1. GB resolves official DBT *latest metadata and CSVW compatibility first*,
   pins one immutable version, fetches a complete catalogue and all expanded
   measures, and uses one UTC as-of date for all measure applicability.
   It keeps original CSV bytes and SHA-256 along with a separately built
   SQLite source review. A sudden measure-export drop below 1,000,000 rows
   or fewer than 90% of official active leaves represented in the source
   blocks acceptance for operator review; these floors are regression alarms,
   **not proof of a fully legally payable rate**.
2. NI scheduled runs cannot reuse previous-week checkpoint/run-ID artifacts.
   Fresh catalogue and fresh four-way source fetch always begin on current
   UTC date. Only an explicit `workflow_dispatch` can request retry of an
   original same-day frozen checkpoint; the source SHA/as-of must match.
   All original response hashes and zero-gap measures must pass acceptance.
3. Each source job is read-only to the repository and uses Actions artifacts
   for output. Both reject failed fetches, missing source, broken SQLite
   or source hash mismatch; neither writes `latest-data` or desktop SQLite.
4. Results are retained in GitHub Actions for 30 days. Real offsite immutable
   archive restore remains required before long-term stability acceptance.
5. Periodic GB and NI source refresh does NOT fill missing tariff candidates,
   invent `0%`, or waive origin/UKIMS/certificate legal review.

## How full refresh combines with incremental detection

- **Daily:** query official source version/change notices and current-day
  effective/expiry dates. Where a verified change scope is available, fetch
  and validate only changed measures; retain old production DB on any error.
- **Weekly:** independently download and verify all relevant GB/NI source
  data even if no version change was detected, compare to prior accepted
  source snapshots and create a complete shadow candidate snapshot.
- **Release:** only a separately authorized, exclusive, signed publisher
  can promote a fresh, legally validated full snapshot. Incremental updates
  and weekly source audit both feed that same gate, never directly publish.
- **Monitoring:** track last successful official-source verification separately
  from the publication timestamp. Merely having no new public release for
  two days does not prove source is stale when no official change occurred.

## Testing and release blockers

`tests/test_weekly_full_refresh.py` enforces schedules, no stale checkpoint
reuse on NI cron, pinned GB version and SHA parity, complete source count
checks, read-only permissions and no production release actions.
Until CI passes and code is merged to default, this remains a planned
schedule in Draft PR #6, not an active recurring production job.

Remaining release blockers: legally payable NI Other rate interpretation;
GB missing/complex candidate application; desktop crash recovery; only one
approved production release writer and offsite verified backup restore.
