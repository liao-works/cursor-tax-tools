"""Chapter-by-chapter XI source coverage receipt from pinned official evidence.

Strict gate certifies ONLY that all independently enumerated active/declarable
goods nomenclature codes have replay-verifiable import-measure responses.
It does NOT certify HMRC cross-request atomicity, customs applicability,
country-specific NI 'Other', duty calculation, or production eligibility.

Only original frozen manifest + exactly matching managed XI catalogue can
claim full source coverage. A public canary may produce diagnostics but cannot
pass the strict full-catalogue gate regardless of code completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from .xi_acceptance import evaluate
from .xi_frozen_catalogue import verify, digest_file
from .xi_measure_crawl import candidates

KIND = "xi-chapter-raw-source-coverage-receipt-v1"


def receipt(catalogue: str, checkpoint: str, *, manifest: str | None = None,
            public_canary: bool = False) -> dict:
    if public_canary:
        if manifest:
            raise ValueError("Public XI canary cannot use managed frozen manifest")
        with sqlite3.connect(Path(catalogue).resolve().as_uri()+"?mode=ro",
                             uri=True) as db:
            md = dict(db.execute("SELECT key,value FROM metadata"))
            count = db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
            if (md.get("listing_scope")!="partial_or_fixture"
                    or md.get("production_eligible")!="false" or count>5):
                raise ValueError("XI public canary receipt requires partial <=5 chapters")
        scope = "partial_or_fixture"
        cat_sha = digest_file(catalogue)
    else:
        if not manifest:
            raise ValueError("Managed XI coverage receipt needs original frozen manifest")
        pinned = verify(catalogue,manifest)
        scope = "managed_full"
        cat_sha = pinned["catalogue_sha256"]

    # Independently replay raw JSON bytes and every related import measure
    # before accepting a request as successfully collected.
    audit = evaluate(catalogue,checkpoint,min_chapters=1 if public_canary else 90)
    if audit["listing_scope"]!=scope:
        raise ValueError("XI coverage receipt source scope differs from catalogue")
    expected_codes = candidates(catalogue)
    expected_set = set(expected_codes)
    categories = {code[:2]:{"chapter":code[:2], "expected_codes":0,
                            "replayed_successes":0,"source_failures":0,
                            "missing_codes":0,"failure_samples":[],
                            "missing_samples":[]} for code in expected_codes}
    with sqlite3.connect(Path(catalogue).resolve().as_uri()+"?mode=ro",uri=True) as db:
        chapters = [x[0] for x in db.execute("SELECT chapter FROM chapters ORDER BY chapter")]
    # Preserve zero-active-commodity chapters explicitly for 97-chapter proof.
    for chapter in chapters:
        categories.setdefault(chapter,{
            "chapter":chapter,"expected_codes":0,"replayed_successes":0,
            "source_failures":0,"missing_codes":0,
            "failure_samples":[],"missing_samples":[]})
    if len(categories)!=len(chapters):
        raise ValueError("XI commodities refer to chapters absent in pinned catalogue")
    for code in expected_codes:
        categories[code[:2]]["expected_codes"]+=1

    with sqlite3.connect(Path(checkpoint).resolve().as_uri()+"?mode=ro",
                         uri=True) as db:
        statuses = dict(db.execute("SELECT code,status FROM requests"))
    if len(statuses)!=audit["requests_recorded"] or set(statuses)-expected_set:
        raise ValueError("XI coverage receipt mismatched checkpoint request IDs")

    for code,status in statuses.items():
        dst=categories[code[:2]]
        if status=="ok":
            dst["replayed_successes"]+=1
        elif status=="source_error":
            dst["source_failures"]+=1
            if len(dst["failure_samples"])<5:
                dst["failure_samples"].append(code)
        else:
            raise ValueError("XI receipt contains unsupported request status")
    for code in expected_codes:
        if code not in statuses:
            dst=categories[code[:2]]
            dst["missing_codes"]+=1
            if len(dst["missing_samples"])<5:
                dst["missing_samples"].append(code)
    chapter_rows=[]
    totals=Counter()
    for chapter in sorted(categories):
        x=categories[chapter]
        if x["expected_codes"]!=(
            x["replayed_successes"]+x["source_failures"]+x["missing_codes"]):
            raise ValueError("XI chapter source coverage accounting invariant failed")
        x["complete"]=x["missing_codes"]==0 and x["source_failures"]==0
        chapter_rows.append(x)
        for key in ("expected_codes","replayed_successes","source_failures","missing_codes"):
            totals[key]+=x[key]
    if (totals["expected_codes"]!=audit["active_declarable_codes"]
            or totals["replayed_successes"]+totals["source_failures"]!=audit["requests_recorded"]
            or totals["source_failures"]!=audit["request_failures"]
            or totals["missing_codes"]!=audit["missing_codes"]):
        raise ValueError("XI chapter receipt differs from independently replayed source audit")

    ready = (scope=="managed_full"
             and audit["all_collected_measure_responses_verified"]
             and audit["independent_catalogue_scope_ok"]
             and len(chapter_rows)>=90
             and totals["missing_codes"]==0 and totals["source_failures"]==0)
    return {
        "kind":KIND,
        "production_eligible":False,
        "ni_other_verified":False,
        "payable_duty_verified":False,
        "atomic_official_snapshot_verified":False,
        "source_scope":scope,
        "as_of":audit["date"],
        "catalogue_sha256":cat_sha,
        "checkpoint_sha256":digest_file(checkpoint),
        "chapters_collected":len(chapter_rows),
        "complete_chapters":sum(x["complete"] for x in chapter_rows),
        "pending_chapters":sum(not x["complete"] for x in chapter_rows),
        "active_declarable_codes":totals["expected_codes"],
        "successful_responses":totals["replayed_successes"],
        "failed_responses":totals["source_failures"],
        "missing_responses":totals["missing_codes"],
        "raw_import_measures_replayed":audit["replayed_source_measures"],
        "managed_full_source_coverage_pass":bool(ready),
        "readiness":"raw_source_complete_business_review_required" if ready
                    else "incomplete_source_coverage",
        "chapters":chapter_rows,
        "warning":"This receipt is raw source coverage ONLY; not NI Other or legally payable duty.",
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--checkpoint",required=True)
    p.add_argument("--manifest")
    p.add_argument("--public-canary",action="store_true")
    p.add_argument("--strict",action="store_true",help="Fail if managed whole-source coverage incomplete")
    p.add_argument("--output",default="xi-chapter-coverage-receipt.json")
    args=p.parse_args()
    result=receipt(args.catalogue,args.checkpoint,manifest=args.manifest,
                   public_canary=args.public_canary)
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as folder:
        staged=Path(folder)/"receipt.json"
        staged.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                          encoding="utf-8")
        os.replace(staged,out)
    print(json.dumps({k:v for k,v in result.items() if k!="chapters"},
                     ensure_ascii=False,indent=2))
    if args.strict and not result["managed_full_source_coverage_pass"]:
        raise SystemExit("XI full official source coverage gate not met; see per-chapter report")


if __name__=="__main__":
    main()
