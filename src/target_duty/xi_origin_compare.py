"""Compare date-specific XI official import measures by actual origin country.

Review-only: comparing filtered measures is NOT a payable-duty calculation and
cannot establish the old desktop 'NI Other' column's intended meaning.

The official frontend uses filter[geographical_area_id] with the real origin.
Fetch an unfiltered baseline plus 1-3 country-specific filtered responses.
Keep each exact raw response compressed with SHA-256 for independently auditable
review; never collapse 103/105 to a single rate or silently infer 0%.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .xi_measure_crawl import parse
from .xi_probe import ACCEPT, get_token

CODE = re.compile(r"\d{10}\Z")
COUNTRY = re.compile(r"[A-Z]{2}\Z")
TYPES = frozenset(("103", "105"))
PUBLIC = "https://www.trade-tariff.service.gov.uk/xi/api/commodities/"
MANAGED = "https://api.trade-tariff.service.gov.uk/xi/api/commodities/"
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE context (
  origin TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  as_of TEXT NOT NULL,
  source_url TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  raw_bytes INTEGER NOT NULL,
  compressed_body BLOB NOT NULL
);
CREATE TABLE measures (
  origin TEXT NOT NULL REFERENCES context(origin),
  ordinal INTEGER NOT NULL,
  measure_id TEXT NOT NULL,
  measure_type TEXT,
  geography TEXT,
  expression TEXT,
  additional_code INTEGER NOT NULL,
  conditions INTEGER NOT NULL,
  exclusions INTEGER NOT NULL,
  raw_json TEXT NOT NULL,
  PRIMARY KEY(origin, ordinal)
);
"""


def url(code: str, when: date, origin: str | None, *, public: bool) -> str:
    if not CODE.fullmatch(code):
        raise ValueError("XI commodity code must contain exactly ten digits")
    if origin is not None and not COUNTRY.fullmatch(origin):
        raise ValueError("Explicit ISO two-letter country code required")
    values = [("as_of",when.isoformat())]
    if origin:
        values.append(("filter[geographical_area_id]", origin))
    return (PUBLIC if public else MANAGED)+code+"?"+urllib.parse.urlencode(values)


def request_one(code: str, when: date, origin: str | None, *,
                public: bool = False, client_id: str = "", secret: str = "",
                max_bytes: int = 8_000_000) -> bytes:
    target=url(code,when,origin,public=public)
    token=None if public else get_token(client_id,secret)
    refreshed=False
    for attempt in range(4):
        headers={"Accept":ACCEPT, "User-Agent":"liao-xi-origin-review/1.0"}
        if not public:
            headers["Authorization"]="Bearer "+token
        try:
            req=urllib.request.Request(target,headers=headers)
            with urllib.request.urlopen(req,timeout=50) as res:
                if res.status != 200:
                    raise ValueError(f"Unexpected XI status: {res.status}")
                body=res.read(max_bytes+1)
            if not body or len(body)>max_bytes:
                raise ValueError("XI source response missing or oversized")
            return body
        except urllib.error.HTTPError as error:
            if error.code==401 and not public and not refreshed:
                token=get_token(client_id,secret)
                refreshed=True
                continue
            if error.code not in (429,500,502,503,504) or attempt==3:
                raise RuntimeError(f"XI source HTTP {error.code}") from None
        except urllib.error.URLError:
            if attempt==3:
                raise RuntimeError("XI source network failure after retries") from None
        time.sleep(min(12,2**attempt))
    raise RuntimeError("XI request did not return a usable response")


def extract(code: str, payload: bytes) -> list[dict]:
    parsed=parse(payload,code)
    measures=[]
    for raw in parsed:
        measure=json.loads(raw["raw_json"])
        relations=measure.get("relationships") or {}
        def present(name: str) -> bool:
            group=relations.get(name) or {}
            value=group.get("data")
            if isinstance(value,list):
                return bool(value)
            return isinstance(value,dict) and bool(value)
        measures.append({
            "measure_id":raw["measure_id"],
            "measure_type":raw["measure_type"],
            "geography":raw["geography_id"],
            "expression":raw["duty_expression"],
            "additional_code":present("additional_code"),
            "conditions":present("measure_conditions"),
            "exclusions":present("excluded_countries"),
            "effective_start":raw["effective_start"],
            "effective_end":raw["effective_end"],
            "raw_json":raw["raw_json"]
        })
    return measures


