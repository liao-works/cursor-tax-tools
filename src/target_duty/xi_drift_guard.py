"""Detect cross-batch XI official source changes using replay-verified anchors.

This is a conservative drift *canary*, not proof of atomic snapshot consistency.
Before continuing an old as_of date across different collection runs, refetch
up to three previously successful XI commodity responses. Compare canonical
source JSON (ignores JSON object key order and included-object list order)
against the original official bytes, which are independently replayed first.

Never mutate or reinterpret prior response evidence and never infer NI Other.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import zlib
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Callable

from .xi_acceptance import decompress_checked, evaluate
from .xi_frozen_catalogue import verify, digest_file
from .xi_measure_crawl import candidates, managed_fetcher, parse

KIND="xi-cross-run-source-drift-review-v1"


def semantic_fingerprint(raw: bytes, code: str) -> str:
    """Canonical *strict source evidence* including relationships and linked data."""
    parse(raw,code)  # fail closed if official XI response is incomplete
    doc=json.loads(raw)
    included=doc.get("included",[])
    if not isinstance(included,list):
        raise ValueError("XI included relationships are not a list")
    seen=set()
    for item in included:
        if not isinstance(item,dict) or not isinstance(item.get("type"),str) or not isinstance(item.get("id"),str):
            raise ValueError("XI included official object missing type/id")
        key=(item["type"],item["id"])
        if key in seen:
            raise ValueError("Duplicate official XI included object")
        seen.add(key)
    # Use the complete original measure graph, not just the bare duty
    # expression; changes to conditions, exclusions and related data matter.
    canonical={"data":doc["data"],
               "included":sorted(included,key=lambda x:(x["type"],x["id"]))}
    return hashlib.sha256(json.dumps(
        canonical,sort_keys=True,ensure_ascii=False,
        separators=(",",":"),allow_nan=False
    ).encode("utf-8")).hexdigest()


def _anchors(successful: list[str], count: int) -> list[str]:
    if not successful or not 1 <= count <= 3:
        raise ValueError("Choose 1-3 anchored XI commodity responses")
    size=min(len(successful),count)
    if size==1:
        positions=[0]
    else:
        positions=[i*(len(successful)-1)//(size-1) for i in range(size)]
    return [successful[i] for i in positions]


def _save_evidence(path: str, catalogue: str, checkpoint: str,
                   captures: list[tuple[str,bytes,bytes|None,str]]) -> str:
    """Atomically preserve original and newly fetched official anchor bytes."""
    target=Path(path).resolve()
    if target in (Path(catalogue).resolve(),Path(checkpoint).resolve()):
        raise ValueError("XI anchor evidence must not overwrite source inputs")
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        stage=Path(folder)/"xi-drift-evidence.db"
        with sqlite3.connect(stage) as db:
            db.execute("""CREATE TABLE anchors (
                code TEXT PRIMARY KEY, outcome TEXT NOT NULL,
                old_body_sha256 TEXT NOT NULL, new_body_sha256 TEXT,
                old_compressed_body BLOB NOT NULL, new_compressed_body BLOB
            )""")
            for code,old,new,outcome in captures:
                db.execute("INSERT INTO anchors VALUES (?,?,?,?,?,?)",(
                    code,outcome,hashlib.sha256(old).hexdigest(),
                    hashlib.sha256(new).hexdigest() if new is not None else None,
                    zlib.compress(old),zlib.compress(new) if new is not None else None
                ))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("XI anchor evidence SQLite corrupted")
        os.replace(stage,target)
    return digest_file(str(target))


def audit(catalogue: str, checkpoint: str, *,
          as_of: date, fetcher: Callable[[str,date],bytes],
          manifest: str | None = None, public_canary: bool = False,
          count: int = 3, max_bytes: int = 8_000_000,
          sleep_seconds: float = 0.0,
          evidence_output: str | None = None) -> dict:
    if not 1 <= count <= 3 or not 0 <= sleep_seconds <= 120:
        raise ValueError("XI anchor limits out of bounds")
    if not 1 <= max_bytes <= 8_000_000:
        raise ValueError("Invalid XI source response bound")
    if (evidence_output and manifest and
            Path(evidence_output).resolve()==Path(manifest).resolve()):
        raise ValueError("XI anchor evidence must not overwrite frozen source manifest")
    if public_canary:
        if manifest:
            raise ValueError("Public XI canary cannot use managed manifest")
        with sqlite3.connect(Path(catalogue).resolve().as_uri()+"?mode=ro",
                             uri=True) as db:
            meta=dict(db.execute("SELECT key,value FROM metadata"))
            total=db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
            if (meta.get("listing_scope")!="partial_or_fixture"
                or meta.get("production_eligible")!="false" or total>5):
                raise ValueError("Public XI drift check requires partial <=5 chapters")
    else:
        if not manifest:
            raise ValueError("Managed XI drift audit requires frozen source manifest")
        verify(catalogue,manifest,day=as_of.isoformat())
    # This also verifies checkpoint region, as_of, catalogue SHA, all raw
    # bytes and every stored measure. No fetch if the checkpoint is corrupt.
    replay=evaluate(catalogue,checkpoint,
                    min_chapters=1 if public_canary else 90)
    if replay["date"]!=as_of.isoformat():
        raise ValueError("XI drift audit source as_of differs")
    valid=set(candidates(catalogue))
    with sqlite3.connect(Path(checkpoint).resolve().as_uri()+"?mode=ro",
                         uri=True) as db:
        ok=[code for (code,) in db.execute(
            "SELECT code FROM requests WHERE status='ok' ORDER BY code")]
        if set(ok)-valid:
            raise ValueError("XI drift audit checkpoint outside frozen catalogue")
        selected=_anchors(ok,count)
        baselines={code:db.execute(
            "SELECT compressed_body,response_sha256 FROM requests WHERE code=?",
            (code,)).fetchone() for code in selected}
    rows=[]
    captures=[]
    for idx,code in enumerate(selected):
        compressed,sha=baselines[code]
        baseline=decompress_checked(compressed)
        old_semantic=semantic_fingerprint(baseline,code)
        new_raw_sha=None
        new_semantic=None
        captured_new=None
        try:
            now=fetcher(code,as_of)
            if not isinstance(now,bytes) or not now or len(now)>max_bytes:
                raise ValueError("XI anchor fetch returned invalid or oversized body")
            captured_new=now
            new_raw_sha=hashlib.sha256(now).hexdigest()
            new_semantic=semantic_fingerprint(now,code)
            stable=(old_semantic==new_semantic)
            status="stable" if stable else "source_drift_detected"
            error=None
        except Exception as exc:
            status="source_fetch_or_parse_error"
            error=type(exc).__name__+": "+str(exc)[:180]
        captures.append((code,baseline,captured_new,status))
        rows.append({
            "code":code,
            "status":status,
            "old_raw_sha256":sha,
            "new_raw_sha256":new_raw_sha,
            "old_semantic_sha256":old_semantic,
            "new_semantic_sha256":new_semantic,
            "error":error,
        })
        if idx+1<len(selected) and sleep_seconds:
            time.sleep(sleep_seconds)
    evidence_sha=_save_evidence(evidence_output,catalogue,checkpoint,captures) if evidence_output else None
    changed=[r["code"] for r in rows if r["status"]=="source_drift_detected"]
    errors=[r["code"] for r in rows if r["status"]=="source_fetch_or_parse_error"]
    return {
        "kind":KIND,
        "production_eligible":False,
        "ni_other_verified":False,
        "atomic_official_snapshot_verified":False,
        "as_of":as_of.isoformat(),
        "source_scope":"partial_or_fixture" if public_canary else "managed_full",
        "catalogue_sha256":digest_file(catalogue),
        "checkpoint_sha256":digest_file(checkpoint),
        "previous_successful_codes":len(ok),
        "anchor_count":len(rows),
        "raw_anchor_evidence_sqlite_sha256":evidence_sha,
        "changed_anchor_codes":changed,
        "failed_anchor_codes":errors,
        "resume_source_stability_gate_pass":not changed and not errors,
        "anchors":rows,
        "warning":(
            "Stable sampled responses DO NOT prove an atomic full XI snapshot; "
            "source drift or fetch errors must BLOCK resuming old evidence."
        ),
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--checkpoint",required=True)
    p.add_argument("--manifest")
    p.add_argument("--as-of",required=True)
    p.add_argument("--count",type=int,default=3)
    p.add_argument("--min-interval",type=float,default=1.5)
    p.add_argument("--public-canary",action="store_true")
    p.add_argument("--require-stable",action="store_true")
    p.add_argument("--output",default="xi-source-drift-review.json")
    p.add_argument("--evidence-db",help="Optional audit-only SQLite holding exact original and refetched anchor responses")
    args=p.parse_args()
    fetch=managed_fetcher(os.getenv("HMRC_CLIENT_ID",""),
                          os.getenv("HMRC_CLIENT_SECRET",""),
                          public=args.public_canary)
    result=audit(args.catalogue,args.checkpoint,
                 as_of=date.fromisoformat(args.as_of),
                 manifest=args.manifest,count=args.count,
                 public_canary=args.public_canary,fetcher=fetch,
                 sleep_seconds=args.min_interval,
                 evidence_output=args.evidence_db)
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        stage=Path(folder)/"drift.json"
        stage.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                         encoding="utf-8")
        os.replace(stage,output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.require_stable and not result["resume_source_stability_gate_pass"]:
        raise SystemExit("XI anchor drift or fetch failure: old source snapshot cannot be safely resumed")


if __name__=="__main__":
    main()
