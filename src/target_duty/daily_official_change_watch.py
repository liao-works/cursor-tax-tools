"""Daily GB official-version and XI public sample / measure-date change evidence.

This is a SIGNAL detector, never a proof that all 16,728 NI commodities
are unchanged and NEVER a payable-rate calculator or production publisher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .dbt_version_discovery import discover, version_key
from .xi_measure_crawl import managed_fetcher

HEX=re.compile(r"[0-9a-f]{64}\Z")
CODE=re.compile(r"[0-9]{10}\Z")
BASELINE_KIND="xi-daily-source-canary-baseline-v1"


def check_baseline(baseline: dict, on: date) -> None:
    if not isinstance(baseline,dict) or baseline.get("kind")!=BASELINE_KIND:
        raise ValueError("Invalid frozen original XI source baseline")
    start=date.fromisoformat(baseline["source_as_of"])
    if start>on:
        raise ValueError("Cannot use a future XI baseline")
    if baseline.get("ni_other_verified") is not False or baseline.get("production_eligible") is not False:
        raise ValueError("XI baseline must be explicitly source-only")
    if baseline.get("original_codes_verified",0)<10000:
        raise ValueError("No independently accepted full XI inventory")
    if not HEX.fullmatch(str(baseline.get("catalogue_sha256"))):
        raise ValueError("Missing pinned XI catalogue SHA")
    if not any(HEX.fullmatch(str(baseline.get(k))) for k in
               ("original_candidate_detail_csv_sha256","source_merged_db_sha256")):
        raise ValueError("Missing independently accepted XI original-source digest")
    codes=baseline.get("canary_response_sha256")
    if not isinstance(codes,dict) or len(codes)<6 or len(codes)>30:
        raise ValueError("Six to thirty pinned source canaries required")
    for code,sha in codes.items():
        if not CODE.fullmatch(code) or not HEX.fullmatch(str(sha)):
            raise ValueError("Malformed original XI code/response fingerprint")
    boundaries=baseline.get("known_103_105_end_dates")
    if not isinstance(boundaries,dict):
        raise ValueError("Frozen XI measure expiry ledger missing")
    for d,n in boundaries.items():
        day=date.fromisoformat(d)
        if day < start or isinstance(n,bool) or not isinstance(n,int) or n<=0:
            raise ValueError("Invalid frozen candidate end-date proof")


def classify(baseline: dict, *, official_gb: dict, gb_base_version: str,
             ni_response_sha: dict, on: date, source_errors: dict|None=None,
             max_baseline_age_days: int=7) -> dict:
    """Triages a proven limited-scope signal, never claims a full NI diff."""
    check_baseline(baseline,on)
    version_key(gb_base_version)
    if max_baseline_age_days<1 or max_baseline_age_days>30:
        raise ValueError("Invalid full-source baseline freshness budget")
    expected=set(baseline["canary_response_sha256"])
    errors=dict(source_errors or {})
    if set(ni_response_sha)|set(errors)!=expected or set(ni_response_sha)&set(errors):
        raise ValueError("All pinned NI canaries need a success/error receipt")
    for code,sha in ni_response_sha.items():
        if not HEX.fullmatch(str(sha)):
            raise ValueError("Invalid current XI source digest")
    if not isinstance(official_gb,dict):
        official_gb={}
    ready=official_gb.get("state")=="ready_for_full_data_replay"
    new=official_gb.get("newest_official_version")
    version_changed=None
    if ready and isinstance(new,str):
        try:
            version_changed=version_key(new)>version_key(gb_base_version)
            if version_key(new)<version_key(gb_base_version):
                errors["GB"]="Official GB newest pin is older than verified baseline"
        except ValueError:
            errors["GB"]="Official GB returned an invalid version"
    else:
        errors["GB"]="Official DBT version/schema unavailable: "+str(official_gb.get("state"))
    changed=sorted(code for code,sha in ni_response_sha.items()
                   if baseline["canary_response_sha256"][code]!=sha)
    dated=date.fromisoformat(baseline["source_as_of"])
    elapsed=(on-dated).days
    # Include all unreviewed expiries since frozen source, not just today's,
    # otherwise a missed daily run can silently skip a legal boundary.
    ending=[{"date":day,"candidate_measures":n} for day,n in
            sorted(baseline["known_103_105_end_dates"].items())
            if dated<date.fromisoformat(day)<=on]
    reasons=[]
    if version_changed:
        reasons.append("GB_OFFICIAL_VERSION_CHANGED_FULL_REPLAY_REQUIRED")
    if changed:
        reasons.append("NI_CANARY_RAW_CHANGE_SCOPE_UNKNOWN")
    if ending:
        reasons.append("NI_KNOWN_103_105_EFFECTIVE_END_BOUNDARIES")
    if elapsed>max_baseline_age_days:
        reasons.append("NI_WHOLE_SOURCE_BASELINE_TOO_OLD")
    if errors:
        reasons.append("OFFICIAL_SOURCE_FETCH_OR_SCHEMA_FAILURE")
    return {
        "kind":"gb-ni-daily-official-change-signal-v1",
        "checked_as_of":on.isoformat(),
        "gb_baseline_pin":gb_base_version,
        "gb_official_newest_pin":new,
        "gb_official_schema_ready":ready,
        "gb_version_changed":version_changed,
        "xi_source_baseline_as_of":dated.isoformat(),
        "xi_baseline_age_days":elapsed,
        "xi_canaries_checked":len(ni_response_sha),
        "xi_canaries_expected":len(expected),
        "xi_changed_raw_response_codes":changed,
        "xi_sample_covers_entire_source":False,
        "xi_full_source_changes_excluded":False,
        "known_ni_third_country_expirations_since_baseline":ending,
        "unknown_future_starts_or_other_measure_expiries_possible":True,
        "source_errors":errors,
        "review_reasons":reasons,
        "review_state":"REVIEW_OR_FULL_REPLAY_REQUIRED" if reasons else "NO_SIGNALS_IN_CHECKED_SCOPE_ONLY",
        "release_permitted":False,
        "ni_other_payable_verified":False,
        "production_database_mutated":False,
        "warning":"GB version and 6+ XI canaries are only change indicators; all-country measures, future effective starts, preferences and NI payable Other remain unverified."
    }


def inspect(baseline: dict, *, on: date, gb_base_version: str,
            fetcher=None, gb_metadata=None, sleep_seconds: float=0.4) -> dict:
    check_baseline(baseline,on)
    fetcher=fetcher or managed_fetcher("","",public=True)
    try:
        official=gb_metadata if gb_metadata is not None else discover(previous_pin=gb_base_version)
    except Exception as exc:
        official={"state":"source_unavailable","error":str(exc)[:250]}
    signatures,errors={},{}
    for code in sorted(baseline["canary_response_sha256"]):
        try:
            raw=fetcher(code,on)
            if not isinstance(raw,bytes) or len(raw)==0 or len(raw)>8_000_000:
                raise ValueError("Empty/non-byte/oversized official XI response")
            doc=json.loads(raw)
            if not isinstance(doc,dict) or not isinstance(doc.get("data"),dict):
                raise ValueError("Malformed original XI JSON:API response")
            data=doc["data"]
            if str(data.get("type"))!="commodity" or str(data.get("attributes",{}).get("goods_nomenclature_item_id"))!=code:
                raise ValueError("Unexpected official XI response commodity identity")
            signatures[code]=hashlib.sha256(raw).hexdigest()
        except Exception as exc:
            errors[code]=type(exc).__name__+": "+str(exc)[:160]
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return classify(baseline,official_gb=official,gb_base_version=gb_base_version,
                    ni_response_sha=signatures,on=on,source_errors=errors)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",default="ops/xi-daily-canary-baseline-20261014.json")
    p.add_argument("--gb-baseline-version",default="v4.0.1627")
    p.add_argument("--as-of",default=date.today().isoformat())
    p.add_argument("--output",default="daily-official-change-review.json")
    a=p.parse_args()
    baseline=json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    receipt=inspect(baseline,on=date.fromisoformat(a.as_of),
                    gb_base_version=a.gb_baseline_version)
    Path(a.output).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",
                              encoding="utf-8")
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
    if receipt["source_errors"]:
        raise SystemExit("Official source failure: review required, DO NOT silently treat as no change")
    # Even a clean scan never triggers a database install or release.


if __name__=="__main__":
    main()
