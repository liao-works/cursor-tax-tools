"""Build a deterministic desktop database from two independently complete sources.

Publishes only unambiguous active 103 (ordinary MFN/third-country) percent expressions.
Other categories/conditions remain empty, never guessed as zero. Both original
source snapshots must replay, hash-verify and cover their full active catalogue.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from .full_source_delta_scope import _source
from .gb_full_measures import _catalogue
from .catalog import NA
from .xi_measure_crawl import candidates
from .xi_public_full_capture import verify_public
from .xi_acceptance import decompress_checked
from .china_trade_remedies import gb_remedies, xi_remedies, summary as remedy_summary

PERCENT=re.compile(r"^\s*(?:\d+(?:\.\d+)?|\.\d+)\s*%\s*$")
CODE=re.compile(r"[0-9]{10}\Z")
SQL="""CREATE TABLE tariffs (
 code TEXT PRIMARY KEY,description TEXT,rate TEXT,url TEXT,
 north_ireland_rate TEXT,north_ireland_url TEXT,other_rate TEXT,
 anti_dumping_rate TEXT,countervailing_rate TEXT,
 ni_anti_dumping_rate TEXT,ni_countervailing_rate TEXT,
 trade_remedy_details TEXT,last_updated DATETIME
);
CREATE INDEX idx_tariffs_description ON tariffs(description);
"""


def simple_rate(rows):
    """Multiple applicable or conditional measures are never guessed."""
    if not rows:
        return ""
    values=[]
    for expression, additional, conditions, excluded in rows:
        expression=(expression or "").strip()
        if (not PERCENT.fullmatch(expression) or
                (additional or "").strip() or
                (conditions or "").strip() not in ("","[]","null") or
                (excluded or "").strip() not in ("","[]","null")):
            return ""
        values.append(expression)
    return values[0] if len(values)==1 else ""



def _blank(value):
    """DBT CSV export represents missing attributes with #NA/N/A/None."""
    return value is None or str(value).strip() in NA or str(value).strip() in ("[]","{}")


def gb_default_rate(rows):
    """Select an unconditional base duty, ignoring inapplicable extra-code rows.

    A different unconditional rate, or an unclear base expression, makes
    the code ambiguous. Never publish a conditional or specific-country rate.
    """
    default=[]
    for expr,additional,conditions,excluded in rows:
        if not (_blank(additional) and _blank(conditions) and _blank(excluded)):
            continue
        expression=(expr or "").strip()
        if not PERCENT.fullmatch(expression):
            return ""
        default.append(expression)
    distinct=set(default)
    return next(iter(distinct)) if len(distinct)==1 else ""


def xi_rate(rows, as_of:date, original_body:bytes|None=None):
    """Use ordinary 103/1011 third-country duty, not 105 authorised-use rates.

    105 can be 0% only for special authorised use; it is not a default rate
    for Chinese-origin/general third-country imports. Never invent a rate.
    """
    related={}
    if original_body is not None:
        document=json.loads(original_body)
        related={(str(x.get("type")),str(x.get("id"))):x
                 for x in document.get("included",[]) if isinstance(x,dict)}
    selected=[]
    for kind, geo, expr, start, end, raw in rows:
        if kind != "103" or geo!="1011":
            continue
        try:
            begin=date.fromisoformat(start[:10])
            finish=date.fromisoformat(end[:10]) if end else None
        except (TypeError,ValueError):
            return ""
        if begin>as_of or (finish and finish<as_of):
            continue
        node=json.loads(raw)
        rel=node.get("relationships") or {}
        has_special_scope = False
        for name in ("measure_conditions","measure_condition_permutation_groups",
                     "excluded_countries","additional_code","additional_codes"):
            val=(rel.get(name) or {}).get("data")
            if isinstance(val,dict) or (isinstance(val,list) and val):
                has_special_scope = True
                break
        if has_special_scope:
            # A special 103 variant does not invalidate an independent ordinary
            # 103 base measure. Never use the special variant as default.
            continue
        # HMRC v2 frequently leaves resolved_duty_expression blank and supplies
        # its actual expression through included duty_expression.base/verbose_duty.
        link=(rel.get("duty_expression") or {}).get("data")
        if isinstance(link,list) and len(link)!=1:
            return ""
        if isinstance(link,list):
            link=link[0]
        if isinstance(link,dict):
            duty=related.get((str(link.get("type")),str(link.get("id"))))
            if duty is not None:
                attrs=duty.get("attributes") or {}
                linked=attrs.get("base") or attrs.get("verbose_duty") or ""
                if not isinstance(linked,str):
                    return ""
                # Prefer the actual relationship over a potentially shortened inline summary.
                if linked.strip():
                    expr=linked
        selected.append((expr,"","",""))
    return simple_rate(selected)


