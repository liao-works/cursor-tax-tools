"""Independent cross-database commodity coverage matrix, never a tax-rate release.

Unions the SHA-validated official GB source ledger, frozen XI catalogue and
replayed XI raw measures with the legacy desktop tariffs table. A missing code
in one region is NOT automatically a missing duty; jurisdictions can differ.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .gb_review_diff import _snapshot as gb_snapshot
from .xi_acceptance import evaluate
from .xi_measure_crawl import candidates
from .xi_frozen_catalogue import digest_file

CODE = re.compile(r"[0-9]{10}\Z")


def matrix(legacy: str, gb_review: str, xi_catalogue: str,
           xi_checkpoint: str, *, output_csv: str,
           output_report: str, min_chapters: int = 90) -> dict:
    source_paths = {Path(p).resolve() for p in
                    (legacy, gb_review, xi_catalogue, xi_checkpoint)}
    target_csv, target_json = Path(output_csv), Path(output_report)
    if (len(source_paths) != 4 or target_csv.resolve() in source_paths
            or target_json.resolve() in source_paths
            or target_csv.resolve() == target_json.resolve()):
        raise ValueError("Coverage output must not overwrite or alias any source DB")
    gb = gb_snapshot(gb_review)  # original GB report, SHA and leaf counts checked
    xi = evaluate(xi_catalogue, xi_checkpoint, min_chapters=min_chapters)
    xi_codes = set(candidates(xi_catalogue))
    legacy_path = Path(legacy)
    if not legacy_path.is_file():
        raise ValueError("Legacy production reference DB missing")
    with sqlite3.connect(legacy_path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Legacy desktop database integrity failed")
        legacy_codes = set()
        for (code,) in db.execute("SELECT code FROM tariffs"):
            if not isinstance(code, str) or not CODE.fullmatch(code) or code in legacy_codes:
                raise ValueError("Duplicate or invalid legacy commodity code")
            legacy_codes.add(code)
    with sqlite3.connect(Path(xi_checkpoint).resolve().as_uri()+"?mode=ro",
                         uri=True) as db:
        statuses = dict(db.execute("SELECT code,status FROM requests"))
    if set(statuses) - xi_codes:
        raise ValueError("XI checkpoint contains a code outside official active catalogue")
    all_codes = sorted(legacy_codes | set(gb["codes"]) | xi_codes)
    counters = Counter()
    target_csv.parent.mkdir(parents=True, exist_ok=True)
    target_json.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_csv.parent) as folder:
        stage = Path(folder)/"matrix.csv"
        with stage.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["code","legacy_desktop","gb_official_leaf",
                             "gb_103_1011_source_status","xi_official_declarable",
                             "xi_original_response_status","requires_source_followup"])
            for code in all_codes:
                old = code in legacy_codes
                gb_data = gb["codes"].get(code)
                xi_listed = code in xi_codes
                xi_state = (statuses.get(code, "source_not_collected") if xi_listed
                            else "not_in_active_xi_catalogue")
                gb_state = gb_data["status"] if gb_data else "not_in_active_gb_catalogue"
                # Do not use 0% for absent official rows or an empty measure list.
                issue = (xi_listed and xi_state != "ok") or (
                    bool(gb_data) and gb_data["status"]
                    != "candidate_present_requires_applicability_review")
                if old and not gb_data and not xi_listed:
                    counters["legacy_not_active_in_either_catalogue"] += 1
                if xi_listed:
                    counters["xi_" + xi_state] += 1
                if gb_data:
                    counters["gb_" + gb_state] += 1
                if issue:
                    counters["source_followup_codes"] += 1
                writer.writerow([code, int(old), int(bool(gb_data)), gb_state,
                                 int(xi_listed), xi_state, int(bool(issue))])
        raw = stage.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        stage.replace(target_csv)
    xi_complete = bool(xi["independent_catalogue_scope_ok"]
                       and xi["all_collected_measure_responses_verified"]
                       and xi["missing_codes"] == 0
                       and xi["request_failures"] == 0)
    report = {
        "kind": "gb-xi-legacy-source-coverage-matrix-review-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_gb": gb["as_of"],
        "as_of_xi": xi["date"],
        "as_of_dates_equal": gb["as_of"] == xi["date"],
        "gb_source_sha256": gb["sha256"],
        "gb_version": gb["version"],
        "xi_catalogue_sha256": digest_file(xi_catalogue),
        "xi_checkpoint_sha256": digest_file(xi_checkpoint),
        "legacy_sha256": digest_file(legacy),
        "matrix_csv_sha256": sha,
        "matrix_rows": len(all_codes),
        "legacy_desktop_codes": len(legacy_codes),
        "gb_official_active_leaf_codes": len(gb["codes"]),
        "xi_official_active_declarable_codes": len(xi_codes),
        "xi_source_response_complete": xi_complete,
        "gb_source_ledger_validated": True,
        "both_regional_sources_complete": (xi_complete and gb["as_of"] == xi["date"]),
        "source_status_counts": dict(sorted(counters.items())),
        "production_eligible": False,
        "ni_other_verified": False,
        "gb_payable_duty_verified": False,
        "atomic_official_snapshot_verified": False,
        "warning": (
            "This matrix proves row-level source inventory and replay status only. "
            "GB and XI inventories may differ. Neither unresolved GB 103/1011 "
            "nor XI raw 103/105 may be inferred as a 0% or payable Other duty. "
            "Never use this evidence to overwrite desktop tariffs.db."
        ),
    }
    with tempfile.TemporaryDirectory(dir=target_json.parent) as folder:
        stage = Path(folder)/"matrix.json"
        stage.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                         encoding="utf-8")
        stage.replace(target_json)
    return report


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-db", required=True)
    p.add_argument("--gb-review-db", required=True)
    p.add_argument("--xi-catalogue", required=True)
    p.add_argument("--xi-checkpoint", required=True)
    p.add_argument("--output-csv", default="gb-xi-legacy-source-matrix.csv")
    p.add_argument("--output-report", default="gb-xi-legacy-source-matrix.json")
    p.add_argument("--min-chapters", type=int, default=90)
    p.add_argument("--require-complete", action="store_true",
                   help="Fail on missing XI responses after retaining diagnostic report")
    a=p.parse_args()
    report=matrix(a.legacy_db,a.gb_review_db,a.xi_catalogue,a.xi_checkpoint,
                  output_csv=a.output_csv,output_report=a.output_report,
                  min_chapters=a.min_chapters)
    print(json.dumps({k:report[k] for k in
                      ("matrix_rows","legacy_desktop_codes",
                       "gb_official_active_leaf_codes",
                       "xi_official_active_declarable_codes",
                       "xi_source_response_complete","production_eligible")},
                     ensure_ascii=False,indent=2))
    if a.require_complete and not report["both_regional_sources_complete"]:
        raise SystemExit("GB/XI source incomplete or pinned dates differ; matrix saved for repair")


if __name__ == "__main__":
    main()