def build(code: str, when: date, origins: list[str], output: str, *,
          public: bool, fetcher: Callable[[str,date,str|None],bytes],
          max_bytes: int = 8_000_000) -> dict:
    if not CODE.fullmatch(code):
        raise ValueError("Expected exactly ten-digit commodity code")
    if not origins or len(origins)>3 or len(set(origins))!=len(origins):
        raise ValueError("Select 1-3 distinct actual country codes")
    if any(not COUNTRY.fullmatch(s) or s == "GB" and False for s in origins):
        raise ValueError("Origins must be ISO two-letter country codes")
    if not 1 <= max_bytes <= 8_000_000:
        raise ValueError("Invalid source response bound")
    root=Path(output)
    root.parent.mkdir(parents=True, exist_ok=True)
    selection=[None,*origins]
    collected=[]
    for origin in selection:
        body=fetcher(code,when,origin)
        if not isinstance(body,bytes) or not body or len(body)>max_bytes:
            raise ValueError("Official XI source returned invalid bounded bytes")
        rows=extract(code,body)
        collected.append((origin,body,rows))
    with tempfile.TemporaryDirectory(dir=root.parent) as folder:
        stage=Path(folder)/"xi-country-review.db"
        db=sqlite3.connect(stage)
        try:
            db.executescript(SCHEMA)
            db.execute("PRAGMA foreign_keys=ON")
            for origin,body,rows in collected:
                key=origin or "UNFILTERED"
                db.execute("INSERT INTO context VALUES (?,?,?,?,?,?,?)",
                           (key,code,when.isoformat(),url(code,when,origin,public=public),
                            hashlib.sha256(body).hexdigest(),len(body),zlib.compress(body)))
                for index,m in enumerate(rows):
                    db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (key,index,m["measure_id"],m["measure_type"],m["geography"],
                                m["expression"],int(m["additional_code"]),int(m["conditions"]),
                                int(m["exclusions"]),m["raw_json"]))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("XI origin review SQLite integrity check failed")
        finally:
            db.close()
        stage.replace(root)
    original_ids={m["measure_id"] for m in collected[0][2]}
    comparisons=[]
    for origin,body,rows in collected[1:]:
        origin_ids={m["measure_id"] for m in rows}
        candidates=[m for m in rows if m["measure_type"] in TYPES]
        comparisons.append({
            "origin":origin,
            "filtered_measure_count":len(rows),
            "third_country_103_105_count":len(candidates),
            "third_country_measure_types":sorted(set(
                m["measure_type"] for m in candidates)),
            "third_country_measure_evidence": [{
                "id":m["measure_id"],"type":m["measure_type"],
                "geography":m["geography"],"expression":m["expression"],
                "has_conditions":m["conditions"],
                "has_additional_code":m["additional_code"],
                "has_exclusions":m["exclusions"],
            } for m in candidates],
            "missing_vs_unfiltered":sorted(original_ids-origin_ids),
            "added_vs_unfiltered":sorted(origin_ids-original_ids),
            "raw_source_sha256":hashlib.sha256(body).hexdigest(),
            "source_url":url(code,when,origin,public=public),
            "applicable_rate":None,
            "status":"manual_applicability_review_required",
        })
    report={
        "kind":"xi-country-filter-official-comparison-v1",
        "production_eligible":False,"ni_other_verified":False,
        "as_of":when.isoformat(),"code":code,"public_canary":public,
        "unfiltered_measure_count":len(collected[0][2]),
        "unfiltered_sha256":hashlib.sha256(collected[0][1]).hexdigest(),
        "comparisons":comparisons,
        "evidence_sha256":hashlib.sha256(root.read_bytes()).hexdigest(),
        "source":("official public XI V2 canary" if public else "official managed XI V2"),
        "warning":"Country-specific filtered measures are not an automatically payable NI Other rate.",
    }
    root.with_suffix(".json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--code",required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--origins",required=True,help="1-3 country codes, e.g. US,CN")
    p.add_argument("--public-canary",action="store_true")
    p.add_argument("--output",default="xi-origin-comparison.db")
    p.add_argument("--min-interval",type=float,default=1.5)
    args=p.parse_args()
    origins=[s.strip() for s in args.origins.split(",")]
    if not 1 <= args.min_interval <= 30:
        raise ValueError("Request interval must be between 1 and 30 seconds")
    last=[False]
    def fetch(code,when,origin):
        if last[0]:
            time.sleep(args.min_interval)
        last[0]=True
        return request_one(code,when,origin,public=args.public_canary,
                           client_id=os.getenv("HMRC_CLIENT_ID",""),
                           secret=os.getenv("HMRC_CLIENT_SECRET",""))
    report=build(args.code,date.fromisoformat(args.as_of),origins,args.output,
                 public=args.public_canary,fetcher=fetch)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