def hash_file(path:Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def build(gb_db:str, gb_catalogue:str, xi_db:str, xi_catalogue:str,
          xi_manifest:str, output_db:str, output_metadata:str):
    input_paths=[Path(s).resolve() for s in (gb_db,gb_catalogue,xi_db,xi_catalogue,xi_manifest)]
    out=Path(output_db).resolve()
    meta=Path(output_metadata).resolve()
    if out==meta or out in input_paths or meta in input_paths:
        raise ValueError("Never overwrite input source files")
    gb=_source(gb_db,region="GB")
    xi=_source(xi_db,region="XI")
    verified=verify_public(xi_catalogue,xi_manifest)
    if (gb["source_rows"]<1_000_000 or
            verified["as_of"]!=xi["as_of"].isoformat() or
            verified["catalogue_sha256"]!=xi["version_or_catalogue"]):
        raise ValueError("Complete GB/XI snapshot provenance is missing")
    gb_codes=_catalogue(gb_catalogue,gb["version_or_catalogue"],gb["as_of"])
    xi_codes=set(candidates(xi_catalogue))
    if len(xi_codes)!=len(xi["codes"]) or not gb_codes:
        raise ValueError("XI/GB current catalogue not fully covered")
    day=max(gb["as_of"],xi["as_of"])
    if abs((gb["as_of"]-xi["as_of"]).days)>7:
        raise ValueError("GB/XI snapshots too far apart for combined desktop release")
    gb_descr={}
    with closing(sqlite3.connect(Path(gb_catalogue).resolve().as_uri()+"?mode=ro",uri=True)) as db:
        for code,description in db.execute("SELECT code,description FROM commodities WHERE active=1 AND suffix80=1"):
            if code in gb_codes and code not in gb_descr and description:
                gb_descr[code]=description
    xi_descr={}
    with closing(sqlite3.connect(Path(xi_catalogue).resolve().as_uri()+"?mode=ro",uri=True)) as db:
        for code,description in db.execute("SELECT code,description FROM nomenclature WHERE active=1 AND declarable=1"):
            if code in xi_codes and description:
                xi_descr[code]=description
    out.parent.mkdir(parents=True,exist_ok=True)
    meta.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temp:
        stage=Path(temp)/"tariffs.db"
        with closing(sqlite3.connect(stage)) as dest, closing(
                sqlite3.connect(Path(gb_db).resolve().as_uri()+"?mode=ro",uri=True)
            ) as gb_conn, closing(
                sqlite3.connect(Path(xi_db).resolve().as_uri()+"?mode=ro",uri=True)
            ) as xi_conn:
            dest.executescript(SQL)
            counts=Counter()
            all_codes=sorted(gb_codes|xi_codes)
            for code in all_codes:
                if not CODE.fullmatch(code):
                    raise ValueError("Invalid official declarable code")
                gb_rate=""
                ni_rate=""
                gb_trade=[]
                ni_trade=[]
                if code in gb_codes:
                    source=list(gb_conn.execute("""SELECT expression,additional_code,conditions,excluded_areas
                       FROM measures WHERE region='GB' AND code=?
                        AND effective_status='active' AND measure_type = '103'
                        AND geography='1011' ORDER BY ordinal""",(code,)))
                    gb_rate=gb_default_rate(source)
                    counts["gb_mfn_103_codes"]+=bool(source)
                    counts["gb_unconditional_candidates"]+=sum(
                        _blank(r[1]) and _blank(r[2]) and _blank(r[3]) for r in source)
                    counts["gb_percent"]+=bool(gb_rate)
                    remedies=list(gb_conn.execute("""SELECT measure_type,geography,expression,
                         additional_code,conditions,excluded_areas,effective_start,effective_end,raw_json
                         FROM measures WHERE region='GB' AND code=?
                         AND measure_type IN ('551','552','553','554')
                         AND effective_status='active' ORDER BY ordinal""",(code,)))
                    gb_trade=gb_remedies(remedies,gb["as_of"])
                if code in xi_codes:
                    source=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type = '103'
                        ORDER BY ordinal""",(code,)))
                    original=xi_conn.execute(
                        "SELECT response_sha256,compressed_body FROM requests WHERE code=?",
                        (code,)).fetchone()
                    if not original or not original[0]:
                        raise ValueError("Accepted XI full source missing response for "+code)
                    raw=decompress_checked(original[1])
                    if hashlib.sha256(raw).hexdigest()!=original[0]:
                        raise ValueError("XI raw response fingerprint mismatch for "+code)
                    ni_rate=xi_rate(source,xi["as_of"],raw)
                    counts["xi_percent"]+=bool(ni_rate)
                    trade_rows=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type IN ('551','552','553','554')
                        ORDER BY ordinal""",(code,)))
                    ni_trade=xi_remedies(trade_rows,xi["as_of"],raw)
                gb_antidumping=remedy_summary(gb_trade,"anti_dumping")
                gb_countervailing=remedy_summary(gb_trade,"countervailing")
                ni_antidumping=remedy_summary(ni_trade,"anti_dumping")
                ni_countervailing=remedy_summary(ni_trade,"countervailing")
                counts["gb_anti_dumping_codes"]+=bool(gb_antidumping)
                counts["gb_countervailing_codes"]+=bool(gb_countervailing)
                counts["xi_anti_dumping_codes"]+=bool(ni_antidumping)
                counts["xi_countervailing_codes"]+=bool(ni_countervailing)
                details=json.dumps(gb_trade+ni_trade,ensure_ascii=False,separators=(",",":"))
                dest.execute("INSERT INTO tariffs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                    code,gb_descr.get(code) or xi_descr.get(code) or "",
                    gb_rate,
                    "https://www.trade-tariff.service.gov.uk/uk/commodities/"+code if code in gb_codes else "",
                    ni_rate,
                    "https://www.trade-tariff.service.gov.uk/xi/commodities/"+code if code in xi_codes else "",
                    "", gb_antidumping, gb_countervailing,
                    ni_antidumping, ni_countervailing, details, None))
            dest.commit()
            if dest.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("Desktop SQLite integrity failed")
            if dest.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]!=len(all_codes):
                raise ValueError("Desktop code union has holes")
        stage.replace(out)
    digest=hash_file(out)
    version="data-"+day.strftime("%Y%m%d")+"-"+digest[:16]
    stamp=datetime.now(timezone.utc).isoformat()
    report={
        "version":version,
        "timestamp":stamp,
        "last_modified":stamp,
        "file_hash":"sha256:"+digest,
        "file_size":out.stat().st_size,
        "record_count":len(gb_codes|xi_codes),
        "changes_summary":{"total_updates":counts["gb_percent"]+counts["xi_percent"],
                           "gb_available_simple_percent":counts["gb_percent"],
                           "xi_available_simple_percent":counts["xi_percent"],
                           "gb_anti_dumping_codes":counts["gb_anti_dumping_codes"],
                           "gb_countervailing_codes":counts["gb_countervailing_codes"],
                           "xi_anti_dumping_codes":counts["xi_anti_dumping_codes"],
                           "xi_countervailing_codes":counts["xi_countervailing_codes"]},
        "source_versions":{"GB":gb["version_or_catalogue"],
                           "GB_as_of":gb["as_of"].isoformat(),
                           "XI_as_of":xi["as_of"].isoformat(),
                           "XI_catalogue_sha256":xi["version_or_catalogue"]},
        "coverage":{"GB":len(gb_codes),"XI":len(xi_codes)},
        "rate_scope":"China-origin ordinary 103/1011 MFN; GB/XI China or non-excluded Erga Omnes 551/552 anti-dumping and 553/554 countervailing; producer/code/conditions retained in trade_remedy_details, no assumed universal exporter rate",
        "download_urls":{
            "primary":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/tariffs.db",
            "metadata":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/metadata.json"},
    }
    if len(gb_codes)<10_000 or len(xi_codes)<10_000:
        raise ValueError("Suspiciously incomplete official commodity catalogue")
    # Do not overwrite a mostly populated desktop DB with mostly blank rates
    # if the official expression layout changes unexpectedly.
    print("Verified simple percentage coverage:",dict(counts))
    if counts["gb_percent"]<10_000 or counts["xi_percent"]<10_000:
        raise ValueError("Insufficient unambiguous GB/XI percentage coverage for desktop release")
    meta.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser()
    for arg in ("gb-db","gb-catalogue","xi-db","xi-catalogue","xi-manifest",
                "output-db","output-metadata"):
        parser.add_argument("--"+arg,required=True)
    a=vars(parser.parse_args())
    print(json.dumps(build(a["gb_db"],a["gb_catalogue"],a["xi_db"],
                     a["xi_catalogue"],a["xi_manifest"],
                     a["output_db"],a["output_metadata"]),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
