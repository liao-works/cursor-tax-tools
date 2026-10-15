"""Bounded public XI whole-inventory raw response capture; NON managed evidence.

Exact original public catalogue SHA and all raw response bytes are replayed.
This MUST NEVER label public data managed or NI 'Other' duty as certified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_measure_crawl import candidates, collect, managed_fetcher
from .xi_parallel_capture import bounds
from .xi_acceptance import evaluate

KIND = "xi-public-full-catalogue-frozen-review-v1"


def inspect_public(catalogue: str) -> dict:
    p = Path(catalogue)
    if not p.is_file():
        raise ValueError("Public XI catalogue missing")
    with sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Public XI catalogue SQLite integrity failure")
        meta = dict(db.execute("SELECT key,value FROM metadata"))
        count = db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        unknown = db.execute(
            "SELECT COUNT(*) FROM nomenclature WHERE active=1 AND declarable IS NULL").fetchone()[0]
        if (meta.get("listing_scope") != "public_full_untrusted"
                or meta.get("production_eligible") != "false"
                or not 90 <= count <= 99
                or str(count) != meta.get("listed_chapters")
                or unknown):
            raise ValueError("XI public full inventory incomplete or unsafe")
        day = date.fromisoformat(meta["as_of"]).isoformat()
    codes = candidates(catalogue)
    return {
        "kind": KIND,
        "as_of": day,
        "scope": "public_full_untrusted",
        "official_chapters": count,
        "active_declarable_codes": len(codes),
        "catalogue_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "production_eligible": False,
        "managed_source_verified": False,
        "ni_other_verified": False,
        "atomic_official_snapshot_verified": False}


def freeze_public(catalogue: str, manifest: str) -> dict:
    info = inspect_public(catalogue)
    target = Path(manifest)
    if target.resolve() == Path(catalogue).resolve():
        raise ValueError("Manifest cannot overwrite catalogue")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return info


def verify_public(catalogue: str, manifest: str) -> dict:
    receipt = json.loads(Path(manifest).read_text(encoding="utf-8"))
    info = inspect_public(catalogue)
    if receipt != info:
        raise ValueError("Frozen public XI catalogue SHA/scope/metadata mismatch")
    return info


def collect_partition(catalogue: str, manifest: str, output: str, *,
                      worker: int, workers: int = 4,
                      batch_size: int = 250, interval: float = 2.0,
                      fetcher=None) -> dict:
    if not 1 <= batch_size <= 1000 or not 0 <= interval <= 120:
        raise ValueError("Invalid bounded public XI request configuration")
    info = verify_public(catalogue, manifest)
    start, length = bounds(info["active_declarable_codes"], workers, worker)
    path = Path(output)
    if path.resolve() in (Path(catalogue).resolve(), Path(manifest).resolve()):
        raise ValueError("Public response output aliases original evidence")
    if path.name in ("tariffs.db", "latest-data.db"):
        raise ValueError("Cannot overwrite desktop tariff DB")
    codes = candidates(catalogue)
    assigned = set(codes[start:start + length])
    if path.is_file():
        evaluate(catalogue, str(path), min_chapters=90)
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            existing = {code for (code,) in db.execute("SELECT code FROM requests")}
        if not existing.issubset(assigned):
            raise ValueError("Checkpoint includes other public XI worker's codes")
    if fetcher is None:
        fetcher = managed_fetcher("", "", public=True)
    attempts = 0
    for position in range(start, start + length, batch_size):
        report = collect(catalogue, str(path), as_of=date.fromisoformat(info["as_of"]),
                         limit=min(batch_size, start + length - position),
                         start_index=position, fetcher=fetcher,
                         sleep_seconds=interval)
        attempts += report["attempted_this_run"]
        if report["failed_codes"]:
            # Preserve the error checkpoint immediately; retry from same shard
            # on later execution, never skip a failed source response.
            break
    replay = evaluate(catalogue, str(path), min_chapters=90)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        statuses = dict(db.execute("SELECT code,status FROM requests"))
    if not set(statuses).issubset(assigned):
        raise ValueError("Public XI shard contains off-partition codes")
    ok = sum(statuses.get(code) == "ok" for code in assigned)
    failed = sum(statuses.get(code) == "source_error" for code in assigned)
    summary = {
        "kind": "xi-public-full-raw-response-partition-review-v1",
        "scope": "public_full_untrusted",
        "worker": worker, "workers": workers, "as_of": info["as_of"],
        "start_index": start, "expected": length, "successful": ok,
        "failed": failed, "missing": length-ok-failed,
        "attempted_this_run": attempts,
        "catalogue_sha256": info["catalogue_sha256"],
        "shard_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_responses_replayed": replay["requests_recorded"] - replay["request_failures"],
        "complete": ok == length and failed == 0,
        "production_eligible": False, "managed_source_verified": False,
        "ni_other_verified": False, "atomic_official_snapshot_verified": False,
    }
    path.with_suffix(".public-review.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def public_acceptance(catalogue: str, manifest: str, merged: str) -> dict:
    source = verify_public(catalogue, manifest)
    audit = evaluate(catalogue, merged, min_chapters=90)
    complete = (audit["listing_scope"] == "public_full_untrusted"
                and audit["official_chapters_collected"] == source["official_chapters"]
                and audit["active_declarable_codes"] == source["active_declarable_codes"]
                and audit["all_collected_measure_responses_verified"]
                and audit["missing_codes"] == 0 and audit["request_failures"] == 0)
    return {
        "kind": "xi-public-full-source-acceptance-review-v1",
        "public_full_raw_source_coverage": complete,
        "as_of": source["as_of"],
        "official_chapters": source["official_chapters"],
        "expected": source["active_declarable_codes"],
        "responses_replayed": audit["requests_recorded"] - audit["request_failures"],
        "missing": audit["missing_codes"],
        "failed": audit["request_failures"],
        "source_measure_rows": audit["replayed_source_measures"],
        "managed_full_source_coverage_proven": False,
        "production_eligible": False,
        "ni_other_verified": False,
        "atomic_official_snapshot_verified": False,
        "warning": "Public XI raw API completeness is NOT managed OAuth provenance or NI Other payable duty."
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    f=sub.add_parser("freeze")
    f.add_argument("--catalogue", required=True)
    f.add_argument("--manifest", required=True)
    c=sub.add_parser("shard")
    c.add_argument("--catalogue", required=True)
    c.add_argument("--manifest", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--worker", type=int, required=True)
    c.add_argument("--workers", type=int, default=4)
    c.add_argument("--batch-size", type=int, default=250)
    c.add_argument("--interval", type=float, default=2.0)
    a=sub.add_parser("accept")
    a.add_argument("--catalogue", required=True)
    a.add_argument("--manifest", required=True)
    a.add_argument("--merged", required=True)
    a.add_argument("--output", default="xi-public-full-acceptance.json")
    args=p.parse_args()
    if args.action=="freeze":
        result=freeze_public(args.catalogue,args.manifest)
    elif args.action=="shard":
        result=collect_partition(args.catalogue,args.manifest,args.output,
                                 worker=args.worker,workers=args.workers,
                                 batch_size=args.batch_size,interval=args.interval)
    else:
        result=public_acceptance(args.catalogue,args.manifest,args.merged)
        Path(args.output).write_text(
            json.dumps(result,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.action=="accept" and not result["public_full_raw_source_coverage"]:
        raise SystemExit("XI public raw source still incomplete; all evidence retained")


if __name__=="__main__":
    main()
