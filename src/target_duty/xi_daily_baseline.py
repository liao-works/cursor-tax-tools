"""Compact SHA-backed daily XI canary baseline from a fully accepted raw replay.

Created only by the weekly all-code source audit, not by a 6-code sample.
It tracks original-response digests and known candidate expiry boundaries;
it is NOT a complete daily delta feed or a payable NI rate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

CANARIES=("0101210000","0101299000","0207412010",
          "0713101000","0713109000","0805102210")
HEX=re.compile(r"[0-9a-f]{64}\Z")


def _digest(path: str) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def build(merged: str, manifest: str, acceptance: str, *, run_id: int,
          output: str, min_codes: int=10000) -> dict:
    if not (isinstance(run_id,int) and run_id>0) or not (min_codes>=1):
        raise ValueError("Invalid XI capture identifiers")
    if Path(output).resolve() in {Path(merged).resolve(),
                                Path(manifest).resolve(),Path(acceptance).resolve()}:
        raise ValueError("Cannot overwrite raw source evidence")
    m=json.loads(Path(manifest).read_text(encoding="utf-8"))
    a=json.loads(Path(acceptance).read_text(encoding="utf-8"))
    if (m.get("kind")!="xi-public-full-catalogue-frozen-review-v1"
            or a.get("kind")!="xi-public-full-source-acceptance-review-v1"
            or m.get("as_of")!=a.get("as_of")
            or a.get("expected")!=m.get("active_declarable_codes")
            or a.get("expected",0)<min_codes
            or a.get("missing")!=0 or a.get("failed")!=0
            or a.get("responses_replayed")!=a.get("expected")
            or a.get("public_full_raw_source_coverage") is not True
            or a.get("production_eligible") is not False
            or a.get("ni_other_verified") is not False):
        raise ValueError("Cannot trust unaccepted/incomplete XI source for daily baseline")
    asof=date.fromisoformat(m["as_of"])
    signatures={}
    ends=Counter()
    with sqlite3.connect(Path(merged).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("Damaged merged original XI source SQLite")
        config=dict(conn.execute("SELECT key,value FROM config"))
        for key,want in (("region","XI"),("as_of",asof.isoformat()),
                         ("catalogue_sha256",m["catalogue_sha256"]),
                         ("production_eligible","false")):
            if config.get(key)!=want:
                raise ValueError("Merged original XI source provenance mismatch: "+key)
        records=conn.execute("SELECT COUNT(*) FROM requests WHERE status='ok'").fetchone()[0]
        if records!=a["expected"]:
            raise ValueError("Accepted raw source request count differs")
        for code in CANARIES:
            found=conn.execute("SELECT response_sha256,status FROM requests WHERE code=?",
                               (code,)).fetchone()
            if not found or found[1]!="ok" or not HEX.fullmatch(str(found[0])):
                raise ValueError("Selected XI canary absent/unverified: "+code)
            signatures[code]=found[0]
        measure_count=0
        for start,end in conn.execute(
                "SELECT effective_start,effective_end FROM measures "
                "WHERE measure_type IN ('103','105')"):
            begin=date.fromisoformat(start[:10]) if start else None
            finish=date.fromisoformat(end[:10]) if end else None
            if begin and begin>asof or finish and finish<asof:
                continue
            measure_count+=1
            if finish:
                ends[finish.isoformat()]+=1
    receipt={
      "kind":"xi-daily-source-canary-baseline-v1",
      "source_run_id":run_id,
      "source_as_of":asof.isoformat(),
      "catalogue_sha256":m["catalogue_sha256"],
      "source_merged_db_sha256":_digest(merged),
      "original_codes_verified":a["expected"],
      "third_country_source_candidates":measure_count,
      "canary_response_sha256":signatures,
      "known_103_105_end_dates":dict(sorted(ends.items())),
      "ni_other_verified":False,
      "production_eligible":False,
      "caveat":"Only currently enumerated type 103/105 date ends; not future starts, other import measures, legal applicability or total changes."
    }
    dest=Path(output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--merged",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--acceptance",required=True)
    p.add_argument("--run-id",type=int,required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    print(json.dumps(build(a.merged,a.manifest,a.acceptance,run_id=a.run_id,
                           output=a.output),indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
