"""Audit compact GB 103/1011 REVIEW databases before a source-version change.

Compare immutable evidence, not consumer payable duty. Current-status
changes can be caused by as-of date as well as source version. Preserve
explicit unresolved codes; never promote Channel Islands 1080 to GB 1011.
No output authorizes release or merges anything into desktop tariffs.db.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .dbt_version_discovery import version_key
from .gb_compact_review import file_sha

ROLES = {"gb_103_1011_candidate", "non_gb_103_1080_context"}
STATUSES = {
    "candidate_present_requires_applicability_review",
    "no_expanded_measure_rows_unverified",
    "non_gb_1080_only_or_other_measures_unresolved",
    "no_active_103_1011_unresolved",
}
REQUIRED_FLAGS = {
    "production_eligible": "false",
    "rate_applicability_verified": "false",
    "ni_other_verified": "false",
}
IDENTITY = (
    "sid", "effective_status", "expression", "additional_code", "conditions",
    "excluded_areas", "effective_start", "effective_end"
)


def _snapshot(path: str) -> dict:
    target = Path(path)
    manifest_path = target.with_suffix(".json")
    if not target.is_file() or not manifest_path.is_file():
        raise ValueError("GB review DB and its original JSON report are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or manifest.get("kind") != "gb-compact-103-1011-shadow-v1"
            or manifest.get("production_eligible") is not False
            or manifest.get("rate_applicability_verified") is not False
            or manifest.get("ni_other_verified") is not False):
        raise ValueError("Unrecognized or production-enabled GB review manifest")
    sha = file_sha(str(target))
    if sha != manifest.get("review_database_sha256"):
        raise ValueError("GB review SHA-256 does not match original source manifest")
    db = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("GB compact SQLite failed integrity_check")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("GB compact SQLite foreign keys broken")
        metadata = dict(db.execute("SELECT key,value FROM provenance"))
        if any(metadata.get(k) != v for k, v in REQUIRED_FLAGS.items()):
            raise ValueError("GB compact source review flags unsafe")
        if (metadata.get("region") != "GB"
                or metadata.get("source_kind") != "pinned-DBT-compact-103-review"
                or metadata.get("source_version") != manifest.get("version")
                or metadata.get("as_of") != manifest.get("as_of")
                or metadata.get("full_source_sha256") != manifest.get("full_source_sha256")
                or metadata.get("complete_import_source_rows") != str(manifest.get("full_source_rows"))):
            raise ValueError("GB compact region, version or full-source provenance differs")
        version_key(metadata["source_version"])
        date.fromisoformat(metadata["as_of"])
        records = {}
        for row in db.execute("SELECT code,status,active_target_candidates,other_area_context_rows FROM commodities ORDER BY code"):
            code, status, target_count, other_count = tuple(row)
            if (not isinstance(code,str) or len(code) != 10 or not code.isascii()
                    or not code.isdigit() or status not in STATUSES):
                raise ValueError("Malformed GB commodity evidence status/code")
            if code in records:
                raise ValueError("Duplicate GB commodity evidence")
            records[code] = {
                "status":status,
                "target_count":target_count,
                "other_count":other_count,
                "active":[],
                "all":[],
            }
        for row in db.execute(
            "SELECT code,role,geography,measure_type,sid,effective_status,expression,"
            " additional_code,conditions,excluded_areas,effective_start,effective_end "
            "FROM evidence ORDER BY code,source_ordinal"
        ):
            data = dict(row)
            code = data.pop("code")
            role = data.pop("role")
            geo = data.pop("geography")
            typ = data.pop("measure_type")
            if (code not in records or role not in ROLES or typ != "103"
                    or (role == "gb_103_1011_candidate" and geo != "1011")
                    or (role == "non_gb_103_1080_context" and geo != "1080")):
                raise ValueError("GB candidate measure geography/type integrity failure")
            if data["effective_status"] not in ("active","future","expired"):
                raise ValueError("Invalid measure effective state")
            semantic = json.dumps([role,geo,*[data[k] for k in IDENTITY]],
                                  ensure_ascii=False,separators=(",",":"))
            records[code]["all"].append(semantic)
            if role == "gb_103_1011_candidate" and data["effective_status"] == "active":
                records[code]["active"].append(semantic)
        targets = 0
        for code, data in records.items():
            if (len(data["active"]) != data["target_count"]
                or sum(x.startswith('["non_gb_103_1080_context"') for x in data["all"])
                != data["other_count"]):
                raise ValueError("GB candidate record counters differ: "+code)
            has_active = bool(data["active"])
            if has_active != (data["status"]=="candidate_present_requires_applicability_review"):
                raise ValueError("GB candidate status inconsistent: "+code)
            targets += int(has_active)
        if (len(records)!=manifest.get("official_active_leaf_codes")
                or targets!=manifest.get("active_target_codes")
                or metadata.get("official_active_leaves")!=str(len(records))):
            raise ValueError("GB compact manifest active-leaf parity failed")
        return {
            "version":metadata["source_version"],
            "as_of":metadata["as_of"],
            "sha256":sha,
            "codes":records,
            "target_codes":targets,
        }
    finally:
        db.close()


def _digest(rows: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(rows),ensure_ascii=False,separators=(",",":")).encode()
    ).hexdigest()


def compare(before_db: str, after_db: str, *, allow_self_check: bool=False,
            sample_limit: int=40) -> dict:
    if not 1 <= sample_limit <= 500:
        raise ValueError("GB comparison sample limit must be 1..500")
    earlier = _snapshot(before_db)
    later = _snapshot(after_db)
    old_v, new_v = version_key(earlier["version"]), version_key(later["version"])
    old_day, new_day = date.fromisoformat(earlier["as_of"]), date.fromisoformat(later["as_of"])
    if new_v < old_v or new_day < old_day:
        raise ValueError("GB comparison must not go backwards in version or date")
    identical_inputs = (earlier["sha256"]==later["sha256"]
                        and earlier["version"]==later["version"]
                        and earlier["as_of"]==later["as_of"])
    if (new_v==old_v and not allow_self_check) or (identical_inputs and not allow_self_check):
        raise ValueError("GB version comparison needs two distinct versions unless self-check")
    old, new = earlier["codes"], later["codes"]
    common = sorted(set(old)&set(new))
    removed = sorted(set(old)-set(new))
    added = sorted(set(new)-set(old))
    gained, lost, changed_active, changed_inactive, changed_status = [],[],[],[],[]
    for code in common:
        prev, curr=old[code],new[code]
        had, has=bool(prev["active"]),bool(curr["active"])
        if not had and has:
            gained.append(code)
        if had and not has:
            lost.append(code)
        if had and has and _digest(prev["active"])!=_digest(curr["active"]):
            changed_active.append(code)
        if _digest(prev["all"])!=_digest(curr["all"]) and code not in changed_active:
            changed_inactive.append(code)
        if prev["status"]!=curr["status"]:
            changed_status.append(code)
    high_risk=bool(removed or lost or changed_active)
    any_change=bool(added or removed or gained or lost or changed_active
                    or changed_inactive or changed_status)
    samples={}
    for key, codes in {
        "added_commodities":added,
        "removed_commodities":removed,
        "gained_active_103_1011":gained,
        "lost_active_103_1011":lost,
        "changed_active_103_1011":changed_active,
        "changed_other_source_evidence":changed_inactive,
        "changed_commodity_status":changed_status
    }.items():
        samples[key]=codes[:sample_limit]
    return {
        "kind":"gb-third-country-compact-version-diff-review-v1",
        "production_eligible":False,
        "payable_rate_verified":False,
        "ni_other_verified":False,
        "old_version":earlier["version"],
        "new_version":later["version"],
        "old_as_of":earlier["as_of"],
        "new_as_of":later["as_of"],
        "as_of_changed":earlier["as_of"]!=later["as_of"],
        "old_sha256":earlier["sha256"],
        "new_sha256":later["sha256"],
        "self_check_only":identical_inputs,
        "old_commodity_codes":len(old),
        "new_commodity_codes":len(new),
        "old_active_target_codes":earlier["target_codes"],
        "new_active_target_codes":later["target_codes"],
        "counts": {
            "added_commodities":len(added),
            "removed_commodities":len(removed),
            "gained_active_103_1011":len(gained),
            "lost_active_103_1011":len(lost),
            "changed_active_103_1011":len(changed_active),
            "changed_other_source_evidence":len(changed_inactive),
            "changed_commodity_status":len(changed_status),
        },
        "samples":samples,
        "high_risk_change_present":high_risk,
        "any_source_change":any_change,
        "manual_business_review_required":True,
        "readiness":"high_risk_change_requires_review" if high_risk else (
            "source_differences_require_review" if any_change else "no_source_difference"),
        "notes":(
            "Changes in as_of may explain effective_status transitions. "
            "Missing/changed measures are not evidence of 0% duty. "
            "This comparison never certifies an applicable GB or NI tariff."
        )
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before",required=True)
    p.add_argument("--after",required=True)
    p.add_argument("--output",default="gb-compact-version-diff.json")
    p.add_argument("--self-check",action="store_true",
                   help="Allow the same snapshot as a structural smoke-test only")
    p.add_argument("--fail-on-high-risk",action="store_true",
                   help="Fail the review CI if a target is removed or active rate evidence changes")
    args=p.parse_args()
    result=compare(args.before,args.after,allow_self_check=args.self_check)
    destination=Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        temp=Path(folder)/"diff.json"
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                        encoding="utf-8")
        os.replace(temp,destination)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.fail_on_high_risk and result["high_risk_change_present"]:
        raise SystemExit("GB candidate loss/active-evidence change requires manual review")


if __name__=="__main__":
    main()
