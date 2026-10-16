# Data-first acceptance — official GB v4.0.1627 vs previous v4.0.1626

**Priority changed:** source completeness and correctness take precedence over
new S3/archive features. Keep all producer and consumer PRs Draft while NI
`Other` semantics and deployable duties remain unverified.

## Real, not simulated, GB newest data result (2026-10-14)

Official DBT version inventory, required table and CSVW schema checks passed
for highest discovered `v4.0.1627`. PR CI actually downloaded the exact
newest GB catalogue, independently rebuilt its focused third-country
candidate baseline, imported **all 1,123,765 official expanded measures**,
then built the compact database and checked source-version stability.

Successful actual CI:
https://github.com/liao-works/cursor-tax-tools/actions/runs/38538948392

Observed from actual source:

- **16,742** official active declarable leaf commodity codes.
- **16,376** codes have current type `103` and geography `1011`
  GB third-country *candidate* measures.
- Exactly **16,560** type-103/1011 source candidate rows across all dates.
- **359** other active leaf codes only have geography 1080
  Channel Islands context within the focused review; 1080 must NOT
  substitute for GB 1011.
- **7** other special leaf codes have no raw expanded measure rows:
  9930240000, 9930270000, 9930990000, 9931240000,
  9931270000, 9931990000, 9950000000.
- **366** codes are therefore explicitly unresolved for the requested
  GB 103/1011 rate scope. Unknown does NOT mean 0%.

New vs old source comparison on the same `as_of=2026-10-14`:

- Old `v4.0.1626`; new `v4.0.1627`.
- Zero gained/lost active target candidates.
- Zero changed currently-active 103/1011 expressions/conditions or
  inactive/other-geography source evidence for common active leaves.
- Zero active-leaf commodity additions/deletions.

This is not proof that VAT, anti-dumping, quota balance, trade
remedies, conditions or all legal import costs are covered. The DBT
expanded dataset has a narrower scope than all official import duties.

## Queryable complete GB source ledger

`src/target_duty/gb_source_ledger.py` emits a UTF-8 CSV with one row per
active leaf code, plus a JSON coverage receipt:

    python -m src.target_duty.gb_source_ledger \
      --database gb-newest-compact-review.db \
      --output-csv gb-newest-all-codes.csv \
      --output-report gb-newest-code-coverage.json

The source SQLite's original JSON manifest and SHA-256 must pass
independent validation before this export is written. Every CSV row
includes original evidence expressions, conditions, additional codes,
exclusions, effective periods and explicit unresolved reason. Even
an official source expression `0%` is not falsely presented as a
verified rate payable for an arbitrary declaration.

For a single code:

    python -m src.target_duty.gb_source_ledger \
      --database gb-newest-compact-review.db --code 0101210000

The JSON response preserves all applicable-candidate evidence and
`payable_rate=null`, `rate_applicability_verified=false`, and
`production_eligible=false`. A code absent from current official
active declarable leaves gets an explicit not-in-catalogue status.

The newest-GB PR CI generates and archives:
`gb-newest-compact-review.db`, `.json`,
`gb-newest-all-codes.csv`, `gb-newest-code-coverage.json`,
and the full `gb-newest-versus-baseline.json` diff.

## Actual business usability gates still required

1. **GB:** determine whether the 366 remaining active-leaf codes
   legitimately have no default GB 103/1011 duty, require alternative
   declarations or have official hierarchy/measure exceptions.
   Independently resolve real compound rates, additional code
   qualifications and special conditions before calling anything
   a payable rate. The source ledger does not fill that gap.
2. **XI:** authenticated managed official full chapter enumeration and
   all active-declarable commodity response replay. The present public
   canary includes only two chapters and five of 516 code responses.
   It is not complete NI and cannot supply an NI `Other` column.
3. **Origin and declaration:** demonstrate the desktop legacy `Other`
   field's exact meaning using origin-country filters, 103/105, dates,
   additional codes, quotas and relevant HMRC/UKIMS rules. No blanket
   GB-to-XI substitution.
4. **Consumer:** compare existing production `tariffs.db` against
   a fully accepted country/date-specific data contract, then perform
   migration/rollback and real desktop query tests in a separate
   authorized review stage before release.

**None of the new shadow data may be released or merged automatically.**
