"""Materialize a *complete, queryable* GB third-country SOURCE review ledger.

Every official active suffix-80 leaf gets exactly one record, including
no-measure/unresolved goods. This must NEVER populate payable duty fields:
conditional, multiple and missing candidate expressions are not simple rates.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from .gb_review_diff import _snapshot

FIELDS=[
    "code","status","active_103_1011_candidates",
    "raw_source_measure_count","non_gb_1080_context_count",
    "active_measure_sids","duty_expressions_json","additional_codes_json",
    "conditions_json","excluded_areas_json","effective_periods_json",
    "applicability_verified","payable_rate","missing_or_review_reason",
]


def _has_qualifier(value: str | None) -> bool:
    """Structured empty official JSON arrays are not qualifying conditions."""
    if value is None:
        return False
    if not isinstance(value,str):
        raise ValueError("Official GB qualifier must be text")
    item=value.strip()
    if item in ("","[]","{}","#NA","N/A","NULL","null","None"):
        return False
    if item[0] in "[{":
        try:
            decoded=json.loads(item)
        except json.JSONDecodeError:
            # Unknown/malformed conditions must NOT be treated as none.
            return True
        return bool(decoded) if isinstance(decoded,(list,dict)) else True
    return True


def _simple_percentage(expression: str | None) -> bool:
    return isinstance(expression,str) and bool(
        re.fullmatch(r"\d+(?:\.\d+)?\s*%",expression.strip())
    )


def build(database: str, output_csv: str, output_report: str) -> dict:
    src=Path(database).resolve()
    out=Path(output_csv).resolve()
    report_file=Path(output_report).resolve()
    if src in (out,report_file) or out==report_file:
        raise ValueError("GB source ledger cannot overwrite evidence inputs")
    # Recheck original compact manifest SHA, DB and source provenance
    snapshot=_snapshot(str(src))
    out.parent.mkdir(parents=True,exist_ok=True)
    report_file.parent.mkdir(parents=True,exist_ok=True)
    status_counts=Counter()
    ambiguous=0
    with sqlite3.connect(src.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        with tempfile.TemporaryDirectory(dir=out.parent) as td:
            tmp=Path(td)/"ledger.csv"
            with tmp.open("w",newline="",encoding="utf-8") as fh:
                writer=csv.DictWriter(fh,fieldnames=FIELDS)
                writer.writeheader()
                rows=0
                for code,status,source_rows,active_count,context_count in db.execute(
                    """SELECT code,status,source_measure_rows,
                              active_target_candidates,other_area_context_rows
                       FROM commodities ORDER BY code"""
                ):
                    measures=[dict(r) for r in db.execute(
                        """SELECT sid,expression,additional_code,conditions,
                                  excluded_areas,effective_start,effective_end
                           FROM evidence
                           WHERE code=? AND role='gb_103_1011_candidate'
                                 AND geography='1011' AND effective_status='active'
                           ORDER BY source_ordinal""",(code,))]
                    if len(measures)!=active_count:
                        raise ValueError("GB ledger active-measure parity failed: "+code)
                    if status=="candidate_present_requires_applicability_review":
                        if not measures:
                            raise ValueError("GB ledger candidate record has no measure")
                        reason="candidate_not_certified_for_payable_rate"
                        if len(measures)!=1:
                            reason="multiple_active_candidates_require_business_rules"
                            ambiguous+=1
                        elif any(_has_qualifier(measures[0][key]) for key in
                                 ("conditions","additional_code","excluded_areas")):
                            reason="conditional_or_additional_code_requires_business_rules"
                            ambiguous+=1
                        elif not measures[0]["expression"]:
                            reason="candidate_expression_missing"
                            ambiguous+=1
                        elif not _simple_percentage(measures[0]["expression"]):
                            reason="compound_or_specific_expression_requires_calculation"
                            ambiguous+=1
                    elif status=="non_gb_1080_only_or_other_measures_unresolved":
                        reason="no_active_gb_103_1011_non_gb_1080_is_not_gb"
                    elif status=="no_expanded_measure_rows_unverified":
                        reason="no_official_expanded_measure_rows_not_zero_rate"
                    else:
                        reason="no_active_gb_103_1011_not_zero_rate"
                    def j(values):
                        return json.dumps(values,ensure_ascii=False,separators=(",",":"))
                    writer.writerow({
                        "code":code,"status":status,
                        "active_103_1011_candidates":active_count,
                        "raw_source_measure_count":source_rows,
                        "non_gb_1080_context_count":context_count,
                        "active_measure_sids":j([m["sid"] for m in measures]),
                        "duty_expressions_json":j([m["expression"] for m in measures]),
                        "additional_codes_json":j([m["additional_code"] for m in measures]),
                        "conditions_json":j([m["conditions"] for m in measures]),
                        "excluded_areas_json":j([m["excluded_areas"] for m in measures]),
                        "effective_periods_json":j([[m["effective_start"],m["effective_end"]]
                                                     for m in measures]),
                        "applicability_verified":"false",
                        "payable_rate":"",
                        "missing_or_review_reason":reason,
                    })
                    rows+=1
                    status_counts[status]+=1
            if rows!=len(snapshot["codes"]) or sum(status_counts.values())!=rows:
                raise ValueError("GB ledger fails full active-leaf row parity")
            if status_counts.get("candidate_present_requires_applicability_review",0)!=snapshot["target_codes"]:
                raise ValueError("GB ledger active-target count differs from source evidence")
            os.replace(tmp,out)
    result={
        "kind":"gb-all-active-leaf-third-country-review-ledger-v1",
        "version":snapshot["version"],
        "as_of":snapshot["as_of"],
        "compact_sha256":snapshot["sha256"],
        "official_active_leaf_codes":len(snapshot["codes"]),
        "candidate_codes":snapshot["target_codes"],
        "unresolved_codes":len(snapshot["codes"])-snapshot["target_codes"],
        "complex_or_ambiguous_candidate_codes":ambiguous,
        "status_counts":dict(status_counts),
        "exactly_one_row_per_active_leaf":True,
        "source_coverage_verified":True,
        "all_rates_payable_verified":False,
        "rate_applicability_verified":False,
        "ni_other_verified":False,
        "production_eligible":False,
        "csv_file":out.name,
        "warning":"This is a complete GB candidate-evidence listing, NOT a production payable tariff file.",
    }
    report_file.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                           encoding="utf-8")
    return result


def lookup(database: str, code: str) -> dict:
    """Get a strictly read-only, source-verified commodity review record."""
    if not isinstance(code,str) or len(code)!=10 or not code.isascii() or not code.isdigit():
        raise ValueError("GB commodity lookup requires exactly ten digits")
    snapshot=_snapshot(database)  # entire immutable source/manifest checked
    with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute(
            """SELECT code,status,source_measure_rows,
                      active_target_candidates,other_area_context_rows
               FROM commodities WHERE code=?""",(code,)).fetchone()
        if row is None:
            return {
                "region":"GB","commodity_code":code,
                "status":"not_in_current_official_active_leaf_catalogue",
                "version":snapshot["version"],"as_of":snapshot["as_of"],
                "source_coverage_verified":True,"payable_rate":None,
                "production_eligible":False,
            }
        evidence=[dict(r) for r in db.execute(
            """SELECT sid,measure_type,geography,expression,additional_code,
                      conditions,excluded_areas,effective_start,effective_end
               FROM evidence WHERE code=? AND role='gb_103_1011_candidate'
                         AND geography='1011' AND effective_status='active'
               ORDER BY source_ordinal""",(code,))]
    if len(evidence)!=row["active_target_candidates"]:
        raise ValueError("GB lookup evidence parity differs from source")
    return {
        "region":"GB","commodity_code":code,
        "status":row["status"],
        "version":snapshot["version"],"as_of":snapshot["as_of"],
        "source_coverage_verified":True,
        "raw_source_measure_rows":row["source_measure_rows"],
        "active_103_1011_count":len(evidence),
        "non_gb_1080_context_rows":row["other_area_context_rows"],
        "official_candidate_evidence":evidence,
        "payable_rate":None,
        "rate_applicability_verified":False,
        "ni_other_verified":False,
        "production_eligible":False,
        "warning":"Source candidates are not payable duty; missing evidence never means 0%.",
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",required=True)
    p.add_argument("--code",help="Lookup exact 10-digit commodity, no output files")
    p.add_argument("--output-csv",default="gb-complete-source-review.csv")
    p.add_argument("--output-report",default="gb-complete-source-review.json")
    args=p.parse_args()
    report=(lookup(args.database,args.code) if args.code is not None
            else build(args.database,args.output_csv,args.output_report))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
