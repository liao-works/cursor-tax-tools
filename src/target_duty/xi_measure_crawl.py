"""Resumable, bounded XI HMRC raw import-measure audit.

Input MUST be a separately enumerated XI nomenclature SQLite, not GB codes.
Store every import measure and original JSON:API response with exact SHA-256.
Never produce a NI Other tariff rate, even when type 103/1011 exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .xi_catalogue import fetch as fetch_endpoint
from .xi_probe import get_token, refs

CODE = re.compile(r"\d{10}\Z")
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
 code TEXT PRIMARY KEY, status TEXT NOT NULL, response_sha256 TEXT,
 bytes INTEGER, checked_at TEXT NOT NULL, error TEXT, compressed_body BLOB
);
CREATE TABLE IF NOT EXISTS measures (
 code TEXT NOT NULL REFERENCES requests(code),
 ordinal INTEGER NOT NULL,
 measure_id TEXT NOT NULL, measure_type TEXT,
 geography_id TEXT, duty_expression TEXT,
 effective_start TEXT, effective_end TEXT,
 raw_json TEXT NOT NULL,
 PRIMARY KEY(code,ordinal)
);
CREATE INDEX IF NOT EXISTS idx_xi_import_type ON measures(code,measure_type,geography_id);
"""


def candidates(xi_catalogue: str) -> list[str]:
    db=sqlite3.connect(Path(xi_catalogue).resolve().as_uri()+"?mode=ro",uri=True)
    try:
        cols={r[1] for r in db.execute("PRAGMA table_info(nomenclature)")}
        if not {"code","declarable","active"}.issubset(cols):
            raise ValueError("Expected independent official XI catalogue schema")
        result=sorted({code for (code,) in db.execute(
            "SELECT code FROM nomenclature WHERE active=1 AND declarable=1")})
        if not result:
            raise ValueError("No XI official declarable commodities in catalogue")
        return result
    finally:
        db.close()


def parse(payload: bytes, code: str) -> list[dict]:
    if not CODE.fullmatch(code):
        raise ValueError("Invalid XI commodity code")
    try:
        doc=json.loads(payload)
    except (ValueError,UnicodeError) as err:
        raise ValueError("HMRC XI response is not valid JSON") from err
    data=doc.get("data")
    if not isinstance(data,dict):
        raise ValueError("XI commodity response lacks data object")
    attr=data.get("attributes") or {}
    returned=attr.get("goods_nomenclature_item_id")
    if returned is not None and returned != code:
        raise ValueError("XI commodity response returned another code")
    relationships=data.get("relationships")
    if not isinstance(relationships,dict) or "import_measures" not in relationships:
        raise ValueError("XI response lacks explicit import_measures relationship")
    import_measures=relationships["import_measures"]
    if not isinstance(import_measures,dict) or not isinstance(import_measures.get("data"),list):
        raise ValueError("XI import_measures relationship must be a complete list")
    included={(node.get("type"),str(node.get("id"))):node
              for node in doc.get("included",[]) if isinstance(node,dict)}
    relations=refs(data,"import_measures")
    extracted=[]
    seen=set()
    for ref in relations:
        key=("measure",str(ref.get("id")))
        measure=included.get(key)
        if measure is None:
            raise ValueError(f"Unresolved XI import measure relationship: {key[1]}")
        if key in seen:
            raise ValueError(f"Duplicate XI measure link: {key[1]}")
        seen.add(key)
        mt=refs(measure,"measure_type")
        geo=refs(measure,"geographical_area")
        attrs=measure.get("attributes") or {}
        extracted.append({
            "measure_id":key[1],
            "measure_type":str(mt[0].get("id")) if mt else None,
            "geography_id":str(geo[0].get("id")) if geo else None,
            "duty_expression":attrs.get("resolved_duty_expression"),
            "effective_start":attrs.get("effective_start_date"),
            "effective_end":attrs.get("effective_end_date"),
            "raw_json":json.dumps(measure,ensure_ascii=False,sort_keys=True),
        })
    # A usable response with no import measures is valid *evidence of zero
    # returned rows*, NEVER evidence of 0% duty.
    return extracted


