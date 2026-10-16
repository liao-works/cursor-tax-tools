# Phase 6 — XI origin-aware official comparison and cross-batch source assembly

**Review-only, no production write or payable duty calculation.**

This phase fixes two missing prerequisites for eventually trustworthy
Northern Ireland "Other" evidence:

1. **Country-specific official measure comparison**, not choosing an
   unfiltered 103/1011 candidate for every third-country origin.
2. **Deterministic cross-batch resume and merge**, instead of claiming that
   a GitHub Actions ephemeral runner can preserve a full all-XI crawl.

## A. Real official source comparison

The HMRC Online Trade Tariff frontend makes the duty calculator apply
\`filter[geographical_area_id]\` using the **actual** country of origin.
An \`OTHER\` UI sentinel from an import route is not a tariff geography.
The separate type group \`third_country_duties\` comprises both 103 and 105.

\`src/target_duty/xi_origin_compare.py\` queries exactly one declarable
XI code for its unfiltered import measures and 1-3 explicitly chosen
two-letter origin codes. Each bounded V2 response is persisted in an
audit-only SQLite with compressed body, SHA-256, original measure attributes,
conditions, additional code relationships, and source URL. The output
contrasts measure IDs, type 103/105, geography and compound expressions.
No candidate becomes a payable duty rate.

Real source evidence, 2026-10-14:
https://github.com/liao-works/cursor-tax-tools/actions/runs/38508653594

Commodity 0101210000:
- Unfiltered XI: 42 import measures.
- US-filtered: 6 import measures, 1 candidate type 103, geography 1011.
- CN-filtered: 6 import measures, 1 candidate type 103, geography 1011.
- In this example the measure's directly resolved string is empty, BUT its
  linked official `duty_expression.base` is `0.00 %`, and it has multiple
  condition relationships. The linked base is correctly preserved as
  official evidence but is NOT automatically a payable 0% duty.
- This pair is a **canary**, not proof that US/CN or "Other" always match.
  Other chapters, 105 measures, Meursing, additional codes, exclusions and
  mixed/conditional expressions require representative UI reconciliation.

Run a bounded public canary only:

    python -m src.target_duty.xi_origin_compare \
      --code 0101210000 --as-of 2026-10-14 \
      --origins US,CN --public-canary \
      --output xi-origin-comparison.db

For managed authenticated calls omit \`--public-canary\` and supply
\`HMRC_CLIENT_ID\` and \`HMRC_CLIENT_SECRET\` privately. The output is still
non-production even with managed authentication.

The current \`north_ireland_rate\` desktop value is a single string.
It cannot encode different origins, conditions or additional codes;
therefore no automatic mapping to that column is authorized.

## B. Deterministic XI bounded batching

The XI source audit can now select an explicit **zero-based** start-index
over the independent, sorted XI active declarable commodity catalogue.

    python -m src.target_duty.xi_measure_crawl \
      --catalogue xi-full-catalogue-shadow.db \
      --as-of 2026-10-14 --start-index 0 --limit 100 \
      --min-interval 2 --output xi-batch-000.db

    python -m src.target_duty.xi_measure_crawl \
      --catalogue xi-full-catalogue-shadow.db \
      --as-of 2026-10-14 --start-index 100 --limit 100 \
      --min-interval 2 --output xi-batch-100.db

Default behavior **without** start-index continues from existing successful
local checkpoint. With start-index, the fixed catalogue slice is reproducible.
Do not mix as-of days or catalogue snapshots.

The \`src/target_duty/xi_merge.py\` assembler:

- validates each shard's region, date and exact catalogue-byte SHA-256;
- re-decompresses every successful original API response and checks SHA-256;
- re-parses measures and compares them against each shard's persisted rows;
- detects overlap: identical source evidence may be deduplicated, but
  conflicting source evidence fails (does not silently choose last writer);
- merges atomically into a new SQLite; corrupt source cannot replace prior
  successful merge;
- keeps explicit missing codes/source errors and
  \`production_eligible=false\` / \`other_semantics_verified=false\`.

    python -m src.target_duty.xi_merge \
      --catalogue xi-full-catalogue-shadow.db \
      --as-of 2026-10-14 \
      --shards xi-batch-000.db xi-batch-100.db \
      --output xi-merged-shadow.db

    python -m src.target_duty.xi_acceptance \
      --catalogue xi-full-catalogue-shadow.db \
      --measures xi-merged-shadow.db \
      --output xi-coverage-audit.json

Unlike an ephemeral local checkpoint, this approach lets an operator retain
and review distinct batches externally. **It does not itself provision durable
artifact retention, secure source storage, scheduled ingestion, or managed
HMRC credentials**; these must be designed separately before unattended
all-XI collection.

## C. Safeguards and remaining blockers

Full GB source remains independently validated at 1,123,765 DBT expanded
measure rows, with 16,376/16,376 GB 103/1011 baseline code parity.

XI remains **incomplete**: 2/97 chapters and five canary product records
previously fetched, not a whole-product import. The newly verified country
filter for one commodity is evidence of API semantics only. The official
frontend's "Other" row as used by the legacy desktop scraper also needs
an explicit source/UI field-level mapping: it must not be conflated with the
duty-calculator origin sentinel.

Before production:
1. Acquire **managed** HMRC tariff API credentials and document approved
   quotas, retention and operations procedure.
2. Collect all 97 official XI chapters, validate the current catalogue
   before each bounded shard, and retain shard artifacts securely.
3. Reconcile "Other" display semantics on multiple official page samples,
   including special 105/103, conditional and Meursing codes.
4. Implement source version/delta replay, completeness and parity gates.
5. Build a compact, signed and reversible GB/XI tariff snapshot specifically
   for user needs (do not deploy 1.5 GB unfiltered shadow SQLite).

Both GitHub PRs remain Draft and latest-data is unchanged.
