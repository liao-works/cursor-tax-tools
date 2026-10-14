"""Freeze and verify a managed XI nomenclature snapshot across independent runs.

A byte-identical original catalogue is mandatory: XI shard indices, checkpoints
and merge hashes must NEVER be calculated from a freshly regenerated SQLite.
This module creates and validates a SHA-256-locked source-review manifest.
It does not authenticate HMRC or grant production eligibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_measure_crawl import candidates

SHA = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_KIND = "xi-managed-frozen-catalogue-review-v1"


def digest_file(path: str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def inspect(catalogue: str, *, min_chapters: int = 90, required_day: str | None = None) -> dict:
    if not 1 <= min_chapters <= 99:
        raise ValueError("Invalid XI minimum chapters")
    path = Path(catalogue)
    if not path.is_file():
        raise ValueError("Original XI catalogue database is missing")
    db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Frozen XI catalogue failed SQLite integrity check")
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("production_eligible") != "false":
            raise ValueError("XI catalogue is ineligible for production release")
        if metadata.get("listing_scope") != "managed_full":
            raise ValueError("XI catalogue is not a managed full enumeration")
        day = date.fromisoformat(metadata["as_of"]).isoformat()
        if required_day and day != date.fromisoformat(required_day).isoformat():
            raise ValueError("Frozen XI catalogue as-of day mismatch")
        chapter_rows = db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        official_count = int(metadata.get("listed_chapters", "-1"))
        if chapter_rows < min_chapters or chapter_rows != official_count:
            raise ValueError("Frozen XI catalogue does not cover the full official chapter list")
        orphan_count = db.execute("""
          SELECT COUNT(*) FROM nomenclature n LEFT JOIN chapters c
             ON n.chapter=c.chapter WHERE c.chapter IS NULL
        """).fetchone()[0]
        if orphan_count:
            raise ValueError("XI nomenclature references unknown chapters")
        unknown = db.execute("""
          SELECT COUNT(*) FROM nomenclature
          WHERE active=1 AND declarable IS NULL
        """).fetchone()[0]
        if unknown:
            raise ValueError("Active XI commodity declarability unknown")
        malformed = db.execute("""
          SELECT COUNT(*) FROM nomenclature
          WHERE length(code)!=10 OR code NOT GLOB '[0-9]*'
            OR substr(code,1,2) != chapter
        """).fetchone()[0]
        if malformed:
            raise ValueError("Invalid XI catalogue code or chapter relationship")
        all_rows = db.execute("SELECT COUNT(*) FROM nomenclature").fetchone()[0]
        if not all_rows:
            raise ValueError("XI catalogue has no nomenclature records")
    finally:
        db.close()
    codes = candidates(catalogue)
    return {
        "kind": MANIFEST_KIND, "as_of": day,
        "listing_scope": "managed_full", "official_chapters": official_count,
        "nomenclature_rows": all_rows, "active_declarable_codes": len(codes),
        "catalogue_sha256": digest_file(catalogue),
        "production_eligible": False,
        "other_semantics_verified": False,
        "atomic_official_snapshot_verified": False,
    }


def freeze(catalogue: str, manifest_path: str, *, min_chapters: int = 90) -> dict:
    info = inspect(catalogue, min_chapters=min_chapters)
    info["frozen_at"] = datetime.now(timezone.utc).isoformat()
    dest = Path(manifest_path)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(info,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return info


def verify(catalogue: str, manifest_path: str, *, day: str | None = None,
           min_chapters: int = 90) -> dict:
    manifest=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if (not isinstance(manifest,dict) or manifest.get("kind")!=MANIFEST_KIND
        or manifest.get("production_eligible") is not False
        or manifest.get("other_semantics_verified") is not False
        or manifest.get("atomic_official_snapshot_verified") is not False):
        raise ValueError("Unrecognized or unsafe XI catalogue manifest")
    fingerprint=manifest.get("catalogue_sha256")
    if not isinstance(fingerprint,str) or not SHA.fullmatch(fingerprint):
        raise ValueError("Frozen XI catalogue fingerprint is malformed")
    # Reject stale/changed bytes before trusting any metadata.
    if digest_file(catalogue)!=fingerprint:
        raise ValueError("Frozen XI catalogue raw SHA-256 mismatch")
    info=inspect(catalogue,min_chapters=min_chapters,required_day=day)
    for key in (
        "as_of","listing_scope","official_chapters","nomenclature_rows",
        "active_declarable_codes","catalogue_sha256"
    ):
        if manifest.get(key)!=info[key]:
            raise ValueError("XI catalogue manifest differs at "+key)
    return info


def shard_plan(catalogue: str, manifest: str, *, size: int=100,
               start: int=0) -> dict:
    validated=verify(catalogue,manifest)
    total=validated["active_declarable_codes"]
    if not 1 <= size <= 1000 or not 0 <= start < total:
        raise ValueError("Invalid XI fixed shard bounds")
    end=min(total,start+size)
    code_list=candidates(catalogue)
    return {
        "kind":"xi-official-fixed-shard-plan-v1",
        "catalogue_sha256":validated["catalogue_sha256"],
        "as_of":validated["as_of"],
        "start_index":start,"end_index_exclusive":end,
        "limit":end-start,
        "first_code":code_list[start],"last_code":code_list[end-1],
        "catalogue_total_codes":total,
        "remaining_after_shard":total-end,
        "production_eligible":False,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    cmds=parser.add_subparsers(dest="command",required=True)
    for name in ("freeze","verify","plan"):
        sub=cmds.add_parser(name)
        sub.add_argument("--catalogue",required=True)
        sub.add_argument("--manifest",required=True)
        sub.add_argument("--min-chapters",type=int,default=90)
        if name=="verify":
            sub.add_argument("--as-of")
        if name=="plan":
            sub.add_argument("--start-index",type=int,required=True)
            sub.add_argument("--limit",type=int,default=100)
            sub.add_argument("--output")
    args=parser.parse_args()
    if args.command=="freeze":
        result=freeze(args.catalogue,args.manifest,min_chapters=args.min_chapters)
    elif args.command=="verify":
        result=verify(args.catalogue,args.manifest,day=args.as_of,
                      min_chapters=args.min_chapters)
    else:
        result=shard_plan(args.catalogue,args.manifest,
                          size=args.limit,start=args.start_index)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
