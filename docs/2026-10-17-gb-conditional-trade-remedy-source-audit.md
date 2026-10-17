# GB trade-remedy conditional-component source audit (2026-10-17)

This is **source evidence**, not a certification of rates payable by a Chinese exporter.

Evidence: [independent read-only workflow run 39008315022](https://github.com/liao-works/cursor-tax-tools/actions/runs/39008315022), attached `gb-condition-components-review-39008315022.zip`. The run replayed the fully accepted GB source version `v4.0.1629` from run `38995575082`.

## Observed scope

- Relevant GB 551/552/553/554 measure SIDs: **9,159**
- Remedy measure SIDs whose expanded measures have official conditions: **7,588**
- Linked measure SIDs found via `measure-conditions.dependent_measure_id -> measures.trackedmodel_ptr_id -> measures.sid`: **7,588 / 7,588**
- Official normalized condition rows: **1,660,112**, of which **38,570** relate to remedies
- Official condition component rows: **809,194**, of which **18,816** link to remedies
- No unresolved condition relationship IDs after joining certificates from `certificates.certificate_type_id` + `certificate_types.sid`.
- Many condition rows have no duty component, or a blank component duty amount. Those are not automatically 0%.

## Why this evidence is not yet a production payable-rate mapping

The pinned normalized source contains multiple historical tracked-model revisions of a measure's condition (same public SID, different `trackedmodel_ptr_id`). For example, GB remedy measure `20272576` (commodity `1516209821`) has D008-linked candidate expressions `15.68 %` and `14.79 %` under different tracked records, alongside a no-certificate candidate `54.64 %` and blank later revisions. Assigning any one of those expressions as the current rate without applying the official version/transaction lineage would be wrong.

Required before promotion: use the pinned official revision/history records to select exactly the effective condition revisions and reconcile them against `measures-as-defined` and the live official commodity measures; prove a unique pairing for company additional code, certificate/action, rate, and effective date. A single ambiguous link must remain unresolved; do not substitute Other or a non-conditional rate for an explicit company condition. A source expression like `% or amount` without an official duty amount is **not** an actual tax rate.

The current published data snapshot intentionally does **not** use these ambiguous GB condition-component candidates as confirmed rates. North Ireland XI condition-specific records have independent JSON:API provenance and are projected separately.
