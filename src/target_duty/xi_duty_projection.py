"""Project XI third-country source expression references, WITHOUT NI payable rates.

Read-only: preserves all historical SHA-verified source checkpoints untouched.
Fixes empty resolved_duty_expression by following JSON:API duty_expression
relationships (base / verbose_duty). Never implies a zero/Other payable duty.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from .xi_acceptance import decompress_checked
from .xi_measure_crawl import candidates, parse
from .xi_public_full_capture import verify_public

PERCENT = re.compile(r"\s*\d+(?:\.\d+)?\s*%\s*\Z")
THIRD_COUNTRY_TYPES = frozenset(("103", "105"))


def linked(node: dict, name: str, index: dict) -> list[dict]:
    item = (node.get("relationships") or {}).get(name, {}).get("data")
    refs = item if isinstance(item, list) else [item] if isinstance(item, dict) else []
    resolved = []
    for ref in refs:
        key = (str(ref.get("type")), str(ref.get("id")))
        if key not in index:
            raise ValueError(f"Missing related {name}: {key}")
        resolved.append(index[key])
    return resolved


def project(payload: bytes, code: str, as_of: date) -> list[dict]:
    """Source-derived third-country candidates, never actionable payable rates."""
    parsed = parse(payload, code)
    doc = json.loads(payload)
    index = {(str(x.get("type")), str(x.get("id"))): x
             for x in doc.get("included", []) if isinstance(x, dict)}
    refs = doc["data"]["relationships"]["import_measures"]["data"]
    if len(parsed) != len(refs):
        raise ValueError("Raw import-measure parser/relation count mismatch")
    output = []
    for ref in refs:
        measure = index.get((str(ref.get("type")), str(ref.get("id"))))
        if not isinstance(measure, dict):
            raise ValueError("Raw XI measure relationship unresolved")
        types = linked(measure, "measure_type", index)
        typ = str(types[0]["id"]) if types else None
        if typ not in THIRD_COUNTRY_TYPES:
            continue
        geos = linked(measure, "geographical_area", index)
        geo = str(geos[0]["id"]) if geos else None
        expressions = linked(measure, "duty_expression", index)
        if len(expressions) > 1:
            raise ValueError("Ambiguous multiple linked duty expressions")
        attrs = expressions[0].get("attributes", {}) if expressions else {}
        base, verbose = attrs.get("base") or "", attrs.get("verbose_duty") or ""
        inline = (measure.get("attributes") or {}).get("resolved_duty_expression") or ""
        if not all(isinstance(x, str) for x in (base, verbose, inline)):
            raise ValueError("Malformed official duty expression fields")
        expression = base.strip() or verbose.strip() or inline.strip()
        components = linked(measure, "measure_components", index)
        resolved = linked(measure, "resolved_measure_components", index)
        conditions = linked(measure, "measure_conditions", index)
        permutations = linked(measure, "measure_condition_permutation_groups", index)
        excluded = linked(measure, "excluded_countries", index)
        attr = measure.get("attributes") or {}
        start, end = attr.get("effective_start_date"), attr.get("effective_end_date")
        if not isinstance(start, str):
            raise ValueError("Third-country measure has no effective start date")
        try:
            begin = date.fromisoformat(start[:10])
            finish = date.fromisoformat(end[:10]) if end else None
        except (ValueError, TypeError) as exc:
            raise ValueError("Malformed third-country measure date") from exc
        if begin > as_of or (finish and finish < as_of):
            raise ValueError("Non-active third-country measure in frozen source")
        if not expression:
            status = "missing_expression_review"
        elif geo != "1011":
            status = "specific_geography_review"
        elif conditions or permutations or excluded:
            status = "conditional_candidate_review"
        elif not PERCENT.fullmatch(expression):
            status = "compound_or_specific_candidate_review"
        else:
            status = "simple_percent_candidate_unverified"
        output.append({
            "code":code, "measure_id":str(measure["id"]),
            "measure_type":typ, "geography_id":geo or "",
            "source_origin":str(attr.get("origin") or ""),
            "effective_start":start[:10], "effective_end":end[:10] if end else "",
            "source_expression_base":base, "source_expression_verbose":verbose,
            "stored_inline_expression":inline, "candidate_expression":expression,
            "measure_component_count":len(components),
            "resolved_component_count":len(resolved),
            "condition_count":len(conditions),
            "condition_group_count":len(permutations),
            "excluded_country_count":len(excluded),
            "review_status":status,
            "ni_other_payable_rate":"", "production_eligible":"false",
        })
    return output


def export(catalogue: str, manifest: str, shards: list[str], *,
           output_csv: str, output_report: str) -> dict:
    """Recheck every original response SHA before producing derived evidence."""
    source = verify_public(catalogue, manifest)
    expected = set(candidates(catalogue))
    seen = set()
    counts = Counter()
    out = Path(output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    blocked = {Path(catalogue).resolve(), Path(manifest).resolve()}
    blocked.update(Path(x).resolve() for x in shards)
    if out.resolve() in blocked or Path(output_report).resolve() in blocked:
        raise ValueError("Review output cannot overwrite source checkpoints")
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = None
        for path in shards:
            with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
                settings = dict(db.execute("SELECT key,value FROM config"))
                if any(settings.get(k) != v for k,v in {
                    "region":"XI", "as_of":source["as_of"],
                    "catalogue_sha256":source["catalogue_sha256"],
                    "production_eligible":"false"}.items()):
                    raise ValueError("XI original shard provenance mismatch")
                for code, status, sha, blob in db.execute(
                    "SELECT code,status,response_sha256,compressed_body FROM requests ORDER BY code"):
                    if code not in expected or code in seen:
                        raise ValueError("XI code duplicated or not present in source")
                    seen.add(code)
                    if status != "ok":
                        counts["source_failures"] += 1
                        continue
                    raw = decompress_checked(blob)
                    if hashlib.sha256(raw).hexdigest() != sha:
                        raise ValueError("XI source response SHA mismatch: "+code)
                    rows = project(raw,code,date.fromisoformat(source["as_of"]))
                    counts["responses_verified"] += 1
                    counts["candidate_codes"] += bool(rows)
                    counts["codes_without_candidates"] += not bool(rows)
                    for row in rows:
                        if writer is None:
                            writer=csv.DictWriter(stream,fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                        counts["candidate_measures"] += 1
                        counts[row["review_status"]] += 1
        if writer is None:
            stream.write("code,measure_id,review_status\n")
    report={
        "kind":"xi-public-third-country-source-projection-v1",
        "as_of":source["as_of"],
        "catalogue_sha256":source["catalogue_sha256"],
        "expected_codes":len(expected),
        "responses_verified":counts["responses_verified"],
        "source_failures":counts["source_failures"],
        "missing_responses":len(expected-seen),
        "candidate_measures":counts["candidate_measures"],
        "candidate_codes":counts["candidate_codes"],
        "codes_without_candidates":counts["codes_without_candidates"],
        "review_status_counts":{s:counts[s] for s in (
            "missing_expression_review","specific_geography_review",
            "conditional_candidate_review","compound_or_specific_candidate_review",
            "simple_percent_candidate_unverified")},
        "source_csv_sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
        "raw_source_complete":not (expected-seen) and not counts["source_failures"],
        "ni_other_verified":False,"production_eligible":False,
        "managed_source_verified":False,
        "warning":"The candidate expression is NOT a payable NI Other duty."
    }
    Path(output_report).write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",
                                   encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue",required=True)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--shards",nargs="+",required=True)
    parser.add_argument("--csv",required=True)
    parser.add_argument("--report",required=True)
    args=parser.parse_args()
    result=export(args.catalogue,args.manifest,args.shards,
                  output_csv=args.csv,output_report=args.report)
    print(json.dumps(result,indent=2))
    if not result["raw_source_complete"]:
        raise SystemExit("XI raw source incomplete: never certify or publish")


if __name__=="__main__":
    main()
