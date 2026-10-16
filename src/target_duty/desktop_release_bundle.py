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
 trade_remedy_details TEXT,rate_options TEXT,last_updated DATETIME
);
CREATE INDEX idx_tariffs_description ON tariffs(description);
"""


def _clean(value):
    value="" if value is None else str(value).strip()
    return "" if value in ("", "#NA", "N/A", "NULL", "null", "None", "[]", "{}") else value


def _expression(value):
    """Accept percentage, specific, compound and monetary duty expressions."""
    return _clean(value)


def simple_rate(rows):
    """Return one exact unconditional expression, including specific/compound duty."""
    if not rows:
        return ""
    values=[]
    for expression, additional, conditions, excluded in rows:
        expression=_expression(expression)
        if (not expression or _clean(additional) or _clean(conditions) or _clean(excluded)):
            return ""
        values.append(expression)
    distinct=set(values)
    return next(iter(distinct)) if len(distinct)==1 else ""


def _gb_rate_options(rows, as_of):
    options=[]
    for kind, geo, expression, additional, additional_desc, conditions, excluded, start, end, raw in rows:
        raw_obj=json.loads(raw) if isinstance(raw,str) else (raw or {})
        option={
            "region":"GB",
            "measure_type":str(kind),
            "measure_type_description":_clean(raw_obj.get("measure__type__description")),
            "geography":_clean(geo),
            "geography_description":_clean(raw_obj.get("measure__geographical_area__description")),
            "expression":_expression(expression),
            "additional_code":_clean(additional),
            "additional_description":_clean(additional_desc or raw_obj.get("measure__additional_code__description")),
            "conditions":_clean(conditions),
            "excluded_areas":_clean(excluded),
            "effective_start":_clean(start),
            "effective_end":_clean(end),
            "source_id":_clean(raw_obj.get("measure__sid")),
        }
        if not option["expression"]:
            continue
        option["special_use"] = option["measure_type"] == "105"
        option["conditional"] = bool(option["additional_code"] or option["conditions"] or option["excluded_areas"] or option["special_use"])
        option["china_applicable"] = (
            option["geography"] == "CN" or
            (option["geography"] == "1011" and "CN" not in option["excluded_areas"].upper())
        )
        option["applicability"] = (
            "default" if option["measure_type"] == "103" and option["geography"] == "1011"
            and not option["conditional"] else
            "authorised_use" if option["special_use"] else
            "conditional"
        )
        options.append(option)
    return options


def _xi_relation_map(document):
    return {(str(x.get("type")), str(x.get("id"))):x
            for x in document.get("included",[]) if isinstance(x,dict)}


def _xi_refs(node, name):
    value=((node.get("relationships") or {}).get(name) or {}).get("data")
    if isinstance(value,dict):
        return [value]
    if isinstance(value,list):
        return [x for x in value if isinstance(x,dict)]
    return []


def _xi_linked(index, refs):
    return [index[(str(r.get("type")),str(r.get("id")))]
            for r in refs if (str(r.get("type")),str(r.get("id"))) in index]


def _xi_text(nodes):
    values=[]
    for node in nodes:
        attrs=node.get("attributes") or {}
        value=(attrs.get("description") or attrs.get("formatted_description")
               or attrs.get("name") or attrs.get("base") or attrs.get("verbose_duty"))
        if value:
            values.append(str(value).strip())
    return "; ".join(dict.fromkeys(values))


def _xi_rate_options(rows, original_body, as_of):
    document=json.loads(original_body)
    index=_xi_relation_map(document)
    options=[]
    for kind, geo, expression, start, end, raw in rows:
        if str(kind) not in ("103","105"):
            continue
        node=json.loads(raw) if isinstance(raw,str) else raw
        linked_expr=_xi_linked(index,_xi_refs(node,"duty_expression"))
        expr=_expression(expression)
        if linked_expr:
            attrs=linked_expr[0].get("attributes") or {}
            expr=_expression(attrs.get("base") or attrs.get("verbose_duty") or expr)
        additional=_xi_linked(index,_xi_refs(node,"additional_code"))
        if not additional:
            additional=_xi_linked(index,_xi_refs(node,"additional_codes"))
        conditions=(_xi_linked(index,_xi_refs(node,"measure_conditions"))
                    +_xi_linked(index,_xi_refs(node,"measure_condition_permutation_groups")))
        excluded=_xi_linked(index,_xi_refs(node,"excluded_countries"))
        geos=_xi_linked(index,_xi_refs(node,"geographical_area"))
        type_nodes=_xi_linked(index,_xi_refs(node,"measure_type"))
        additional_codes=[]
        for item in additional:
            attrs=item.get("attributes") or {}
            code=_clean(attrs.get("code") or attrs.get("additional_code"))
            if code:
                additional_codes.append(code)
        excluded_codes=[]
        for item in excluded:
            attrs=item.get("attributes") or {}
            excluded_codes.append(_clean(attrs.get("code") or attrs.get("id")))
        geo_id=_clean(geo or ((geos[0].get("attributes") or {}).get("id") if geos else ""))
        option={
            "region":"XI",
            "measure_type":str(kind),
            "measure_type_description":_xi_text(type_nodes),
            "geography":geo_id,
            "geography_description":_xi_text(geos),
            "expression":expr,
            "additional_code":",".join(dict.fromkeys(additional_codes)),
            "additional_description":_xi_text(additional),
            "conditions":_xi_text(conditions) or ("见官方措施条件" if conditions else ""),
            "excluded_areas":",".join(dict.fromkeys(x for x in excluded_codes if x)),
            "effective_start":_clean(start),
            "effective_end":_clean(end),
            "source_id":_clean(node.get("id")),
        }
        if not option["expression"]:
            continue
        option["special_use"]=option["measure_type"]=="105"
        option["conditional"]=bool(option["additional_code"] or option["conditions"]
                                    or option["excluded_areas"] or option["special_use"])
        option["china_applicable"]=(
            option["geography"]=="CN" or
            (option["geography"]=="1011" and "CN" not in option["excluded_areas"].upper())
        )
        option["applicability"]=(
            "default" if option["measure_type"]=="103" and option["geography"]=="1011"
            and not option["conditional"] else
            "authorised_use" if option["special_use"] else
            "conditional"
        )
        options.append(option)
    return options



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
                gb_options=[]
                xi_options=[]
                if code in gb_codes:
                    source=list(gb_conn.execute("""SELECT expression,additional_code,conditions,excluded_areas
                       FROM measures WHERE region='GB' AND code=?
                        AND effective_status='active' AND measure_type = '103'
                        AND geography='1011' ORDER BY ordinal""",(code,)))
                    gb_rate=gb_default_rate(source)
                    option_rows=list(gb_conn.execute("""SELECT measure_type,geography,expression,
                         additional_code,additional_code,conditions,excluded_areas,
                         effective_start,effective_end,raw_json
                         FROM measures WHERE region='GB' AND code=?
                         AND effective_status='active' AND measure_type IN ('103','105')
                         ORDER BY ordinal""",(code,)))
                    gb_options=_gb_rate_options(option_rows,gb["as_of"])
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
                    option_rows=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type IN ('103','105')
                        ORDER BY ordinal""",(code,)))
                    xi_options=_xi_rate_options(option_rows,raw,xi["as_of"])
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
                rate_options=json.dumps(gb_options+xi_options,ensure_ascii=False,separators=(",",":"))
                counts["gb_rate_option_codes"]+=bool(gb_options)
                counts["xi_rate_option_codes"]+=bool(xi_options)
                counts["conditional_rate_options"]+=sum(
                    1 for option in gb_options+xi_options if option["conditional"])
                counts["china_applicable_rate_options"]+=sum(
                    1 for option in gb_options+xi_options if option["china_applicable"])
                dest.execute("INSERT INTO tariffs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                    code,gb_descr.get(code) or xi_descr.get(code) or "",
                    gb_rate,
                    "https://www.trade-tariff.service.gov.uk/uk/commodities/"+code if code in gb_codes else "",
                    ni_rate,
                    "https://www.trade-tariff.service.gov.uk/xi/commodities/"+code if code in xi_codes else "",
                    "", gb_antidumping, gb_countervailing,
                    ni_antidumping, ni_countervailing, details, rate_options, None))
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
                           "xi_countervailing_codes":counts["xi_countervailing_codes"],
                           "gb_rate_option_codes":counts["gb_rate_option_codes"],
                           "xi_rate_option_codes":counts["xi_rate_option_codes"],
                           "conditional_rate_options":counts["conditional_rate_options"],
                           "china_applicable_rate_options":counts["china_applicable_rate_options"]},
        "source_versions":{"GB":gb["version_or_catalogue"],
                           "GB_as_of":gb["as_of"].isoformat(),
                           "XI_as_of":xi["as_of"].isoformat(),
                           "XI_catalogue_sha256":xi["version_or_catalogue"]},
        "coverage":{"GB":len(gb_codes),"XI":len(xi_codes)},
        "rate_scope":"China-origin ordinary 103/1011 plus all active 103/105 conditional/specific-use options; GB/XI China or non-excluded Erga Omnes 551/552 anti-dumping and 553/554 countervailing; exact source conditions, additional codes, exclusions and effective dates retained in rate_options/trade_remedy_details",
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
