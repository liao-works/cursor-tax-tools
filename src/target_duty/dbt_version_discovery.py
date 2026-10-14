"""Discover actual newest UK Tariff version through DBT's dataset metadata API.

The commodities-report/latest CSV alias can return HTTP 404. Do NOT infer
freshness from a stale pinned data import, HTTP redirection, or the highest
version whose old schema happens to work.

Official documented API:
  GET /v1/datasets/uk-tariff-2021-01-01/versions?format=json
  GET /v1/datasets/uk-tariff-2021-01-01/versions/{version}/tables?format=json

Probe makes two tiny authenticated-by-origin public metadata requests.
Any missing latest table/schema => BLOCKED, never auto-fallback.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BASE = "https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01"
VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
REQUIRED_TABLES = frozenset(("commodities-report", "measures-on-declarable-commodities"))
# A compatibility test is not a legal-duties or data-freshness certificate.
KIND = "dbt-official-version-and-table-discovery-v1"


def version_key(s: str) -> tuple[int,int,int]:
    match = VERSION.fullmatch(s)
    if not match:
        raise ValueError("Unexpected official DBT version id")
    return tuple(map(int,match.groups()))


def _get(url: str, *, opener: Callable | None = None) -> dict:
    # No arbitrary host or redirects to another origin for metadata.
    p=urllib.parse.urlsplit(url)
    if p.scheme!="https" or p.netloc!="data.api.trade.gov.uk" or p.username:
        raise ValueError("Invalid DBT metadata origin")
    opener=opener or urllib.request.urlopen
    request=urllib.request.Request(url,headers={
        "Accept":"application/json", "User-Agent":"liao-uk-tariff-version-discovery/1.0"})
    with opener(request,timeout=35) as response:
        if response.status!=200:
            raise ValueError("Official DBT metadata returned unexpected status")
        resolved=urllib.parse.urlsplit(response.geturl())
        if resolved.scheme!="https" or resolved.netloc!="data.api.trade.gov.uk":
            raise ValueError("Official DBT metadata redirected to another origin")
        raw=response.read(4_000_001)
    if not raw or len(raw)>4_000_000:
        raise ValueError("Empty or oversized official DBT metadata response")
    try:
        document=json.loads(raw)
    except (ValueError,UnicodeError) as error:
        raise ValueError("Malformed official DBT metadata JSON") from error
    if not isinstance(document,dict):
        raise ValueError("DBT metadata root must be a JSON object")
    return document


def parse_versions(payload: dict) -> list[str]:
    raw=payload.get("versions")
    if not isinstance(raw,list) or not raw or len(raw)>100_000:
        raise ValueError("Official DBT version list missing or unreasonable")
    found=set()
    for item in raw:
        if not isinstance(item,dict):
            raise ValueError("DBT version must be an object")
        ver=item.get("id")
        if not isinstance(ver,str):
            raise ValueError("DBT version has no version ID")
        version_key(ver)
        if ver in found:
            raise ValueError("DBT version catalogue contains duplicates")
        found.add(ver)
    return sorted(found,key=version_key,reverse=True)


def parse_tables(payload: dict) -> list[str]:
    raw=payload.get("tables")
    if not isinstance(raw,list) or not raw or len(raw)>1000:
        raise ValueError("Official DBT table list missing or unreasonable")
    found=set()
    for item in raw:
        if not isinstance(item,dict):
            raise ValueError("DBT table entry must be an object")
        name=item.get("id")
        if not isinstance(name,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*",name):
            raise ValueError("Unexpected DBT table ID")
        if name in found:
            raise ValueError("Duplicate DBT table in response")
        found.add(name)
    return sorted(found)


def discover(*, previous_pin: str | None = None,
             opener: Callable | None = None) -> dict:
    if previous_pin is not None:
        version_key(previous_pin)
    result={
        "kind":KIND,
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "source":"official-DBT-data-API-metadata",
        "dataset":"uk-tariff-2021-01-01",
        "version_inventory_url":BASE+"/versions?format=json",
        "required_tables":sorted(REQUIRED_TABLES),
        "replayed_previous_version":previous_pin,
        "newest_official_version":None,
        "newest_version_tables":None,
        "newer_than_replayed":None,
        "version_discovered":False,
        "required_tables_present":False,
        "schema_compatibility_proven":False,
        "full_data_replayed_for_newest":False,
        "freshness_proven":False,
        "publish_eligible":False,
        "production_eligible":False,
        "state":"source_unavailable",
        "error":None,
    }
    try:
        versions=parse_versions(_get(result["version_inventory_url"],opener=opener))
        newest=versions[0]
        result["newest_official_version"]=newest
        result["version_discovered"]=True
        result["available_version_count"]=len(versions)
        result["newer_than_replayed"]=(
            version_key(newest)>version_key(previous_pin)
            if previous_pin else None
        )
        tables_url=BASE+"/versions/"+newest+"/tables?format=json"
        result["table_inventory_url"]=tables_url
        tables=parse_tables(_get(tables_url,opener=opener))
        result["newest_version_tables"]=tables
        missing=sorted(REQUIRED_TABLES-set(tables))
        result["missing_required_tables"]=missing
        result["required_tables_present"]=not missing
        result["state"]="schema_preflight_required" if not missing else "unsupported_newest_tables"
    except (ValueError,urllib.error.HTTPError,urllib.error.URLError,TimeoutError,OSError) as err:
        result["error"]=f"{type(err).__name__}: {str(err)[:400]}"
        if result["version_discovered"]:
            result["state"]="table_inventory_unavailable"
    # A metadata-only check never proves columns, row counts or publication fitness.
    return result


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous-pin",default="v4.0.1626")
    p.add_argument("--output",default="dbt-version-discovery.json")
    p.add_argument("--require-available",action="store_true",
                   help="Block if source metadata or required tables unavailable")
    args=p.parse_args()
    result=discover(previous_pin=args.previous_pin)
    Path(args.output).write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.require_available and result["state"]!="schema_preflight_required":
        raise SystemExit("Official DBT newest dataset metadata/tables unavailable or incompatible")


if __name__=="__main__":
    main()
