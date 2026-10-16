"""Independent DBT latest-alias health evidence without fallback or publication.

Use the SAME official commodities-report source as the pinned importer. This
probe opens a bounded HTTP GET response and reads NO commodity rows; its sole
purpose is to determine whether the latest alias resolves and whether it
matches the separately replay-verified pinned version.

Unavailable 'latest' never silently falls back to a previous valid snapshot.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .catalog import source_url

PIN = re.compile(r"/versions/(v[0-9]+(?:\.[0-9]+)*)/tables/")
VERSION = re.compile(r"v[0-9]+(?:\.[0-9]+)*\Z")


def probe(pinned_version: str, *, opener=None) -> dict:
    if not VERSION.fullmatch(pinned_version):
        raise ValueError("DBT health check requires an immutable pinned version")
    url=source_url("latest")
    opener = opener or urllib.request.urlopen
    status, resolved, problem = None,None,None
    try:
        req=urllib.request.Request(url,headers={
            "Accept":"text/csv",
            "User-Agent":"liao-dbt-source-health-check/1.0",
            "Range":"bytes=0-128",
        })
        with opener(req,timeout=30) as response:
            status=response.status
            resolved=response.geturl()
    except urllib.error.HTTPError as e:
        status=e.code
        problem=f"DBT official latest alias returned HTTP {e.code}"
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        problem=f"DBT official latest alias network error: {type(e).__name__}"
    found=PIN.search(resolved or "")
    resolved_pin=found.group(1) if found else None
    reachable=status in (200,206) and resolved_pin is not None
    same_version=reachable and resolved_pin==pinned_version
    return {
        "kind":"dbt-uk-tariff-latest-alias-health-v1",
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "official_url":url,
        "http_status":status,
        "resolved_url":resolved,
        "resolved_version":resolved_pin,
        "replay_pinned_version":pinned_version,
        "latest_alias_usable":bool(reachable),
        "latest_equals_replayed_version":bool(same_version),
        "freshness_proven":bool(same_version),
        "production_eligible":False,
        "should_publish":False,
        "error":problem if problem else
                 (None if reachable else "No verifiable official pinned-version redirect"),
        "warning":(
            "This is an alias reachability comparison only. Re-check full "
            "pinned source coverage and NI semantics before any production release."
        ),
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pinned-version",required=True)
    p.add_argument("--output",default="dbt-latest-alias-health.json")
    p.add_argument("--require-match",action="store_true",
                   help="Fail a freshness/release gate if latest is absent or has changed")
    args=p.parse_args()
    report=probe(args.pinned_version)
    Path(args.output).write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.require_match and not report["freshness_proven"]:
        raise SystemExit("DBT latest alias unavailable or newer than pinned replay: refuse promotion")


if __name__=="__main__":
    main()
