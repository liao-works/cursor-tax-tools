"""Read-only NI candidate conditions and legal evidence from original JSON:API.

Outputs all 103/105 candidate measure detail links, not a payable duty.
Missing source relationships are UNKNOWN, not proof that no additional code
or other restriction exists. Source bytes stay unchanged.
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
from .xi_duty_projection import project
from .xi_measure_crawl import candidates
from .xi_public_full_capture import verify_public

ISO = re.compile(r"[A-Z]{2}\Z")
ALLOWED_MOVEMENTS = frozenset(("GB_TO_NI", "OTHER_TO_NI"))


def _refs(node: dict, name: str) -> list[dict] | None:
    relationships = node.get("relationships") or {}
    if name not in relationships:
        return None
    relation = relationships[name]
    if not isinstance(relation, dict) or "data" not in relation:
        raise ValueError("Invalid source relationship: " + name)
    value = relation["data"]
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("Invalid source relationship data: " + name)
    for ref in value:
        if not isinstance(ref, dict) or not ref.get("id") or not ref.get("type"):
            raise ValueError("Malformed source linked reference: " + name)
    return value


def _related(node: dict, name: str, index: dict, missing: list[str]) -> list[dict] | None:
    references = _refs(node, name)
    if references is None:
        return None
    result = []
    for ref in references:
        key = (str(ref["type"]), str(ref["id"]))
        found = index.get(key)
        if found is None:
            missing.append(name + ":" + key[0] + ":" + key[1])
        else:
            result.append(found)
    return result


def _stripped(node: dict, *, keep_relations: bool = False) -> dict:
    out = {"id":str(node.get("id")), "type":str(node.get("type")),
           "attributes":node.get("attributes") or {}}
    if keep_relations:
        out["relationships"]=node.get("relationships") or {}
    return out


def details(payload: bytes, code: str, as_of: date) -> list[dict]:
    base = project(payload, code, as_of)
    doc = json.loads(payload)
    index = {(str(o["type"]),str(o["id"])):o for o in doc["included"]
             if isinstance(o,dict) and "type" in o and "id" in o}
    output = []
    response_sha = hashlib.sha256(payload).hexdigest()
    for row in base:
        measure = index.get(("measure", row["measure_id"]))
        if not measure:
            raise ValueError("Candidate measure missing from original source")
        missing = []
        def resolve(name):
            return _related(measure,name,index,missing)
        geos=resolve("geographical_area")
        conditions=resolve("measure_conditions")
        groups=resolve("measure_condition_permutation_groups")
        exclusions=resolve("excluded_countries")
        components=resolve("measure_components")
        resolved=resolve("resolved_measure_components")
        footnotes=resolve("footnotes")
        acts=resolve("legal_acts")
        # A missing additional_code relationship is NOT an asserted absence.
        extra=resolve("additional_code")
        group_records=[]
        for group in groups or []:
            permutations=_related(group,"permutations",index,missing)
            group_records.append({
                "group":_stripped(group),
                "permutation_reference_state":"not_exposed" if permutations is None else "provided",
                "permutations":[{
                    "record":_stripped(p),
                    "measure_condition_refs":_refs(p,"measure_conditions")
                } for p in permutations or []]
            })
        geography_records=[]
        for item in geos or []:
            child_refs=_refs(item,"children_geographical_areas")
            geography_records.append({
                "entity":_stripped(item),
                "child_reference_state":"not_exposed" if child_refs is None else "provided",
                "child_geographical_area_ids":sorted(
                    str(x["id"]) for x in child_refs or [])
            })
        out={
            "code":code,"measure_id":row["measure_id"],
            "measure_type":row["measure_type"],"geography_id":row["geography_id"],
            "candidate_expression":row["candidate_expression"],
            "effective_start":row["effective_start"],
            "effective_end":row["effective_end"],
            "original_response_sha256":response_sha,
            "original_measure_sha256":hashlib.sha256(
                json.dumps(measure,sort_keys=True,ensure_ascii=False,
                           separators=(",",":")).encode()).hexdigest(),
            "geo_evidence_json":json.dumps(geography_records,ensure_ascii=False,sort_keys=True),
            "excluded_geographies_json":json.dumps([_stripped(x) for x in exclusions or []],
                                                   ensure_ascii=False,sort_keys=True),
            "condition_evidence_json":json.dumps([_stripped(x,keep_relations=True)
                                                    for x in conditions or []],ensure_ascii=False,sort_keys=True),
            "condition_permutations_json":json.dumps(group_records,ensure_ascii=False,sort_keys=True),
            "measure_components_json":json.dumps([_stripped(x,keep_relations=True)
                                                    for x in components or []],ensure_ascii=False,sort_keys=True),
            "resolved_components_json":json.dumps([_stripped(x,keep_relations=True)
                                                     for x in resolved or []],ensure_ascii=False,sort_keys=True),
            "legal_acts_json":json.dumps([_stripped(x) for x in acts or []],ensure_ascii=False,sort_keys=True),
            "footnotes_json":json.dumps([_stripped(x) for x in footnotes or []],ensure_ascii=False,sort_keys=True),
            "additional_code_state":"not_exposed" if extra is None else "source_explicit_none" if not extra else "source_referenced",
            "additional_codes_json":json.dumps([_stripped(x) for x in extra or []],
                                               ensure_ascii=False,sort_keys=True),
            "missing_nested_references_json":json.dumps(sorted(set(missing)),
                                                         ensure_ascii=False),
            "review_required":"true",
            "ni_other_payable_rate":"",
            "production_eligible":"false",
        }
        output.append(out)
    return output


def scenario(measure_detail: dict, *, origin: str | None,
             movement: str | None, at_risk: bool | None,
             ukims_authorised: bool | None) -> dict:
    """Return only origin exclusion and missing-trader-info review flags."""
    if origin is not None and (not ISO.fullmatch(origin) or origin=="ZZ"):
        raise ValueError("Explicit two-letter origin needed; OTHER is not an ISO origin")
    if movement is not None and movement not in ALLOWED_MOVEMENTS:
        raise ValueError("Movement context is required and must be recognized")
    if at_risk not in (True,False,None) or ukims_authorised not in (True,False,None):
        raise ValueError("Invalid trader risk or UKIMS status")
    exclusions=json.loads(measure_detail["excluded_geographies_json"])
    excluded_ids={str(r["id"]) for r in exclusions}
    excluded = origin in excluded_ids if origin else False
    missing=[]
    if origin is None:
        missing.append("origin_country_required")
    if movement is None:
        missing.append("movement_direction_required")
    if at_risk is None:
        missing.append("at_risk_status_required")
    if ukims_authorised is None:
        missing.append("ukims_authorisation_unknown")
    if measure_detail["additional_code_state"]=="not_exposed":
        missing.append("additional_code_visibility_unknown")
    if json.loads(measure_detail["condition_evidence_json"]):
        missing.append("measure_conditions_require_document_verification")
    if json.loads(measure_detail["condition_permutations_json"]):
        missing.append("condition_permutation_requirements_unassessed")
    if json.loads(measure_detail["missing_nested_references_json"]):
        missing.append("unresolved_official_nested_references")
    # Even a non-at-risk movement may need a UK rate, relief, or declaration.
    missing.append("duty_route_and_preference_legally_unverified")
    return {
        "code":measure_detail["code"],
        "measure_id":measure_detail["measure_id"],
        "origin":origin,
        "movement":movement,
        "at_risk":at_risk,
        "ukims_authorised":ukims_authorised,
        "excluded_from_this_measures_geography":excluded,
        "reason_codes":missing,
        "decision":"excluded_measure_not_a_rate" if excluded else "needs_legal_and_trader_context",
        "payable_rate":None,
        "production_eligible":False,
    }


def export(catalogue: str,manifest: str,shards: list[str],*,
           output_csv: str,output_report: str) -> dict:
    scope=verify_public(catalogue,manifest)
    expected=set(candidates(catalogue))
    seen=set()
    unique_measures=set()
    counts=Counter()
    output_path=Path(output_csv).resolve()
    report_path=Path(output_report).resolve()
    originals={Path(p).resolve() for p in [catalogue,manifest,*shards]}
    if output_path in originals or report_path in originals or output_path==report_path:
        raise ValueError("Evidence output may not overwrite immutable source")
    output_path.parent.mkdir(parents=True,exist_ok=True)
    with output_path.open("w",newline="",encoding="utf-8") as f:
        writer=None
        for source_path in shards:
            with sqlite3.connect(Path(source_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
                config=dict(db.execute("SELECT key,value FROM config"))
                for k,v in {"region":"XI","as_of":scope["as_of"],
                            "catalogue_sha256":scope["catalogue_sha256"],
                            "production_eligible":"false","other_semantics_verified":"false"}.items():
                    if config.get(k)!=v:
                        raise ValueError("Provenance mismatch: "+k)
                for code,status,fingerprint,blob in db.execute(
                    "SELECT code,status,response_sha256,compressed_body FROM requests ORDER BY code"):
                    if code in seen or code not in expected or status!="ok":
                        raise ValueError("XI source missing/unlisted/failed/duplicate code")
                    seen.add(code)
                    original=decompress_checked(blob)
                    if hashlib.sha256(original).hexdigest()!=fingerprint:
                        raise ValueError("XI original response hash mismatch: "+code)
                    rows=details(original,code,date.fromisoformat(scope["as_of"]))
                    counts["verified_responses"]+=1
                    counts["candidate_codes"]+=bool(rows)
                    for row in rows:
                        key=(code,row["measure_id"])
                        if key in unique_measures:
                            raise ValueError("Duplicate measure in official source")
                        unique_measures.add(key)
                        if writer is None:
                            writer=csv.DictWriter(f,fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                        counts["measures"]+=1
                        counts["additional_code_relation_unavailable"]+=row["additional_code_state"]=="not_exposed"
                        counts["condition_rows"]+=bool(json.loads(row["condition_evidence_json"]))
                        counts["condition_groups"]+=bool(json.loads(row["condition_permutations_json"]))
                        counts["geo_exclusion_rows"]+=bool(json.loads(row["excluded_geographies_json"]))
                        counts["unresolved_relation_rows"]+=bool(json.loads(row["missing_nested_references_json"]))
        if writer is None:
            f.write("code,measure_id\n")
    def checksum(path):
        h=hashlib.sha256()
        with open(path,"rb") as fd:
            for chunk in iter(lambda:fd.read(1024*1024),b""):
                h.update(chunk)
        return h.hexdigest()
    report={
        "kind":"xi-third-country-raw-conditions-evidence-v1",
        "as_of":scope["as_of"],
        "catalogue_sha256":scope["catalogue_sha256"],
        "expected_codes":len(expected),
        "verified_original_responses":counts["verified_responses"],
        "candidate_codes":counts["candidate_codes"],
        "candidate_measures":counts["measures"],
        "codes_without_candidates":len(expected)-counts["candidate_codes"],
        "additional_code_relation_unavailable":counts["additional_code_relation_unavailable"],
        "candidate_measures_with_conditions":counts["condition_rows"],
        "candidate_measures_with_permutation_groups":counts["condition_groups"],
        "candidate_measures_with_exclusions":counts["geo_exclusion_rows"],
        "candidate_measures_with_unresolved_nested_reference":counts["unresolved_relation_rows"],
        "missing_original_responses":len(expected-seen),
        "output_csv_sha256":checksum(output_path),
        "source_full_complete":seen==expected,
        "ni_other_verified":False,
        "production_eligible":False,
        "certified_payable_rates":0,
        "warning":"Original linked evidence, NOT a rate calculator. Additional code may be not exposed; origin, UKIMS and rules require independent authorization."
    }
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--shards",nargs="+",required=True)
    p.add_argument("--csv",required=True)
    p.add_argument("--report",required=True)
    a=p.parse_args()
    result=export(a.catalogue,a.manifest,a.shards,output_csv=a.csv,output_report=a.report)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if not result["source_full_complete"] or result["certified_payable_rates"]:
        raise SystemExit("Source-only full evidence failed; no release")


if __name__=="__main__":
    main()
