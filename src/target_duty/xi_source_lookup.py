"""Read-only, source-replayed XI raw import evidence by commodity code.

The lookup does not infer the NI desktop 'Other' field or payable duty.
103/105 are merely third-country candidates until actual origin, UKIMS,
additional codes, declaration context and conditions are resolved.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from .xi_acceptance import evaluate, decompress_checked, THIRD_COUNTRY_GROUP
from .xi_measure_crawl import candidates
from .xi_origin_compare import extract

CODE = re.compile(r"[0-9]{10}\Z")


def lookup_many(catalogue: str, checkpoint: str, codes: list[str], *,
                min_chapters: int = 90) -> dict:
    if not 1 <= len(codes) <= 1000:
        raise ValueError("XI review lookup requires 1..1000 codes")
    if len(set(codes)) != len(codes) or any(
            not isinstance(c,str) or not CODE.fullmatch(c) for c in codes):
        raise ValueError("XI lookup requires distinct ten-digit commodity strings")
    # Deliberately expensive: never trust a cached request status, body or
    # measure row without validating the whole original source snapshot.
    audit=evaluate(catalogue,checkpoint,min_chapters=min_chapters)
    valid=set(candidates(catalogue))
    db=sqlite3.connect(Path(checkpoint).resolve().as_uri()+"?mode=ro",uri=True)
    try:
        results=[]
        for code in codes:
            base={
                "code":code,
                "as_of":audit["date"],
                "source_scope":audit["listing_scope"],
                "production_eligible":False,
                "ni_other_verified":False,
                "payable_rate":None,
                "origin_specific_applicability_verified":False,
                "third_country_103_105_candidate_count":0,
                "candidate_evidence":[],
            }
            if code not in valid:
                base["status"]="not_in_enumerated_active_declarable_xi_catalogue"
            else:
                row=db.execute(
                    "SELECT status,compressed_body,error,response_sha256 "
                    "FROM requests WHERE code=?",(code,)
                ).fetchone()
                if row is None:
                    base["status"]="source_not_collected"
                elif row[0]=="source_error":
                    base["status"]="source_error"
                    base["source_error"]=row[2]
                elif row[0]=="ok":
                    body=decompress_checked(row[1])
                    parts=extract(code,body)
                    candidates_103_105=[{
                        "measure_id":m["measure_id"],
                        "measure_type":m["measure_type"],
                        "geography":m["geography"],
                        "resolved_duty_expression":m["expression"],
                        "linked_duty_base":m["linked_duty_base"],
                        "linked_measure_component_count":m["linked_measure_component_count"],
                        "conditions_present":m["conditions"],
                        "additional_code_present":m["additional_code"],
                        "exclusions_present":m["exclusions"],
                        "effective_start":m["effective_start"],
                        "effective_end":m["effective_end"],
                    } for m in parts if m["measure_type"] in THIRD_COUNTRY_GROUP]
                    base.update({
                        "status":("source_response_no_import_measures" if not parts
                                  else "unfiltered_raw_source_candidates"),
                        "raw_source_sha256":row[3],
                        "raw_import_measure_count":len(parts),
                        "third_country_103_105_candidate_count":len(candidates_103_105),
                        "candidate_evidence":candidates_103_105,
                    })
                else:
                    raise ValueError("XI checkpoint contains unknown source state")
            results.append(base)
    finally:
        db.close()
    return {
        "kind":"xi-raw-source-commodity-lookup-review-v1",
        "production_eligible":False,
        "ni_other_verified":False,
        "payable_duty_verified":False,
        "as_of":audit["date"],
        "source_scope":audit["listing_scope"],
        "managed_full_source_coverage_proven":(
            audit["independent_catalogue_scope_ok"]
            and audit["all_collected_measure_responses_verified"]
        ),
        "requested_codes":len(results),
        "results":results,
        "warning":(
            "The raw XI measures are NOT filtered by actual origin. "
            "Do not map 103/105 or absent responses to a payable Other rate."
        ),
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--checkpoint",required=True)
    inputs=p.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--code",help="Exactly one 10-digit XI commodity code")
    inputs.add_argument("--codes-file",help="UTF-8 file: one 10-digit code per line, up to 1000")
    p.add_argument("--min-chapters",type=int,default=90)
    p.add_argument("--output",default="xi-raw-source-lookup.json")
    args=p.parse_args()
    codes=([args.code] if args.code else
           [line.strip() for line in Path(args.codes_file).read_text(encoding="utf-8").splitlines()])
    result=lookup_many(args.catalogue,args.checkpoint,codes,
                       min_chapters=args.min_chapters)
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.resolve() in (Path(args.catalogue).resolve(),Path(args.checkpoint).resolve()):
        raise ValueError("XI lookup output cannot overwrite source")
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                      encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="results"},
                     ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
