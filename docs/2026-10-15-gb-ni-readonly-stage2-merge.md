# GB/NI source collector Stage 2: isolated merge boundary

- Default branch already has the P0 legacy release writer quarantine (PR #7).
- This PR brings only **read-only source evidence and daily monitoring**.
- Daily GB newest-version + 6 XI canary + expiry monitor is scheduled at 00:30 UTC (08:30 Singapore); official source/schema failure must fail the job and retain a JSON artifact.
- Full GB source replay and full XI four-shard source replay are deliberately **manual workflow_dispatch only in this first merge stage**. Original target schedule, after complete live acceptance: GB Sunday 01:00 UTC (09:00 Singapore), XI Sunday 02:00 UTC (10:00 Singapore).
- GB official latest v4.0.1628 had incomplete tables at 2026-10-15T07:00Z. Do not deploy weekly automatic full replay until the latest CSVW tables and source-row acceptance pass.
- XI live full source acceptance exists for 2026-10-14 (16,728/16,728), but no new-week full-scale repeated cycle is certified. Manual four-shard 340-minute workers and audit must be proven first.
- Daily looks for newest complete weekly acceptance artifacts; until then it falls back to pinned 2026-10-14 original source evidence with seven-day staleness gates.
- No `tariffs.db`, `latest-data`, Release writer, payable NI Other rates, unsigned publisher, desktop updater code or artifact promotion is included.
- Existing old workflow publishing is disabled. The next PR must activate weekly schedules only after source jobs have successful live end-to-end evidence, freshness and operator acknowledgement.