def initialize(db: sqlite3.Connection, as_of: date, catalogue_sha: str):
    db.executescript(SCHEMA)
    properties={"region":"XI","as_of":as_of.isoformat(),
                "catalogue_sha256":catalogue_sha,
                "production_eligible":"false",
                "other_semantics_verified":"false"}
    for k,v in properties.items():
        existing=db.execute("SELECT value FROM config WHERE key=?", (k,)).fetchone()
        if existing and existing[0]!=v:
            raise ValueError("XI checkpoint incompatible with region/date/catalogue")
        db.execute("INSERT OR IGNORE INTO config VALUES (?,?)", (k,v))
    db.commit()


def collect(catalogue: str, output: str, *, as_of: date, limit: int=20,
            fetcher: Callable[[str,date],bytes], start_index: int | None = None,
            skip_successes_from: str | None = None,
            max_bytes: int=8_000_000,
            sleep_seconds: float=0.0) -> dict:
    """Bounded incremental collection; checkpoint every code, safe re-runs.

    Errors are checkpointed but retried on the next run. A changed catalogue
    invalidates the checkpoint to prevent falsely claiming full coverage.
    """
    if not 1 <= limit <= 1000 or not 0 <= sleep_seconds <= 120:
        raise ValueError("Invalid XI batch bounds")
    codes=candidates(catalogue)
    if start_index is not None and not 0 <= start_index < len(codes):
        raise ValueError("XI explicit shard start index outside official catalogue")
    catalogue_sha=hashlib.sha256(Path(catalogue).read_bytes()).hexdigest()
    prior_ok: set[str] = set()
    if skip_successes_from:
        prior_path=Path(skip_successes_from).resolve()
        if not prior_path.is_file():
            raise ValueError("Previous XI checkpoint missing")
        if prior_path==Path(output).resolve():
            raise ValueError("Previous XI checkpoint cannot be overwritten")
        prior=sqlite3.connect(prior_path.as_uri()+"?mode=ro",uri=True)
        try:
            if prior.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("Previous XI checkpoint integrity failure")
            config=dict(prior.execute("SELECT key,value FROM config"))
            for key,value in (
                ("region","XI"),
                ("catalogue_sha256",catalogue_sha),
                ("as_of",as_of.isoformat()),
                ("production_eligible","false"),
                ("other_semantics_verified","false")
            ):
                if config.get(key)!=value:
                    raise ValueError("Previous XI checkpoint provenance mismatch: "+key)
            prior_ok={code for (code,) in prior.execute(
                "SELECT code FROM requests WHERE status='ok'")}
            if not prior_ok.issubset(set(codes)):
                raise ValueError("Previous XI checkpoint contains unlisted commodity codes")
        finally:
            prior.close()
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(target)
    try:
        initialize(db,as_of,catalogue_sha)
        completed={code for (code,) in db.execute(
            "SELECT code FROM requests WHERE status='ok'")}
        completed.update(prior_ok)
        if start_index is None:
            selected=[code for code in codes if code not in completed][:limit]
        else:
            selected=[code for code in codes[start_index:start_index+limit]
                      if code not in completed]
        for index, code in enumerate(selected):
            now=datetime.now(timezone.utc).isoformat()
            body=None
            rows=[]
            try:
                body=fetcher(code,as_of)
                if not isinstance(body,bytes) or not body or len(body)>max_bytes:
                    raise ValueError("XI response is empty, oversized or not bytes")
                rows=parse(body,code)
                status,error="ok",None
            except Exception as ex:
                status,error="source_error",f"{type(ex).__name__}: {str(ex)[:250]}"
            db.execute("DELETE FROM measures WHERE code=?", (code,))
            db.execute("""INSERT OR REPLACE INTO requests
                VALUES (?,?,?,?,?,?,?)""",
                (code,status,hashlib.sha256(body).hexdigest() if body else None,
                 len(body) if body else None,now,error,zlib.compress(body) if body else None))
            if status=="ok":
                for ordinal,m in enumerate(rows):
                    db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?)",
                               (code,ordinal,m["measure_id"],m["measure_type"],
                                m["geography_id"],m["duty_expression"],
                                m["effective_start"],m["effective_end"],m["raw_json"]))
            db.commit()
            if index+1 < len(selected) and sleep_seconds:
                time.sleep(sleep_seconds)
        ok=db.execute("SELECT COUNT(*) FROM requests WHERE status='ok'").fetchone()[0]
        errors=db.execute(
            "SELECT COUNT(*) FROM requests WHERE status='source_error'").fetchone()[0]
        measure_rows=db.execute("SELECT COUNT(*) FROM measures").fetchone()[0]
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("XI measure audit integrity failure")
    finally:
        db.close()
    summary={
        "kind":"xi-resumable-raw-import-measures-v1",
        "production_eligible":False,
        "other_semantics_verified":False,
        "source_region":"XI",
        "as_of":as_of.isoformat(),
        "independent_xi_catalogue_codes":len(codes),
        "attempted_this_run":len(selected),
        "explicit_start_index":start_index,
        "previous_checkpoint_successes_skipped":len(prior_ok),
        "successful_codes":ok, "failed_codes":errors,
        "remaining_codes":len(codes)-ok,
        "measure_rows":measure_rows,
        "all_codes_fetched":ok==len(codes) and errors==0,
        "warning":"Response evidence is not a confirmed NI Other rate or complete NI duty calculation.",
    }
    Path(str(target)+".metadata.json").write_text(
        json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return summary


def managed_fetcher(client_id: str, secret: str, *, public: bool=False):
    """Rate-limited call adapter; refresh expired managed token, retry 429/5xx.

    The outer collect() owns the per-code checkpoint. HTTP errors MUST NOT
    be converted into an empty import measure list.
    """
    token=None if public else get_token(client_id,secret)
    def fetch_one(code: str, as_of: date) -> bytes:
        import urllib.error
        import urllib.parse
        import urllib.request
        nonlocal token
        refreshed_this_request=False
        if not CODE.fullmatch(code):
            raise ValueError("Invalid XI code")
        endpoint=("https://www.trade-tariff.service.gov.uk/xi/api/commodities/" if public
                  else "https://api.trade-tariff.service.gov.uk/xi/api/commodities/")
        url=endpoint+code+"?"+urllib.parse.urlencode({"as_of":as_of.isoformat()})
        for attempt in range(5):
            headers={"Accept":"application/vnd.hmrc.2.0+json",
                     "User-Agent":"liao-xi-measure-evidence-shadow/1.0"}
            if not public:
                headers["Authorization"]="Bearer "+token
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url,headers=headers),timeout=50
                ) as response:
                    if response.status!=200:
                        raise RuntimeError(f"Unexpected HMRC XI status: {response.status}")
                    return response.read(8_000_001)
            except urllib.error.HTTPError as exc:
                status=exc.code
                if status==401 and not public and not refreshed_this_request:
                    token=get_token(client_id,secret)
                    refreshed_this_request=True
                    continue
                if status not in (429,500,502,503,504) or attempt==4:
                    raise RuntimeError(f"HMRC XI HTTP {status}") from None
                retry_after=exc.headers.get("Retry-After","") if exc.headers else ""
                delay=min(60.0,max(1.0,float(retry_after))) if retry_after.isdecimal() else min(30.0,2.0**attempt)
                time.sleep(delay)
            except urllib.error.URLError:
                if attempt==4:
                    raise RuntimeError("HMRC XI network unavailable after retries") from None
                time.sleep(min(30.0,2.0**attempt))
        raise RuntimeError("Unreachable XI managed request retry state")
    return fetch_one

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--as-of",required=True)
    p.add_argument("--output",default="xi-raw-measures-shadow.db")
    p.add_argument("--limit",type=int,default=20)
    p.add_argument("--start-index",type=int,default=None,
                   help="Optional index for independent, explicitly bounded shard")
    p.add_argument("--skip-successes-from",help="Prior same-catalogue XI checkpoint; skip successful codes without re-fetching")
    p.add_argument("--min-interval",type=float,default=2.0)
    p.add_argument("--public-canary",action="store_true")
    a=p.parse_args()
    report=collect(
        a.catalogue,a.output,as_of=date.fromisoformat(a.as_of),limit=a.limit,
        start_index=a.start_index,
        skip_successes_from=a.skip_successes_from,
        fetcher=managed_fetcher(os.getenv("HMRC_CLIENT_ID",""),
                                os.getenv("HMRC_CLIENT_SECRET",""),
                                public=a.public_canary),
        sleep_seconds=a.min_interval)
    print(json.dumps(report,indent=2,ensure_ascii=False))
    if report["failed_codes"]:
        raise SystemExit("XI source errors present; no production eligibility")


if __name__=="__main__":
    main()
