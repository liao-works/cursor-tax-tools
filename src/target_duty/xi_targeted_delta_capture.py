"""Retrieve exactly the independently proven affected XI commodity codes.

This is a source-only immutable per-code replay, not a 0% inference or
an installation of NI Other payable rates. Any failed code blocks acceptance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tempfile
import time
import zlib
from datetime import date,datetime,timezone
from pathlib import Path

from .xi_duty_projection import project
from .xi_measure_crawl import managed_fetcher, parse


def sha(path: str) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):
            h.update(b)
    return h.hexdigest()


def review(queue_csv: str, queue_report: str, output_db: str, output_report: str,
           *, as_of: date, fetcher=None, sleep_seconds=0.6) -> dict:
    paths=[Path(p).resolve() for p in
           (queue_csv,queue_report,output_db,output_report)]
    if len(paths)!=len(set(paths)):
        raise ValueError("Do not overwrite original XI date-impact source")
    proof=json.loads(Path(queue_report).read_text(encoding="utf-8"))
    if (proof.get("kind")!="xi-known-candidate-end-affected-code-queue-v1"
            or proof.get("production_eligible") is not False
            or proof.get("ni_other_payable_verified") is not False
            or proof.get("full_original_source_responses",0)<10000
            or proof.get("impact_queue_csv_sha256")!=sha(queue_csv)
            or proof.get("review_through")!=as_of.isoformat()):
        raise ValueError("Original XI affected-code queue untrusted or changed")
    rows=[]
    with Path(queue_csv).open(newline="",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code,measure=r["code"],r["measure_id"]
            if (len(code)!=10 or not code.isascii() or not code.isdigit()
                    or not measure or r["review_reason"]!="KNOWN_103_105_SOURCE_END"
                    or r["production_eligible"]!="false" or r["payable_rate"]):
                raise ValueError("Invalid source-only XI affected code record")
            if date.fromisoformat(r["effective_end"])>as_of:
                raise ValueError("Future expired-measure event in affected queue")
            rows.append(r)
    codes=sorted({r["code"] for r in rows})
    if (len(rows)!=proof["candidate_effective_end_measures"]
            or len(codes)!=proof["affected_distinct_codes"] or not codes):
        raise ValueError("Original affected-code count mismatch")
    fetcher=fetcher or managed_fetcher("","",public=True)
    outcomes=[]
    raw_rows=[]
    for code in codes:
        affected=sorted({r["measure_id"] for r in rows if r["code"]==code})
        try:
            raw=fetcher(code,as_of)
            if not isinstance(raw,bytes) or len(raw)==0 or len(raw)>8_000_000:
                raise ValueError("Bad public XI source response size/body")
            # Both parsers independently check original source JSON:API
            # relationships and code identity. Never invent empty measures.
            source_rows=parse(raw,code)
            candidates=project(raw,code,as_of)
            active={str(c["measure_id"]) for c in candidates}
            fingerprint=hashlib.sha256(raw).hexdigest()
            raw_rows.append((code,as_of.isoformat(),fingerprint,len(raw),
                            zlib.compress(raw,6)))
            outcomes.append({
                "code":code,"source_status":"verified_raw_public_XI",
                "source_sha256":fingerprint,
                "original_import_measures":len(source_rows),
                "current_103_105_candidates":len(candidates),
                "expired_previous_measure_ids":affected,
                "previous_ids_still_in_current_candidate_set":
                    sorted(set(affected)&active),
                "source_checked_at":datetime.now(timezone.utc).isoformat(),
                "rate_payable":None,"production_eligible":False
            })
        except Exception as exc:
            outcomes.append({"code":code,"source_status":"error",
                             "error":type(exc).__name__+": "+str(exc)[:260],
                             "rate_payable":None,"production_eligible":False})
        if sleep_seconds:
            time.sleep(sleep_seconds)
    failed=sum(o["source_status"]=="error" for o in outcomes)
    output=Path(output_db).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    if not failed:
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            stage=Path(temp)/"xi-review.db"
            with sqlite3.connect(stage) as db:
                db.execute("""CREATE TABLE source_responses(
                    code TEXT PRIMARY KEY,as_of TEXT,response_sha256 TEXT,
                    original_bytes INTEGER,compressed_raw_json BLOB)""")
                db.executemany("INSERT INTO source_responses VALUES(?,?,?,?,?)",raw_rows)
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Temp XI targeted-review DB integrity failure")
            stage.replace(output)
    summary={
        "kind":"xi-exact-code-date-delta-live-public-replay-v1",
        "as_of":as_of.isoformat(),
        "source_condition_csv_sha256":proof["original_condition_source_csv_sha256"],
        "affected_source_queue_sha256":proof["impact_queue_csv_sha256"],
        "selected_distinct_codes":len(codes),
        "verified_public_responses":len(raw_rows),
        "failed_responses":failed,
        "source_success_complete":failed==0 and len(raw_rows)==len(codes),
        "source_db_sha256":sha(str(output)) if not failed else None,
        "source_measure_review":outcomes,
        "source_scope":"ONLY known 103/105 measures ending since original source",
        "all_NI_source_changes_captured":False,
        "public_not_HMRC_managed_OAuth":True,
        "NI_Other_payable_certified":False,
        "production_eligible":False,
        "changed_desktop_db":False
    }
    target=Path(output_report)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queue-csv",required=True)
    p.add_argument("--queue-report",required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--output-db",required=True)
    p.add_argument("--output-report",required=True)
    a=p.parse_args()
    result=review(a.queue_csv,a.queue_report,a.output_db,a.output_report,
                  as_of=date.fromisoformat(a.as_of))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result["source_success_complete"]:
        raise SystemExit("Targeted XI source fetch incomplete: source-only REVIEW and no release")


if __name__=="__main__":
    main()
