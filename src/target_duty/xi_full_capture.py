"""Managed XI full-source capture on durable local storage (evidence only).

Runs bounded batches until every original active/declarable XI code has a replay-
verified official commodity response, or a strictly bounded stop condition occurs.
No NI Other rate derivation, production writes, or implicit snapshot publication.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .xi_acceptance import evaluate
from .xi_drift_guard import audit as drift_audit
from .xi_frozen_catalogue import verify
from .xi_measure_crawl import collect, managed_fetcher
from .xi_run_plan import build_plan


def _write_report(path: Path, data: dict) -> None:
    """Atomic status receipt, never a release manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + ".tmp")
    stage.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    stage.replace(path)


def run(catalogue: str, manifest: str, checkpoint: str, *,
        as_of: date, fetcher: Callable[[str, date], bytes],
        batch_size: int = 500, max_batches: int = 50,
        delay: float = 2.0, drift_delay: float = 2.0,
        report_path: str | None = None) -> dict:
    """Replay, source-drift check, crawl, repeat; independently audit at end.

    A resumable checkpoint is the only mutable source evidence. Never use a
    production desktop DB as output. No attempt is made to forge atomic source
    guarantees from date-pinned, independently timed API responses.
    """
    if not 1 <= batch_size <= 1000 or not 1 <= max_batches <= 1000:
        raise ValueError("Invalid bounded managed XI capture configuration")
    if not 0 <= delay <= 120 or not 0 <= drift_delay <= 120:
        raise ValueError("Invalid HMRC request spacing")
    paths = [Path(p).resolve() for p in (catalogue, manifest, checkpoint)]
    if len(set(paths)) != len(paths):
        raise ValueError("XI checkpoint/catalogue/manifest must be distinct")
    if paths[2].name in {"tariffs.db", "latest-data.db"}:
        raise ValueError("Refusing to overwrite a desktop production tariff DB")
    verify(catalogue, manifest, day=as_of.isoformat())
    target = paths[2]
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = Path(report_path) if report_path else target.with_suffix(".full-coverage.json")
    if receipt.resolve() in paths:
        raise ValueError("Receipt would overwrite XI evidence")
    batches = []
    last_plan = None
    for number in range(1, max_batches + 1):
        plan = build_plan(catalogue, manifest=manifest,
                          checkpoint=str(target) if target.exists() else None,
                          batch_size=batch_size, retry_failed_first=True)
        last_plan = plan
        if plan["all_managed_source_responses_verified"]:
            break
        shard = plan["next_shard"]
        if shard is None:
            raise ValueError("Incomplete XI catalogue unexpectedly has no next shard")
        if target.exists() and plan["verified_successful_codes"]:
            drift = drift_audit(
                catalogue, str(target), as_of=as_of, manifest=manifest,
                fetcher=fetcher, count=3, sleep_seconds=drift_delay)
            if not drift["resume_source_stability_gate_pass"]:
                _write_report(receipt, {
                    "kind": "xi-managed-full-capture-progress-v1",
                    "status": "halted_source_drift", "as_of": as_of.isoformat(),
                    "plan": plan, "drift": drift,
                    "production_eligible": False, "ni_other_verified": False})
                raise ValueError("XI historical source changed; create a NEW frozen snapshot")
        before = plan["verified_successful_codes"]
        result = collect(catalogue, str(target), as_of=as_of,
                         start_index=shard["start_index"], limit=shard["limit"],
                         fetcher=fetcher, sleep_seconds=delay)
        # Complete raw replay on every batch, including stored source errors.
        audit = evaluate(catalogue, str(target), min_chapters=90)
        after = audit["requests_recorded"] - audit["request_failures"]
        if after < before:
            raise ValueError("XI verified successful source coverage regressed")
        batches.append({
            "batch": number, "planned_index": shard["start_index"],
            "attempted": result["attempted_this_run"],
            "newly_verified": after-before,
            "verified_total": after, "pending": audit["missing_codes"],
            "errors": audit["request_failures"]})
        progress = {
            "kind": "xi-managed-full-capture-progress-v1",
            "status": "collecting", "as_of": as_of.isoformat(),
            "verified": after, "expected": audit["active_declarable_codes"],
            "missing": audit["missing_codes"],
            "source_errors": audit["request_failures"],
            "completed_batches_this_invocation": len(batches),
            "last_batch": batches[-1], "production_eligible": False,
            "ni_other_verified": False,
            "atomic_official_snapshot_verified": False}
        _write_report(receipt, progress)
        # Fail with evidence, rather than an unbounded loop of 429/5xx.
        if after == before:
            progress["status"] = "halted_without_progress"
            _write_report(receipt, progress)
            return progress

    final = evaluate(catalogue, str(target), min_chapters=90)
    completed = (final["independent_catalogue_scope_ok"]
                 and final["all_collected_measure_responses_verified"]
                 and final["missing_codes"] == 0
                 and final["request_failures"] == 0)
    report = {
        "kind": "xi-managed-full-capture-progress-v1",
        "status": "source_coverage_verified_business_pending" if completed
                  else "batch_budget_exhausted",
        "as_of": as_of.isoformat(),
        "verified": final["requests_recorded"]-final["request_failures"],
        "expected": final["active_declarable_codes"],
        "missing": final["missing_codes"],
        "source_errors": final["request_failures"],
        "completed_batches_this_invocation": len(batches),
        "last_plan": last_plan,
        "chapter_coverage": final["chapter_coverage"],
        "production_eligible": False,
        "ni_other_verified": False,
        "atomic_official_snapshot_verified": False,
        "source_coverage_verified": completed,
        "warning": "Complete source responses do not certify origin-specific NI Other payable duty."}
    _write_report(receipt, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True,
                        help="Durable private directory; NEVER desktop production DB directory")
    parser.add_argument("--as-of", help="Pinned ISO date; mandatory on initial run")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=50)
    parser.add_argument("--min-interval", type=float, default=2.0)
    parser.add_argument("--drift-interval", type=float, default=2.0)
    args = parser.parse_args()
    if not os.getenv("HMRC_CLIENT_ID") or not os.getenv("HMRC_CLIENT_SECRET"):
        parser.error("Authorized HMRC_CLIENT_ID and HMRC_CLIENT_SECRET required")
    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cat, manifest = root/"xi-full-catalogue-shadow.db", root/"xi-frozen-manifest.json"
    checkpoint = root/"xi-merged-shadow.db"
    if cat.exists() != manifest.exists():
        parser.error("Partial frozen XI catalogue artifacts; recover original files")
    if not cat.exists():
        if checkpoint.exists() or not args.as_of:
            parser.error("New managed source capture requires --as-of and no checkpoint")
        day = date.fromisoformat(args.as_of)
        subprocess.run([
            sys.executable, "-m", "src.target_duty.xi_catalogue",
            "--as-of", day.isoformat(), "--min-chapter-interval", "1.2",
            "--output", str(cat)], check=True)
        subprocess.run([
            sys.executable, "-m", "src.target_duty.xi_frozen_catalogue",
            "freeze", "--catalogue", str(cat), "--manifest", str(manifest)], check=True)
    else:
        source = json.loads(manifest.read_text(encoding="utf-8"))
        day = date.fromisoformat(source["as_of"])
        if args.as_of and date.fromisoformat(args.as_of) != day:
            parser.error("Resume date differs from immutable frozen XI catalogue")
    result = run(str(cat), str(manifest), str(checkpoint), as_of=day,
                 fetcher=managed_fetcher(os.environ["HMRC_CLIENT_ID"],
                                         os.environ["HMRC_CLIENT_SECRET"]),
                 batch_size=args.batch_size, max_batches=args.max_batches,
                 delay=args.min_interval, drift_delay=args.drift_interval,
                 report_path=str(root/"xi-full-capture-progress.json"))
    print(json.dumps({key: result[key] for key in
                      ("status", "verified", "expected", "missing", "source_errors",
                       "production_eligible")}, indent=2))
    if not result.get("source_coverage_verified"):
        raise SystemExit("XI managed full capture incomplete; durable checkpoint retained")


if __name__ == "__main__":
    main()
