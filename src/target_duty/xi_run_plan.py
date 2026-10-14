"""Plan the next XI managed import batch from a frozen catalogue + verified checkpoint.

Fail-closed, *read-only* source coverage planning. A shard index is never
advanced by assumption: previous official responses are replayed, their
original checksum and stored measures checked, and every missing or failed
code remains pending. This is NOT an XI 'Other' rate or product release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from .xi_acceptance import evaluate
from .xi_frozen_catalogue import verify
from .xi_measure_crawl import candidates

SAFE_STATUSES = {"ok", "source_error"}


def _read(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise ValueError("XI evidence file missing: " + str(path))
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)


def build_plan(catalogue: str, *, manifest: str | None = None,
               checkpoint: str | None = None, batch_size: int = 100,
               start_index: int | None = None, canary: bool = False) -> dict:
    if not 1 <= batch_size <= 1000:
        raise ValueError("XI batch size must be between 1 and 1000")
    if canary:
        if manifest:
            raise ValueError("Public canary cannot impersonate managed frozen manifest")
        with _read(catalogue) as db:
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
            chapters = db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        if metadata.get("listing_scope") != "partial_or_fixture":
            raise ValueError("Canary planner requires explicitly partial catalogue")
        if metadata.get("production_eligible") != "false" or chapters > 5:
            raise ValueError("Unsafe XI public canary scope")
        pinned_sha = hashlib.sha256(Path(catalogue).read_bytes()).hexdigest()
        source_scope = "partial_or_fixture"
    else:
        if not manifest:
            raise ValueError("Full XI managed planner requires original frozen manifest")
        frozen = verify(catalogue, manifest)
        pinned_sha = frozen["catalogue_sha256"]
        chapters = frozen["official_chapters"]
        source_scope = "managed_full"

    codes = candidates(catalogue)
    statuses: dict[str, str] = {}
    checkpoint_sha = None
    replay = None
    if checkpoint:
        with _read(checkpoint) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("XI prior checkpoint SQLite integrity failed")
            for code, status in db.execute("SELECT code,status FROM requests"):
                if code in statuses or status not in SAFE_STATUSES:
                    raise ValueError("XI prior checkpoint has duplicate/unsupported request state")
                statuses[code] = status
        if set(statuses) - set(codes):
            raise ValueError("XI prior checkpoint contains commodities outside source catalogue")
        # Replay official stored compressed responses against persisted measures
        # before deciding to skip any successfully captured code.
        replay = evaluate(catalogue, checkpoint,
                          min_chapters=1 if canary else 90)
        if (replay["date"] == "" or
            replay["listing_scope"] != source_scope or
            replay["active_declarable_codes"] != len(codes) or
            replay["requests_recorded"] != len(statuses)):
            raise ValueError("XI source replay mismatch against original inventory")
        checkpoint_sha = hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()

    pending = [i for i, code in enumerate(codes) if statuses.get(code) != "ok"]
    failed = [i for i in pending if statuses.get(codes[i]) == "source_error"]
    missing = [i for i in pending if codes[i] not in statuses]
    if start_index is not None and not 0 <= start_index < len(codes):
        raise ValueError("Requested XI shard index outside official catalogue")
    first = start_index if start_index is not None else (pending[0] if pending else None)
    if first is None:
        next_shard = None
    else:
        end = min(len(codes), first+batch_size)
        shard_pending = [i for i in pending if first <= i < end]
        next_shard = {
            "start_index": first,
            "end_index_exclusive": end,
            "limit": end-first,
            "first_code": codes[first],
            "last_code": codes[end-1],
            "pending_codes_in_window": len(shard_pending),
            "failed_codes_in_window": sum(i in failed for i in shard_pending),
            "manual_start_override": start_index is not None,
        }
        if not shard_pending:
            raise ValueError("Selected XI shard contains no pending source requests")
    full = source_scope == "managed_full" and not pending
    return {
        "kind": "xi-next-managed-source-shard-plan-v1",
        "production_eligible": False,
        "ni_other_verified": False,
        "atomic_official_snapshot_verified": False,
        "source_scope": source_scope,
        "catalogue_sha256": pinned_sha,
        "checkpoint_sha256": checkpoint_sha,
        "official_chapters_collected": chapters,
        "official_active_declarable_codes": len(codes),
        "verified_successful_codes": len(codes)-len(pending),
        "failed_requests": len(failed),
        "missing_requests": len(missing),
        "remaining_codes": len(pending),
        "coverage_percent": round(100*(len(codes)-len(pending))/len(codes), 4),
        "all_managed_source_responses_verified": full,
        "previous_checkpoint_replayed": bool(replay),
        "next_shard": next_shard,
        "next_step": "source_collection_complete_but_business_unverified" if full
                     else "collect_remaining_or_retry_failed",
        "warning": "Source coverage is not a verified NI Other or payable duty rate.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue", required=True)
    p.add_argument("--manifest", help="Original byte-identical managed XI frozen manifest")
    p.add_argument("--checkpoint", help="Prior merged XI checkpoint to replay-verify")
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--start-index", type=int, help="Optional explicit shard override")
    p.add_argument("--public-canary", action="store_true",
                   help="Only for small public/fixture partial catalogues; never production")
    p.add_argument("--output", default="xi-next-shard-plan.json")
    args = p.parse_args()
    result = build_plan(args.catalogue, manifest=args.manifest,
                        checkpoint=args.checkpoint,batch_size=args.batch_size,
                        start_index=args.start_index,canary=args.public_canary)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                    encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
