"""Exact per-commodity change queue from TWO accepted complete GB/NI source DBs.

A source diff is a review queue, never an instruction to patch payable duties.
GB hashes *all original expanded-measure rows*, not only 103/1011 measures.
NI compares original SHA-256 JSON:API response fingerprints. Entire official
commodity catalogues and every source row are covered, including empty codes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

CODE = re.compile(r"[0-9]{10}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def file_sha(path: str) -> str:
    digest=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source(path: str, *, region: str) -> dict:
    dbpath=Path(path).resolve()
    if not dbpath.is_file():
        raise ValueError("Original official source SQLite missing")
    proof=json.loads(dbpath.with_suffix(".json").read_text(encoding="utf-8"))
    if proof.get("production_eligible") is not False:
        raise ValueError("Source must be non-production evidence")
    digest=file_sha(str(dbpath))
    with sqlite3.connect(dbpath.as_uri()+"?mode=ro",uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("Original source SQLite integrity failure")
        if region=="GB":
            if (proof.get("kind")!="gb-full-expanded-measures-shadow"
                    or proof.get("database_sha256")!=digest
                    or proof.get("complete_within_dbt_export_scope") is not True
                    or proof.get("all_import_duties_verified") is not False
                    or proof.get("ni_other_verified") is not False):
                raise ValueError("Original GB full-source report or SHA mismatch")
            snapshot=db.execute("SELECT source_kind,source_version,as_of,production_eligible "
                                "FROM snapshots WHERE region='GB'").fetchone()
            if snapshot!=("DBT-expanded-measures",proof.get("version"),
                          proof.get("as_of"),0):
                raise ValueError("GB version/as-of snapshot mismatch")
            meta=dict(db.execute("SELECT key,value FROM meta"))
            n=db.execute("SELECT COUNT(*) FROM measures").fetchone()[0]
            if n!=proof.get("source_rows") or str(n)!=meta.get("imported_measure_rows"):
                raise ValueError("GB source expanded-measure cardinality mismatch")
            codes={}
            seen=0
            # The raw source JSON is normalized in gb_full_measures._measure_insert.
            # Hash every source row; stable sorted multiset avoids reorder-only noise.
            for code,expected_count,status in db.execute(
                  "SELECT code,row_count,status FROM requests WHERE region='GB' ORDER BY code"):
                if not CODE.fullmatch(str(code)) or code in codes:
                    raise ValueError("Invalid/duplicate GB source commodity")
                rows=[raw for (raw,) in db.execute(
                     "SELECT raw_json FROM measures WHERE region='GB' AND code=? ORDER BY raw_json",
                     (code,))]
                if len(rows)!=expected_count or (
                        status=="no_expanded_measure_rows_unverified" and rows):
                    raise ValueError("GB source measure/source-request count disagrees")
                codes[code]=hashlib.sha256(json.dumps(
                     rows,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
                seen+=len(rows)
            # DBT expanded source may include historical/non-leaf codes too.
            # Its request universe is rows-present PLUS specifically missing
            # official leaves; never equate that universe with active leaves.
            expected = (proof.get("source_commodity_codes",0)
                        +proof.get("active_leaves_without_any_export_rows",0))
            if (seen!=n or len(codes)!=expected
                    or len(codes)<proof.get("official_active_leaves",0)):
                raise ValueError("GB expanded-source/official-leaf cardinality mismatch")
            pin=proof["version"]
        elif region=="XI":
            accepted_path=dbpath.with_name("xi-public-full-acceptance.json")
            accepted=json.loads(accepted_path.read_text(encoding="utf-8"))
            if (proof.get("kind")!="xi-merged-source-checkpoint-v1"
                    or proof.get("sqlite_sha256")!=digest
                    or proof.get("source_complete") is not True
                    or proof.get("remaining_codes")!=0
                    or proof.get("failed_codes")!=0
                    or proof.get("production_eligible") is not False
                    or accepted.get("kind")!="xi-public-full-source-acceptance-review-v1"
                    or accepted.get("public_full_raw_source_coverage") is not True
                    or accepted.get("ni_other_verified") is not False
                    or accepted.get("production_eligible") is not False
                    or accepted.get("missing")!=0 or accepted.get("failed")!=0
                    or accepted.get("as_of")!=proof.get("as_of")
                    or accepted.get("expected")!=proof.get("catalogue_candidate_codes")
                    or accepted.get("responses_replayed")!=proof.get("successful_codes")):
                raise ValueError("XI merged source / independent public acceptance mismatch")
            config=dict(db.execute("SELECT key,value FROM config"))
            if config.get("region")!="XI" or config.get("as_of")!=proof.get("as_of"):
                raise ValueError("XI raw source as-of or region mismatch")
            codes={}
            for code,status,sha in db.execute(
                 "SELECT code,status,response_sha256 FROM requests ORDER BY code"):
                if (not CODE.fullmatch(str(code)) or code in codes or
                        status!="ok" or not SHA.fullmatch(str(sha))):
                    raise ValueError("Missing/invalid XI source response SHA")
                codes[code]=sha
            if (len(codes)!=accepted.get("expected") or
                    len(codes)!=accepted.get("responses_replayed")):
                raise ValueError("XI raw response count != all active declarable codes")
            pin=config["catalogue_sha256"]
        else:
            raise ValueError("Only GB and XI original source supported")
    return {"codes":codes,"as_of":date.fromisoformat(proof["as_of"]),
            "version_or_catalogue":pin,"database_sha256":digest,
            "source_rows":n if region=="GB" else len(codes)}


def create(before: str, after: str, region: str, output_csv: str,
           output_report: str) -> dict:
    if region not in ("GB","XI"):
        raise ValueError("Invalid region")
    files=[Path(x).resolve() for x in (before,after,output_csv,output_report)]
    if len(set(files))!=len(files):
        raise ValueError("Original review evidence must never be overwritten")
    old,new=(_source(before,region=region),_source(after,region=region))
    if new["as_of"]<old["as_of"]:
        raise ValueError("Backward official data as-of is not an incremental update")
    if region=="GB":
        from .dbt_version_discovery import version_key
        if version_key(new["version_or_catalogue"])<version_key(old["version_or_catalogue"]):
            raise ValueError("Backward GB official source version")
    o,n=old["codes"],new["codes"]
    allcodes=sorted(set(o)|set(n))
    changes=[]
    for code in allcodes:
        before_present,after_present=code in o,code in n
        if before_present and after_present and o[code]==n[code]:
            continue
        change=("new_official_commodity" if not before_present
                else "no_longer_in_official_catalogue" if not after_present
                else "official_source_rows_changed")
        changes.append({"code":code,"region":region,"change_kind":change,
                        "before_source_sha256":o.get(code,""),
                        "after_source_sha256":n.get(code,""),
                        "manual_applicability_review":"true",
                        "payable_rate":"","production_eligible":"false"})
    out=Path(output_csv)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",newline="",encoding="utf-8") as f:
        fields=["code","region","change_kind","before_source_sha256",
                "after_source_sha256","manual_applicability_review",
                "payable_rate","production_eligible"]
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(changes)
    types={kind:sum(c["change_kind"]==kind for c in changes) for kind in (
        "new_official_commodity","no_longer_in_official_catalogue",
        "official_source_rows_changed")}
    receipt={
        "kind":"gb-ni-complete-source-exact-code-impact-review-v1",
        "region":region,"before_as_of":old["as_of"].isoformat(),
        "after_as_of":new["as_of"].isoformat(),
        "before_pin":old["version_or_catalogue"],
        "after_pin":new["version_or_catalogue"],
        "before_db_sha256":old["database_sha256"],
        "after_db_sha256":new["database_sha256"],
        "before_all_official_codes":len(o),"after_all_official_codes":len(n),
        "before_source_rows":old["source_rows"],"after_source_rows":new["source_rows"],
        "unchanged_codes":len(allcodes)-len(changes),
        "affected_codes":len(changes),
        "change_kinds":types,
        "changed_code_csv_sha256":file_sha(str(out)),
        "scope":"all expanded source measures" if region=="GB" else "all raw public XI responses",
        "all_import_duties_verified":False,
        "ni_other_verified":False,"production_eligible":False,
        "legal_payable_rates_produced":0,
        "warning":"Exact original source diff, not a scope-certified payable rate or a safe desktop patch."
    }
    dest=Path(output_report)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",
                    encoding="utf-8")
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before",required=True)
    p.add_argument("--after",required=True)
    p.add_argument("--region",choices=("GB","XI"),required=True)
    p.add_argument("--csv",required=True)
    p.add_argument("--report",required=True)
    a=p.parse_args()
    print(json.dumps(create(a.before,a.after,a.region,a.csv,a.report),
                     indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
