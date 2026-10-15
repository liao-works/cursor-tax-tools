"""Exact NI commodity review queue for 103/105 candidate effective-date ends.

Derives affected codes from ALL 17,395 source-only original condition details,
not six sample codes. This is a date-change queue only: NOT the entire NI
source delta; no payable rate, not evidence of all current measures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

CODE=re.compile(r"[0-9]{10}\Z")
HEX=re.compile(r"[0-9a-f]{64}\Z")


def sha(path: str) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""):
            h.update(part)
    return h.hexdigest()


def collect(csv_source: str, summary_source: str, output_csv: str, output_report: str,
            *, through: date) -> dict:
    input_paths=[Path(x).resolve() for x in (csv_source,summary_source)]
    output_paths=[Path(x).resolve() for x in (output_csv,output_report)]
    if len(set(input_paths+output_paths))!=4:
        raise ValueError("Original XI evidence must never be overwritten")
    proof=json.loads(Path(summary_source).read_text(encoding="utf-8"))
    if (proof.get("kind")!="xi-third-country-raw-conditions-evidence-v1"
            or proof.get("source_full_complete") is not True
            or proof.get("expected_codes",0)<10000
            or proof.get("verified_original_responses")!=proof.get("expected_codes")
            or proof.get("candidate_measures",0)<10000
            or proof.get("ni_other_verified") is not False
            or proof.get("production_eligible") is not False
            or proof.get("output_csv_sha256")!=sha(csv_source)):
        raise ValueError("NI full conditions source incomplete or SHA tampered")
    original=date.fromisoformat(proof["as_of"])
    if through<original:
        raise ValueError("Review day precedes full original source")
    matches=[]
    unique=set()
    all_codes=set()
    with Path(csv_source).open(newline="",encoding="utf-8") as src:
        for row in csv.DictReader(src):
            code=row["code"]
            mid=row["measure_id"]
            if not CODE.fullmatch(code) or not mid or (code,mid) in unique:
                raise ValueError("Duplicated/malformed candidate source")
            if row["production_eligible"]!="false" or row["ni_other_payable_rate"]:
                raise ValueError("Source record improperly marked payable")
            unique.add((code,mid))
            all_codes.add(code)
            if row["measure_type"] not in ("103","105"):
                raise ValueError("Non-target XI source evidence")
            end=row["effective_end"]
            if not end:
                continue
            finish=date.fromisoformat(end[:10])
            if original<finish<=through:
                matches.append({
                    "code":code,"measure_id":mid,"candidate_type":row["measure_type"],
                    "geography_id":row["geography_id"],
                    "effective_end":finish.isoformat(),
                    "review_reason":"KNOWN_103_105_SOURCE_END",
                    "current_source_refetched":"false",
                    "payable_rate":"","production_eligible":"false",
                })
    if (len(unique)!=proof["candidate_measures"]
            or len(all_codes)!=proof["candidate_codes"]):
        raise ValueError("XI full 103/105 candidate evidence count mismatch")
    matches.sort(key=lambda r:(r["code"],r["measure_id"]))
    dest=Path(output_csv)
    dest.parent.mkdir(parents=True,exist_ok=True)
    fields=["code","measure_id","candidate_type","geography_id",
            "effective_end","review_reason","current_source_refetched",
            "payable_rate","production_eligible"]
    with dest.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(matches)
    details=Counter(x["effective_end"] for x in matches)
    report={
        "kind":"xi-known-candidate-end-affected-code-queue-v1",
        "source_as_of":original.isoformat(),
        "review_through":through.isoformat(),
        "full_original_source_responses":proof["verified_original_responses"],
        "full_candidate_measure_count":len(unique),
        "source_candidates_codes":len(all_codes),
        "candidate_effective_end_measures":len(matches),
        "affected_distinct_codes":len({x["code"] for x in matches}),
        "ending_on_dates":dict(sorted(details.items())),
        "original_condition_source_csv_sha256":proof["output_csv_sha256"],
        "impact_queue_csv_sha256":sha(str(dest)),
        "delta_scope":"KNOWN expired 103/105 candidates ONLY",
        "future_starts_exhaustively_checked":False,
        "other_measure_changes_exhaustively_checked":False,
        "today_live_source_confirmed":False,
        "ni_other_payable_verified":False,
        "production_eligible":False,
    }
    Path(output_report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                                    encoding="utf-8")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-csv",required=True)
    p.add_argument("--source-summary",required=True)
    p.add_argument("--through",required=True)
    p.add_argument("--csv",required=True)
    p.add_argument("--report",required=True)
    a=p.parse_args()
    print(json.dumps(collect(a.source_csv,a.source_summary,a.csv,a.report,
                             through=date.fromisoformat(a.through)),indent=2))


if __name__=="__main__":
    main()
