"""Partition and collect a frozen managed XI source snapshot.

This module exports original HMRC responses, NOT NI 'Other' duty rates.
Shards are disjoint, deterministic and refer to a byte-identical frozen
managed catalogue. They can be independently replayed then merged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .xi_acceptance import evaluate
from .xi_frozen_catalogue import verify
from .xi_measure_crawl import candidates, collect, managed_fetcher


def bounds(total: int, workers: int, index: int) -> tuple[int, int]:
    if not 1 <= workers <= 16 or total < workers:
        raise ValueError("XI shard worker count exceeds source inventory")
    if not 0 <= index < workers:
        raise ValueError("XI worker index outside partition")
    length, remainder = divmod(total, workers)
    start = index * length + min(index, remainder)
    return start, length + (index < remainder)


def make_plan(catalogue: str, manifest: str, *, workers: int = 4) -> dict:
    original = verify(catalogue, manifest)
    codes = candidates(catalogue)
    if original["active_declarable_codes"] != len(codes):
        raise ValueError("Frozen XI code count changed")
    pieces = []
    for index in range(workers):
        start, length = bounds(len(codes), workers, index)
        pieces.append({
            "index": index, "start_index": start, "limit": length,
            "first_code": codes[start], "last_code": codes[start + length - 1]})
    if sum(p["limit"] for p in pieces) != len(codes):
        raise ValueError("XI partitions omitted source codes")
    return {
        "kind": "xi-managed-full-source-partition-v1",
        "as_of": original["as_of"],
        "catalogue_sha256": original["catalogue_sha256"],
        "expected_codes": len(codes),
        "official_chapters": original["official_chapters"],
        "shards": pieces,
        "production_eligible": False,
        "ni_other_verified": False,
        "atomic_official_snapshot_verified": False,
    }


def crawl_shard(catalogue: str, manifest: str, output: str, *,
                index: int, workers: int, as_of: date,
                fetcher: Callable[[str, date], bytes],
                batch_size: int = 500, interval: float = 2.0) -> dict:
    if not 1 <= batch_size <= 1000 or not 0 <= interval <= 120:
        raise ValueError("Invalid bounded XI batch/interval")
    plan = make_plan(catalogue, manifest, workers=workers)
    if plan["as_of"] != as_of.isoformat():
        raise ValueError("XI shard as-of differs from original frozen catalogue")
    start, length = bounds(plan["expected_codes"], workers, index)
    target = Path(output).resolve()
    if target in (Path(catalogue).resolve(), Path(manifest).resolve()):
        raise ValueError("XI shard cannot replace the frozen catalogue or manifest")
    if target.name in ("tariffs.db", "latest-data.db"):
        raise ValueError("XI shard may not overwrite the desktop database")
    source_codes = candidates(catalogue)
    expected = set(source_codes[start:start + length])
    if target.is_file():
        previous = evaluate(catalogue, str(target))
        if previous["date"] != as_of.isoformat():
            raise ValueError("Incompatible preexisting XI shard")
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
            already = {code for (code,) in db.execute("SELECT code FROM requests")}
        if not already.issubset(expected):
            raise ValueError("XI shard checkpoint contains other worker's source codes")
    receipts = []
    for position in range(start, start + length, batch_size):
        part_size = min(batch_size, start + length - position)
        collected = collect(
            catalogue, str(target), as_of=as_of,
            start_index=position, limit=part_size,
            fetcher=fetcher, sleep_seconds=interval)
        receipts.append({"start_index": position, "batch_size": part_size,
                         "attempted": collected["attempted_this_run"]})
    audit = evaluate(catalogue, str(target))
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
        statuses = dict(db.execute("SELECT code,status FROM requests"))
    if not set(statuses).issubset(expected):
        raise ValueError("XI shard output contains unassigned commodity")
    success = sum(statuses.get(code) == "ok" for code in expected)
    errors = sum(statuses.get(code) == "source_error" for code in expected)
    missing = length - success - errors
    receipt = {
        "kind": "xi-managed-full-source-shard-review-v1",
        "as_of": as_of.isoformat(), "worker_index": index,
        "worker_total": workers, "start_index": start,
        "expected": length, "verified": success,
        "source_errors": errors, "missing": missing,
        "raw_measure_rows_all_collected_shards": audit["replayed_source_measures"],
        "catalogue_sha256": plan["catalogue_sha256"],
        "checkpoint_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "batches": receipts,
        "complete": success == length and errors == 0 and missing == 0,
        "production_eligible": False, "ni_other_verified": False,
        "atomic_official_snapshot_verified": False,
        "warning": "Raw XI source completeness is NOT proof of an applicable NI Other rate."
    }
    progress = target.with_suffix(".json")
    progress.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+"\n",
                        encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--catalogue", required=True)
    plan.add_argument("--manifest", required=True)
    plan.add_argument("--workers", type=int, default=4)
    plan.add_argument("--output", default="xi-partition.json")
    shard = commands.add_parser("shard")
    shard.add_argument("--catalogue", required=True)
    shard.add_argument("--manifest", required=True)
    shard.add_argument("--as-of", required=True)
    shard.add_argument("--workers", type=int, default=4)
    shard.add_argument("--index", type=int, required=True)
    shard.add_argument("--batch-size", type=int, default=500)
    shard.add_argument("--interval", type=float, default=2.0)
    shard.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        report = make_plan(args.catalogue, args.manifest, workers=args.workers)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n",
                                     encoding="utf-8")
    else:
        if not os.environ.get("HMRC_CLIENT_ID") or not os.environ.get("HMRC_CLIENT_SECRET"):
            parser.error("Authorized managed HMRC credentials are required")
        report = crawl_shard(
            args.catalogue, args.manifest, args.output,
            index=args.index, workers=args.workers,
            as_of=date.fromisoformat(args.as_of), batch_size=args.batch_size,
            interval=args.interval,
            fetcher=managed_fetcher(
                os.environ["HMRC_CLIENT_ID"], os.environ["HMRC_CLIENT_SECRET"]))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
