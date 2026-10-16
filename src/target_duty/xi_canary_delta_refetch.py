"""Fetch only independently signaled XI public canary changes (READ-ONLY).

Canary deltas are partial detection evidence, not all XI source changes, HMRC
managed OAuth provenance, or legally payable NI Other duties.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_measure_crawl import managed_fetcher, parse

_CODE = re.compile(r"[0-9]{10}\Z")


def review(signal_path: str, output_db: str, output_report: str, *, fetcher=None) -> dict:
    paths = [Path(p).resolve() for p in (signal_path, output_db, output_report)]
    if len(set(paths)) != len(paths):
        raise ValueError("Never overwrite official source change evidence")
    signal = json.loads(paths[0].read_text(encoding="utf-8"))
    if (signal.get("kind") != "gb-ni-daily-official-change-signal-v1"
            or signal.get("release_permitted") is not False
            or signal.get("production_database_mutated") is not False
            or signal.get("xi_sample_covers_entire_source") is not False):
        raise ValueError("Untrusted daily source-only signal")
    day = date.fromisoformat(signal["checked_as_of"])
    codes = signal.get("xi_changed_raw_response_codes")
    if (not isinstance(codes, list) or len(codes) > 30
            or len(set(codes)) != len(codes)
            or any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes)):
        raise ValueError("Invalid bounded canary change set")
    # A GB schema outage is independent of XI; an XI source error blocks
    # claiming that today's limited canary scan has a complete signal set.
    errors = signal.get("source_errors", {})
    if not isinstance(errors, dict) or any(k != "GB" for k in errors):
        raise ValueError("Missing/failed XI canary response: review needed")
    if codes and (fetcher is None):
        fetcher = managed_fetcher("", "", public=True)
    rows, outcomes = [], []
    for code in sorted(codes):
        try:
            raw = fetcher(code, day)
            if not isinstance(raw, bytes) or not 0 < len(raw) <= 8_000_000:
                raise ValueError("Invalid size for public XI source response")
            # Replays original JSON:API identity and full measure relationships.
            measures = parse(raw, code)
            digest = hashlib.sha256(raw).hexdigest()
            rows.append((code, day.isoformat(), digest, len(raw), zlib.compress(raw, 6)))
            outcomes.append({"code":code, "status":"ok", "sha256":digest,
                             "original_measures":len(measures)})
        except Exception as exc:
            outcomes.append({"code":code, "status":"source_error",
                             "error":type(exc).__name__ + ": " + str(exc)[:230]})
    failures = len(codes) - len(rows)
    report = {
        "kind":"xi-daily-changed-canary-source-replay-v1",
        "source_signal_sha256":hashlib.sha256(paths[0].read_bytes()).hexdigest(),
        "as_of":day.isoformat(),
        "expected_changed_codes":len(codes),
        "verified_responses":len(rows),
        "failed_responses":failures,
        "public_changed_canaries_complete":failures == 0,
        "all_XI_source_changes_captured":False,
        "managed_source_verified":False,
        "ni_other_payable_verified":False,
        "production_eligible":False,
        "desktop_database_mutated":False,
        "source_results":outcomes,
        "source_db_sha256":None,
        "checked_at":datetime.now(timezone.utc).isoformat()
    }
    if failures == 0:
        paths[1].parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=paths[1].parent) as temporary:
            stage = Path(temporary)/"xi-canary-source.db"
            with sqlite3.connect(stage) as conn:
                conn.execute("""CREATE TABLE raw_responses(
                    code TEXT PRIMARY KEY, as_of TEXT, sha256 TEXT,
                    original_bytes INTEGER, compressed_raw_json BLOB)""")
                conn.executemany("INSERT INTO raw_responses VALUES(?,?,?,?,?)", rows)
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Temporary canary source DB corrupted")
            stage.replace(paths[1])
        report["source_db_sha256"] = hashlib.sha256(paths[1].read_bytes()).hexdigest()
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signal", required=True)
    p.add_argument("--output-db", required=True)
    p.add_argument("--output-report", required=True)
    args = p.parse_args()
    result = review(args.signal, args.output_db, args.output_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["public_changed_canaries_complete"]:
        raise SystemExit("XI changed canary capture incomplete; never publish")


if __name__ == "__main__":
    main()
