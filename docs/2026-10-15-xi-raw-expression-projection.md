# NI raw-source duty expression projection — 2026-10-15

## Why this phase

Actual frozen public XI source run [38567997207](https://github.com/liao-works/cursor-tax-tools/actions/runs/38567997207) has independently replayed **16,728** of **16,728** original commodity JSON:API responses, **1,082,800** included import measures, zero failures or gaps. The original collector's `measures.duty_expression` comes only from `measure.attributes.resolved_duty_expression`, which is empty for most real 103/105 source records. Raw JSON instead links a `duty_expression` included node containing `attributes.base` and `attributes.verbose_duty`.

## Implemented source-only correction

- `src/target_duty/xi_duty_projection.py` reads unchanged original compressed response bytes from the four saved shards. It SHA-256-verifies each response, reuses the original measure JSON parser and resolves the related `duty_expression` resource. It never rewrites source checkpoints.
- It projects measure type 103/105, geography, origin metadata, effective validity, raw `base`/`verbose_duty`, original inline value, condition/permutation counts, measure component counts, excluded-country count and a review category.
- `simple_percent_candidate_unverified` is **not** a payable NI Other value; rate field is always deliberately empty. Other categories: `conditional_candidate_review`, `compound_or_specific_candidate_review`, `specific_geography_review` and `missing_expression_review`.
- Unresolved non-empty relationships, invalid original response SHA, conflicting commodity coverage and candidate effective date mismatch fail closed. Unknown or absent candidates are never defaulted to `0%`.
- The new focused tests exercise the authentic nested JSON:API relationship shape and missing reference, conditional, 105, geographic, 0%, complex duty, expired and future cases.

## Real evidence and tests

- [Foundation CI 38664532511](https://github.com/liao-works/cursor-tax-tools/actions/runs/38664532511) passed the new source projection unit tests.
- Actual **4182-code shard 0**, decompressed and checked locally against original SHA-256 for every source response, yielded **4,464** third-country candidate measures and zero parse failures. This is a sample-based execution proof, **not** proof of all four worker outputs.
- The previous complete verified review database holds **17,395** candidate 103/105 measures over **16,630** codes. An independent read-only preflight found **17,118** non-empty base/verbose source expressions; **277** remain absent. Categories from the preflight (which does not include excluded-country relationship checks): **14,183 simple percentage candidates**, **1,882 compound/quantity**, **537 special geography**, **516 conditional**, **277 missing**. These counts are NOT legal tax classifications.
- [New full-source expression projection workflow](https://github.com/liao-works/cursor-tax-tools/actions/workflows/xi-derived-expression-review.yml) is triggered via `ops/xi-derived-expression-launch.txt`; it downloads original source snapshots from run 38567997207, independently replays the raw source, generates a 17,395-row source-expression table, and produces only 30-day review artifacts.

## Release blocker

**This phase fixes the missing expression mapping but does not certify any NI Other payable rate.** Applicability depends on selected origin country/other, exclusions, special geography, NI rules, 103 vs 105 end use, certificates, measure condition permutations, units, additional codes, source origin, and effective date. Source is a 2026-10-14 public API snapshot; this is not an atomic HMRC managed or current-day snapshot. No `latest-data` release, production SQLite write or PR merge is authorized.
