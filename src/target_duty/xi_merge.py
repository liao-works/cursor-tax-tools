"""Merge XI commodity-measure crawl shards with independent raw source replay.

This is the persistent-checkpoint *assembly* step for approved managed API
batches. Shards must share exact XI catalogue SHA, as_of, schema, and region.
Every successful response is decompressed, SHA-256 verified and re-parsed
before merge. Conflicting overlapping shards abort atomically.

Never certifies NI Other, does not publish or alter desktop tariffs.db.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from .xi_measure_crawl import SCHEMA, candidates, initialize, parse
from .xi_acceptance import decompress_checked


def _readonly(path: str) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise ValueError(f"Missing XI shard: {path}")
    return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)


def combine(catalogue: str, sources: list[str], output: str, *,
            as_of: date) -> dict:
    if not 1 <= len(sources) <= 100:
        raise ValueError("Merge requires between 1 and 100 explicit XI shards")
    paths=[Path(s).resolve() for s in sources]
    if len(paths)!=len(set(paths)):
        raise ValueError("Duplicate XI shard paths")
    out=Path(output).resolve()
    if out in paths or out==Path(catalogue).resolve():
        raise ValueError("XI merge output cannot overwrite inputs or catalogue")
    expected=set(candidates(catalogue))
    cat_hash=hashlib.sha256(Path(catalogue).read_bytes()).hexdigest()
    out.parent.mkdir(parents=True,exist_ok=True)
    accepted={}
    counts={"success":0,"source_error":0,"duplicates_identical":0}
    with tempfile.TemporaryDirectory(dir=out.parent) as folder:
        stage=Path(folder)/"xi-combined.db"
        db=sqlite3.connect(stage)
        try:
            initialize(db,as_of,cat_hash)
            for path in paths:
                src=_readonly(str(path))
                try:
                    if src.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                        raise ValueError(f"Damaged XI shard database: {path.name}")
                    config=dict(src.execute("SELECT key,value FROM config"))
                    for key,value in {
                        "region":"XI","as_of":as_of.isoformat(),
                        "catalogue_sha256":cat_hash,
                        "production_eligible":"false",
                        "other_semantics_verified":"false"
                    }.items():
                        if config.get(key)!=value:
                            raise ValueError(f"XI shard metadata mismatch for {key}: {path.name}")
                    for code,status,digest,size,checked,error,body in src.execute(
                        """SELECT code,status,response_sha256,bytes,checked_at,error,
                                  compressed_body FROM requests ORDER BY code"""):
                        if code not in expected:
                            raise ValueError(f"Unexpected XI code outside official catalogue: {code}")
                        row=(code,status,digest,size,checked,error,body)
                        rows=list(src.execute(
                            """SELECT ordinal,measure_id,measure_type,geography_id,
                                      duty_expression,effective_start,effective_end,raw_json
                               FROM measures WHERE code=? ORDER BY ordinal""",(code,)))
                        # Verify EVERY shard, including overlapping rows, before
                        # deduplication. Otherwise a tampered duplicate can bypass
                        # exact replay of its persisted measure records.
                        if status=="ok":
                            original=decompress_checked(body)
                            if size!=len(original) or hashlib.sha256(original).hexdigest()!=digest:
                                raise ValueError(f"XI shard source fingerprint mismatch: {code}")
                            verified=parse(original,code)
                            compare=[(i,m["measure_id"],m["measure_type"],m["geography_id"],
                                      m["duty_expression"],m["effective_start"],
                                      m["effective_end"],m["raw_json"])
                                     for i,m in enumerate(verified)]
                            if rows!=compare:
                                raise ValueError(f"XI shard measure rows differ from official bytes: {code}")
                        elif status=="source_error":
                            if rows:
                                raise ValueError(f"Failed XI source request has measure rows: {code}")
                        else:
                            raise ValueError(f"Unsupported XI source request status: {status}")
                        # A compact evidence fingerprint avoids retaining every
                        # compressed response in Python RAM during large merges.
                        signature=(status,digest,size,error,
                                   hashlib.sha256(body).hexdigest() if body else None)
                        if code in accepted:
                            if signature!=accepted[code]:
                                raise ValueError(f"Conflicting XI shard responses: {code}")
                            counts["duplicates_identical"]+=1
                            continue
                        accepted[code]=signature
                        counts["success" if status=="ok" else "source_error"]+=1
                        db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)",row)
                        db.executemany("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?)",
                                       [(code,*m) for m in rows])
                    db.commit()
                finally:
                    src.close()
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("XI merged database failed integrity check")
            check=db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            if check != len(accepted):
                raise ValueError("XI merged source records lost")
        finally:
            db.close()
        stage.replace(out)
    summary={
        "kind":"xi-merged-source-checkpoint-v1",
        "production_eligible":False,
        "other_semantics_verified":False,
        "atomic_official_snapshot_verified":False,
        "as_of":as_of.isoformat(),
        "catalogue_sha256":cat_hash,
        "source_shards":[{"filename":p.name,
                          "sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in paths],
        "catalogue_candidate_codes":len(expected),
        "merged_requests":len(accepted),
        "successful_codes":counts["success"],
        "failed_codes":counts["source_error"],
        "duplicate_identical_codes":counts["duplicates_identical"],
        "remaining_codes":len(expected)-counts["success"],
        "source_complete":counts["success"]==len(expected)
                          and counts["source_error"]==0,
        "sqlite_sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
        "warning":"Merged data are unofficially assembled XI source evidence, NOT NI Other duty.",
    }
    out.with_suffix(".json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--shards",nargs="+",required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--output",default="xi-merged-shadow.db")
    args=p.parse_args()
    result=combine(args.catalogue,args.shards,args.output,
                   as_of=date.fromisoformat(args.as_of))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
