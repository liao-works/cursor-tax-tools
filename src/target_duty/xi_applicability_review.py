"""Fail-closed per-code review of SHA-pinned XI third-country source candidates.

Every commodity receives a review row. No result is a payable duty. In
particular OTHER is not an origin-country identifier and public source
completeness does not settle UKIMS / at-risk / relief applicability.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

CODE = re.compile(r"\d{10}\Z")
PERCENT = re.compile(r"\s*\d+(?:\.\d+)?\s*%\s*\Z")


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def check_date(value: str, *, optional: bool = False) -> date | None:
    if not value and optional:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid or missing XI evidence date: " + repr(value))
    return date.fromisoformat(value)


def analyze(candidate_csv: str, candidate_report: str,
            coverage_csv: str, coverage_report: str,
            output_csv: str, output_report: str, *, reviewed_on: date) -> dict:
    paths = [Path(x).resolve() for x in
             (candidate_csv, candidate_report, coverage_csv, coverage_report,
              output_csv, output_report)]
    if len(paths) != len(set(paths)):
        raise ValueError("Outputs must not alias source evidence")
    projection = json.loads(Path(candidate_report).read_text(encoding="utf-8"))
    coverage = json.loads(Path(coverage_report).read_text(encoding="utf-8"))
    if (projection.get("kind") != "xi-public-third-country-source-projection-v1"
            or coverage.get("kind") != "xi-public-full-independent-gap-ledger-v1"
            or not projection.get("raw_source_complete")
            or not coverage.get("public_source_complete")
            or projection.get("source_failures") != 0
            or projection.get("missing_responses") != 0
            or coverage.get("source_errors") != 0
            or coverage.get("missing_responses") != 0
            or projection.get("production_eligible") is not False
            or coverage.get("production_eligible") is not False
            or projection.get("ni_other_verified") is not False
            or coverage.get("ni_other_verified") is not False
            or projection.get("as_of") != coverage.get("as_of")
            or projection.get("catalogue_sha256") != coverage.get("catalogue_sha256")):
        raise ValueError("XI source evidence incomplete or provenance mismatch")
    if sha(candidate_csv) != projection.get("source_csv_sha256"):
        raise ValueError("XI candidate source CSV SHA-256 mismatch")
    if sha(coverage_csv) != coverage.get("all_codes_csv_sha256"):
        raise ValueError("XI original source coverage CSV SHA-256 mismatch")
    snapshot = check_date(projection["as_of"])
    if reviewed_on < snapshot:
        raise ValueError("Review date earlier than source snapshot")
    expected = projection.get("expected_codes")
    if expected != coverage.get("expected_commodity_codes"):
        raise ValueError("Projection/official catalogue counts differ")

    codes = {}
    with open(coverage_csv, encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            code = row["commodity_code"]
            if not CODE.fullmatch(code) or code in codes:
                raise ValueError("Duplicate or invalid source commodity code")
            if row["chapter"] != code[:2] or row["source_response_status"] != "ok":
                raise ValueError("Unverified source response or wrong chapter")
            if not re.fullmatch(r"[0-9a-f]{64}", row["source_response_sha256"]):
                raise ValueError("Source response SHA-256 missing")
            if row["source_error"]:
                raise ValueError("Source error masked as success")
            count = int(row["third_country_103_105_candidates"])
            total = int(row["raw_import_measure_count"])
            if count < 0 or total < count:
                raise ValueError("Invalid source measure count")
            codes[code] = (row["source_response_sha256"], count, total)
    if len(codes) != expected:
        raise ValueError("Coverage does not contain every XI code")

    by_code = defaultdict(list)
    seen = set()
    with open(candidate_csv, encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            code, mid = row["code"], row["measure_id"]
            if code not in codes or not mid or (code, mid) in seen:
                raise ValueError("Unlisted/duplicate XI candidate measure")
            seen.add((code, mid))
            if row["production_eligible"] != "false" or row["ni_other_payable_rate"]:
                raise ValueError("XI source illegally labelled payable")
            if row["measure_type"] not in ("103", "105"):
                raise ValueError("Non-target XI candidate type")
            start = check_date(row["effective_start"])
            end = check_date(row["effective_end"], optional=True)
            if start > snapshot or (end and end < snapshot):
                raise ValueError("Candidate not active on frozen source date")
            if end and end < start:
                raise ValueError("End of measure precedes start")
            for field in ("condition_count", "condition_group_count",
                          "excluded_country_count", "measure_component_count",
                          "resolved_component_count"):
                if int(row[field]) < 0:
                    raise ValueError("Negative measure relation count")
            by_code[code].append(row)
    if len(seen) != projection["candidate_measures"] or len(by_code) != projection["candidate_codes"]:
        raise ValueError("Candidate count does not match pinned projection")
    for code,(_, count, _) in codes.items():
        if len(by_code.get(code, [])) != count:
            raise ValueError("Source candidate count differs: " + code)
    codes_with_candidates = len(by_code)

    result = []
    primary_totals, overlapping = Counter(), Counter()
    expiry = []
    for code in sorted(codes):
        rows = by_code[code]
        generic = [r for r in rows if r["measure_type"] == "103" and r["geography_id"] == "1011"]
        competing = [r for r in rows if r["measure_type"] == "105" and r["geography_id"] == "1011"]
        specific = [r for r in rows if r["geography_id"] != "1011"]
        target = generic + competing
        reasons = []
        if not rows:
            reasons.append("no_103_105_measure")
        if not generic:
            reasons.append("no_generic_103_baseline")
        elif len(generic) > 1:
            reasons.append("multiple_generic_103_baselines")
        if competing:
            reasons.append("type_105_competes")
        if specific:
            reasons.append("specific_country_territory_measures")
        if any(int(r["condition_count"]) or int(r["condition_group_count"]) for r in target):
            reasons.append("certificates_and_measure_conditions")
        if any(int(r["excluded_country_count"]) for r in target):
            reasons.append("geographical_exclusions")
        if any(not r["candidate_expression"] for r in target):
            reasons.append("missing_duty_expression")
        if any(r["candidate_expression"] and not PERCENT.fullmatch(r["candidate_expression"])
               for r in target):
            reasons.append("compound_or_specific_expression")
        ended = [r for r in rows if r["effective_end"] and
                 snapshot <= check_date(r["effective_end"]) <= reviewed_on]
        if ended:
            reasons.append("measure_ends_by_review_date")
            expiry.extend((code, r["measure_id"], r["effective_end"]) for r in ended)
        priority = reasons[0] if reasons else "simple_source_candidate_origin_review_only"
        primary_totals[priority] += 1
        overlapping.update(reasons)
        digest,_,total = codes[code]
        result.append({
            "code":code, "source_as_of":snapshot.isoformat(),
            "reviewed_on":reviewed_on.isoformat(),
            "source_response_sha256":digest,
            "raw_import_measures":total,
            "third_country_source_candidates":len(rows),
            "generic_type_103_count":len(generic),
            "competing_type_105_count":len(competing),
            "country_specific_measure_count":len(specific),
            "baseline_expressions":" | ".join(sorted({r["candidate_expression"] for r in generic})),
            "source_review_priority":priority,
            "all_review_reasons":";".join(reasons),
            "country_of_origin_specified":"false",
            "additional_code_verified":"false",
            "ukims_at_risk_and_reliefs_verified":"false",
            "current_date_source_refreshed":"true" if reviewed_on == snapshot else "false",
            "ni_other_payable_rate":"",
            "production_eligible":"false"
        })
    out = Path(output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    summary = {
        "kind":"xi-16728-code-applicability-review-only-v1",
        "source_as_of":snapshot.isoformat(),
        "reviewed_on":reviewed_on.isoformat(),
        "frozen_source_catalogue_sha256":projection["catalogue_sha256"],
        "official_source_codes":len(codes),
        "independently_pinned_original_responses":len(codes),
        "source_import_measure_rows":sum(total for _,_,total in codes.values()),
        "third_country_candidate_measures":len(seen),
        "codes_with_candidates":codes_with_candidates,
        "codes_without_candidates":len(codes)-codes_with_candidates,
        "primary_review_groups":dict(sorted(primary_totals.items())),
        "overlapping_review_reasons":dict(sorted(overlapping.items())),
        "measures_ending_between_snapshot_and_review":len(expiry),
        "expiry_samples":[{"code":c, "measure_id":m, "effective_end":d}
                          for c,m,d in expiry[:30]],
        "source_csv_sha256":sha(candidate_csv),
        "source_coverage_csv_sha256":sha(coverage_csv),
        "output_csv_sha256":sha(str(out)),
        "source_snapshot_current_as_of_review_date":reviewed_on == snapshot,
        "additional_code_details_not_projected":True,
        "origin_and_trader_at_risk_status_not_supplied":True,
        "ni_other_verified":False,
        "production_eligible":False,
        "certified_payable_rates":0,
        "warning":"ALL rates are source candidate hints, not payable NI Other duties."
    }
    dest = Path(output_report)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return summary


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate-csv",required=True)
    p.add_argument("--candidate-report",required=True)
    p.add_argument("--coverage-csv",required=True)
    p.add_argument("--coverage-report",required=True)
    p.add_argument("--reviewed-on",required=True)
    p.add_argument("--output-csv",required=True)
    p.add_argument("--output-report",required=True)
    a=p.parse_args()
    report=analyze(a.candidate_csv,a.candidate_report,a.coverage_csv,
                   a.coverage_report,a.output_csv,a.output_report,
                   reviewed_on=check_date(a.reviewed_on))
    print(json.dumps(report,indent=2,ensure_ascii=False))
    if report["certified_payable_rates"] or report["production_eligible"]:
        raise SystemExit("Never publish unverified source candidates")


if __name__=="__main__":
    main()
