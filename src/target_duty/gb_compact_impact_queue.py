"""Exact affected GB 103/1011 compact-source code queue from pinned proof.

This is limited to GB 103/1011 and 1080 contextual source evidence; full
GB expanded-measure diff must use full_source_delta_scope.py instead.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
from .gb_review_diff import _snapshot,_digest
from .dbt_version_discovery import version_key


def impact(previous: dict, latest: dict) -> list[dict]:
    if (version_key(latest["version"])<version_key(previous["version"]) or
        latest["as_of"]<previous["as_of"]):
        raise ValueError("Backward official GB evidence version/date")
    old,new=previous["codes"],latest["codes"]
    rows=[]
    for code in sorted(set(old)|set(new)):
        p=old.get(code)
        n=new.get(code)
        if p is None:
            kind="added_active_commodity"
        elif n is None:
            kind="removed_active_commodity"
        elif (p["status"]!=n["status"] or
              _digest(p["all"])!=_digest(n["all"]) or
              _digest(p["active"])!=_digest(n["active"])):
            kind="changed_third_country_candidate_or_context"
        else:
            continue
        rows.append({"code":code,"change_kind":kind,
                     "before_status":p["status"] if p else "",
                     "after_status":n["status"] if n else "",
                     "requires_human_review":"true",
                     "payable_rate":"","production_eligible":"false"})
    return rows


def export(before: str, after: str, csv_file: str, report_file: str) -> dict:
    paths=[Path(p).resolve() for p in (before,after,csv_file,report_file)]
    if len(set(paths))!=len(paths):
        raise ValueError("Review outputs cannot overwrite source")
    p,n=_snapshot(before),_snapshot(after)
    rows=impact(p,n)
    dest=Path(csv_file)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=(
            "code","change_kind","before_status","after_status",
            "requires_human_review","payable_rate","production_eligible"))
        writer.writeheader()
        writer.writerows(rows)
    report={
        "kind":"gb-exact-103-source-candidate-affected-code-review-v1",
        "baseline_version":p["version"],"current_version":n["version"],
        "baseline_as_of":p["as_of"],"current_as_of":n["as_of"],
        "baseline_compact_sha256":p["sha256"],
        "current_compact_sha256":n["sha256"],
        "baseline_commodity_codes":len(p["codes"]),
        "current_commodity_codes":len(n["codes"]),
        "affected_commodity_codes":len(rows),
        "source_csv_sha256":hashlib.sha256(dest.read_bytes()).hexdigest(),
        "source_scope":"GB 103/1011 candidates and 103/1080 context ONLY",
        "other_measures_proven_unchanged":False,
        "production_eligible":False,
        "payable_rate_verified":False,
        "ni_other_verified":False,
    }
    Path(report_file).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before",required=True)
    parser.add_argument("--after",required=True)
    parser.add_argument("--csv",required=True)
    parser.add_argument("--report",required=True)
    args=parser.parse_args()
    print(json.dumps(export(args.before,args.after,args.csv,args.report),indent=2))


if __name__=="__main__":
    main()
