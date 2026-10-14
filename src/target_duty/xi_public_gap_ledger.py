"""Independent, source-replayed PUBLIC XI collection gap ledger.

Use it before merging or releasing any raw-source checkpoint. Every recorded
success is replayed from original compressed JSON:API bytes; missing shards,
HTTP errors, overlapping workers and invalid provenance cannot disappear
behind summary counts. This NEVER certifies NI Other duty or managed OAuth.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .xi_acceptance import evaluate
from .xi_measure_crawl import candidates
from .xi_public_full_capture import verify_public


def gaps(catalogue: str, manifest: str, shards: list[str], *,
         output_report: str, output_csv: str, sample_limit: int = 30) -> dict:
    if not 1 <= sample_limit <= 500 or len(shards) > 32:
        raise ValueError("Invalid public XI shard/diagnostic bounds")
    source = verify_public(catalogue, manifest)
    codes = candidates(catalogue)
    if len(codes) != source["active_declarable_codes"]:
        raise ValueError("Frozen public XI candidate count mismatch")
    original_paths = {Path(catalogue).resolve(), Path(manifest).resolve()}
    shard_paths = [Path(p).resolve() for p in shards]
    target_json, target_csv = Path(output_report).resolve(), Path(output_csv).resolve()
    if (len(shard_paths) != len(set(shard_paths)) or
            target_json == target_csv or
            {target_json, target_csv} & (original_paths | set(shard_paths))):
        raise ValueError("Public gap audit output aliases source evidence")
    valid_codes = set(codes)
    seen: dict[str, tuple[str, str | None, str, int, int]] = {}
    stored_shards = []
    for file in shard_paths:
        if not file.is_file():
            raise ValueError("Referenced XI checkpoint missing: " + file.name)
        # Independent whole-shard source replay BEFORE consulting status flags.
        checked = evaluate(catalogue, str(file), min_chapters=90)
        if (checked["listing_scope"] != "public_full_untrusted"
                or checked["active_declarable_codes"] != len(codes)
                or checked["date"] != source["as_of"]):
            raise ValueError("Public XI shard scope/date mismatch")
        rows = 0
        with sqlite3.connect(file.as_uri() + "?mode=ro", uri=True) as db:
            per_code_measures = {
                code: (total, targets)
                for code, total, targets in db.execute(
                    """SELECT code, COUNT(*),
                        SUM(CASE WHEN measure_type IN ('103','105') THEN 1 ELSE 0 END)
                       FROM measures GROUP BY code""")
            }
            for code, status, body_hash, error in db.execute(
                    "SELECT code,status,response_sha256,error FROM requests ORDER BY code"):
                if code not in valid_codes:
                    raise ValueError("XI source includes code outside public catalogue")
                if code in seen:
                    raise ValueError("Same code occurs in multiple public shards: " + code)
                if status not in ("ok", "source_error"):
                    raise ValueError("Unexpected public raw-source status: " + str(status))
                total, targets = per_code_measures.get(code, (0, 0))
                seen[code] = (status, body_hash, error or "", total, targets)
                rows += 1
        stored_shards.append({
            "filename":file.name,
            "sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
            "records": rows,
            "replayed_raw_measures":checked["replayed_source_measures"],
            "replayed_response_bytes":checked["replayed_response_bytes"],
            "production_eligible": False,
        })
    summary = Counter()
    chapters: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str,list[str]] = {"missing":[], "source_error":[]}
    csv_target = Path(output_csv)
    csv_target.parent.mkdir(parents=True,exist_ok=True)
    with csv_target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["commodity_code", "chapter", "source_response_status",
                         "source_response_sha256", "source_error",
                         "raw_import_measure_count", "third_country_103_105_candidates",
                         "review_state"])
        for code in codes:
            status, digest, error, total, targets = seen.get(
                code, ("missing", None, "", 0, 0))
            if status == "ok":
                if total == 0:
                    summary["raw_response_with_zero_import_measures"] += 1
                if targets == 0:
                    summary["raw_response_without_103_105_candidates"] += 1
                else:
                    summary["raw_response_with_103_105_candidates"] += 1
            review_state = (
                "source_not_collected" if status == "missing"
                else "source_fetch_or_parse_error" if status == "source_error"
                else "candidate_requires_origin_and_conditions_review" if targets
                else "no_third_country_candidate_needs_source_review")
            summary[status] += 1
            chapters[code[:2]][status] += 1
            if status in samples and len(samples[status]) < sample_limit:
                samples[status].append(code)
            writer.writerow([code,code[:2],status,digest or "",error,
                             total,targets,review_state])
    total=len(codes)
    success=summary["ok"]
    missing=summary["missing"]
    errors=summary["source_error"]
    chapter_rows=[]
    for chapter in sorted({code[:2] for code in codes}):
        counts=chapters[chapter]
        chapter_rows.append({
            "chapter":chapter,"expected":sum(counts.values()),
            "replayed_successes":counts["ok"],
            "missing_responses":counts["missing"],
            "source_errors":counts["source_error"],
            "complete":not counts["missing"] and not counts["source_error"]})
    complete=success==total and not missing and not errors
    report={
        "kind":"xi-public-full-independent-gap-ledger-v1",
        "as_of":source["as_of"], "listing_scope":"public_full_untrusted",
        "catalogue_sha256":source["catalogue_sha256"],
        "official_chapters":source["official_chapters"],
        "expected_commodity_codes":total,
        "replayed_successful_responses":success,
        "missing_responses":missing,
        "source_errors":errors,
        "raw_response_with_zero_import_measures":summary["raw_response_with_zero_import_measures"],
        "raw_response_without_103_105_candidates":summary["raw_response_without_103_105_candidates"],
        "raw_response_with_103_105_candidates":summary["raw_response_with_103_105_candidates"],
        "public_source_complete":complete,
        "incomplete_chapters":sum(not row["complete"] for row in chapter_rows),
        "chapter_coverage":chapter_rows,
        "samples":samples,
        "original_shards":stored_shards,
        "all_codes_csv_sha256":hashlib.sha256(csv_target.read_bytes()).hexdigest(),
        "review_generated_at":datetime.now(timezone.utc).isoformat(),
        "managed_full_source_coverage_proven":False,
        "ni_other_verified":False,
        "production_eligible":False,
        "atomic_official_snapshot_verified":False,
        "warning":(
            "PUBLIC raw-source coverage is not HMRC managed source attestation "
            "or verified NI Other payable duty. Missing or errored response "
            "cannot be interpreted as a zero-rate tariff.")
    }
    target=Path(output_report)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                      encoding="utf-8")
    return report


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--shards",nargs="*",default=[])
    p.add_argument("--output-json",default="xi-public-source-gaps.json")
    p.add_argument("--output-csv",default="xi-public-source-gaps.csv")
    p.add_argument("--fail-incomplete",action="store_true")
    args=p.parse_args()
    report=gaps(args.catalogue,args.manifest,args.shards,
                output_report=args.output_json,output_csv=args.output_csv)
    print(json.dumps({
        key:report[key] for key in (
            "expected_commodity_codes","replayed_successful_responses",
            "missing_responses","source_errors","incomplete_chapters",
            "public_source_complete","production_eligible")},
        ensure_ascii=False,indent=2))
    if args.fail_incomplete and not report["public_source_complete"]:
        raise SystemExit("Public XI source has missing or failed original responses")


if __name__=="__main__":
    main()
