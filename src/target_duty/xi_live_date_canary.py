"""Check frozen XI response measures against new-as-of PUBLIC XI API (canary).

Records current-vs-frozen candidate deltas only; never stamps full XI as fresh,
never infers NI duty. Captures both response SHAs with precise source dates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import zlib
from datetime import date,datetime,timezone
from pathlib import Path

from .xi_duty_projection import project
from .xi_measure_crawl import managed_fetcher


def compare(original: bytes, current: bytes, code: str, *,
            earlier: date, newer: date) -> dict:
    if newer < earlier:
        raise ValueError("Canary cannot backdate the live source")
    def index(raw,day):
        return {p["measure_id"]: (
            p["measure_type"],p["geography_id"],p["candidate_expression"],
            p["effective_start"],p["effective_end"],
            p["condition_count"],p["condition_group_count"],
            p["excluded_country_count"])
            for p in project(raw,code,day)}
    older=index(original,earlier)
    latest=index(current,newer)
    common=set(older)&set(latest)
    changed=sorted(k for k in common if older[k]!=latest[k])
    return {
        "code":code,
        "baseline_as_of":earlier.isoformat(),
        "current_as_of":newer.isoformat(),
        "original_response_sha256":hashlib.sha256(original).hexdigest(),
        "new_response_sha256":hashlib.sha256(current).hexdigest(),
        "candidate_count_before":len(older),
        "candidate_count_after":len(latest),
        "added_measure_ids":sorted(set(latest)-set(older)),
        "removed_measure_ids":sorted(set(older)-set(latest)),
        "changed_measure_ids":changed,
        "source_document_hash_changed":hashlib.sha256(original).digest()!=hashlib.sha256(current).digest(),
        "candidate_measures_changed":bool(changed or set(older)!=set(latest)),
        "duty_rate_certified":False,
        "full_xi_freshness_verified":False
    }


def audit(baseline_shard: str, *, earlier: date, newer: date,
          codes: list[str], output: str, fetcher=None) -> dict:
    if len(set(codes)) != len(codes) or not 1 <= len(codes) <= 30:
        raise ValueError("One to thirty nonduplicated public source codes required")
    if newer < earlier:
        raise ValueError("Newer date precedes frozen source")
    if Path(output).resolve()==Path(baseline_shard).resolve():
        raise ValueError("Never overwrite original raw source")
    if fetcher is None:
        fetcher=managed_fetcher("","",public=True)
    findings=[]
    with sqlite3.connect(Path(baseline_shard).resolve().as_uri()+"?mode=ro",uri=True) as db:
        config=dict(db.execute("SELECT key,value FROM config"))
        if config.get("region")!="XI" or config.get("as_of")!=earlier.isoformat():
            raise ValueError("Baseline shard provenance mismatch")
        for code in codes:
            if len(code)!=10 or not code.isdigit():
                raise ValueError("Invalid commodity code")
            rec=db.execute("SELECT status,response_sha256,compressed_body FROM requests WHERE code=?",(code,)).fetchone()
            if not rec or rec[0]!="ok":
                raise ValueError("Baseline XI original response absent: "+code)
            original=zlib.decompress(rec[2])
            if hashlib.sha256(original).hexdigest()!=rec[1]:
                raise ValueError("Baseline original response SHA mismatch: "+code)
            try:
                fresh=fetcher(code,newer)
                finding=compare(original,fresh,code,earlier=earlier,newer=newer)
                finding["fetch_status"]="verified_official_public_response"
            except Exception as exc:
                finding={"code":code,"fetch_status":"source_error",
                         "error":str(exc)[:240]}
            findings.append(finding)
            time.sleep(.8)
    results={
        "kind":"xi-as-of-live-public-canary-v1",
        "baseline_as_of":earlier.isoformat(),
        "new_as_of":newer.isoformat(),
        "fetched":sum(r["fetch_status"]=="verified_official_public_response" for r in findings),
        "failed":sum(r["fetch_status"]=="source_error" for r in findings),
        "semantic_candidate_changes":sum(r.get("candidate_measures_changed",False) for r in findings),
        "samples":findings,
        "measures_eligible_for_payable_rates":0,
        "full_xi_freshness_verified":False,
        "ni_other_verified":False,
        "production_eligible":False,
        "checked_at":datetime.now(timezone.utc).isoformat()
    }
    Path(output).write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-shard",required=True)
    p.add_argument("--baseline-date",required=True)
    p.add_argument("--current-date",required=True)
    p.add_argument("--codes",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    codes=[c.strip() for c in a.codes.split(",")]
    result=audit(a.baseline_shard,earlier=date.fromisoformat(a.baseline_date),
                 newer=date.fromisoformat(a.current_date),
                 codes=codes,output=a.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result["failed"]:
        raise SystemExit("Unverified live source canary; no rate release")


if __name__=="__main__":
    main()
