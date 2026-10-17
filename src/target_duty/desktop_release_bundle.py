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
from .geo_area_memberships import inspect_csv as inspect_geo_memberships
from .xi_geographical_areas import inspect_payload as inspect_xi_geographical_areas

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


def _effective_status(start, end, as_of):
    """Classify source dates; missing/invalid dates are never assumed current."""
    start = _clean(start)
    end = _clean(end)
    try:
        if not start:
            return "unknown"
        first = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10]) if end else None
        if last and last < first:
            return "unknown"
    except (TypeError, ValueError):
        return "unknown"
    if first > as_of:
        return "future"
    if last and last < as_of:
        return "expired"
    return "active"


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


def _area_tokens(value):
    return {token.strip().upper() for token in re.split(r"[|,;\s]+", _clean(value))
            if token.strip()}


def _china_applicable(geography, excluded, china_groups=None):
    """Resolve China against explicit areas and same-version active group membership."""
    geos = _area_tokens(geography)
    exclusions = _area_tokens(excluded)
    groups = set(china_groups or ())
    if exclusions & ({"CN", "CHN", "CHINA"} | groups):
        return False
    return bool(geos & {"CN", "1011"} or geos & groups)


def gb_china_rate(rows, china_groups=None):
    """Select China's unconditioned 103 rate with geographic specificity.

    Rows are (expression, additional code, conditions, exclusions, geography).
    Priority is explicit CN, then an active country group containing CN, then
    Erga Omnes 1011. Conflicts at the same specificity fail closed.
    """
    eligible = []
    groups = set(china_groups or ())
    for expression, additional, conditions, excluded, geography in rows:
        if not _china_applicable(geography, excluded, groups):
            continue
        if not _blank(additional) or not _blank(conditions):
            continue
        eligible.append((_clean(geography), _expression(expression)))
    if not eligible:
        return ""
    china_specific = [expr for geo, expr in eligible if geo == "CN"]
    group_specific = [expr for geo, expr in eligible if geo in groups and geo != "1011"]
    selected = china_specific or group_specific or [expr for geo, expr in eligible if geo == "1011"]
    if not selected or any(not expr for expr in selected):
        return ""
    distinct = set(selected)
    return next(iter(distinct)) if len(distinct) == 1 else ""


def _gb_rate_options(rows, as_of, china_groups=None):
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
            "effective_status":_effective_status(start, end, as_of),
            "source_id":_clean(raw_obj.get("measure__sid")),
        }
        option["expression_available"] = bool(option["expression"])
        option["special_use"] = option["measure_type"] == "105"
        option["china_applicable"] = _china_applicable(option["geography"], option["excluded_areas"], china_groups)
        option["conditional"] = bool(
            option["additional_code"] or option["conditions"] or option["special_use"]
        )
        option["applicability"] = (
            "default" if option["measure_type"] == "103"
            and option["china_applicable"] and not option["conditional"] else
            "authorised_use" if option["special_use"] else
            "other_measure" if option["measure_type"] not in ("103", "105") else
            "conditional"
        )
        option["quota_order_number"] = _clean(raw_obj.get("measure__quota__order_number"))
        option["footnotes"] = _clean(raw_obj.get("measure__footnotes"))
        option["regulation_id"] = _clean(raw_obj.get("measure__regulation__id"))
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


def _xi_rate_options(rows, original_body, as_of, china_groups=None, area_descriptions=None):
    document=json.loads(original_body)
    index=_xi_relation_map(document)
    options=[]
    for kind, geo, expression, start, end, raw in rows:
        # The caller supplies all non-remedy measure types for China/Erga
        # Omnes, including expired/future rows. Keep each source row visible.
        node=json.loads(raw) if isinstance(raw,str) else raw
        rel=node.get("relationships") or {}
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
        excluded_refs=(_xi_refs(node,"excluded_countries")
                       +_xi_refs(node,"excluded_geographical_areas"))
        excluded=_xi_linked(index,excluded_refs)
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
            code=attrs.get("code") or attrs.get("country_code") or attrs.get("geographical_area_id") or attrs.get("id") or item.get("id")
            excluded_codes.append(_clean(code))
        linked_excluded_ids={str(x.get("id")) for x in excluded}
        excluded_codes.extend(_clean(x.get("id")) for x in excluded_refs
                              if str(x.get("id")) not in linked_excluded_ids)
        geo_id=_clean(geo or ((geos[0].get("attributes") or {}).get("code")
                              or (geos[0].get("attributes") or {}).get("id") if geos else ""))
        option={
            "region":"XI",
            "measure_type":str(kind),
            "measure_type_description":_xi_text(type_nodes),
            "geography":geo_id,
            "geography_description":_xi_text(geos) or _clean((area_descriptions or {}).get(_clean(geo), "")),
            "expression":expr,
            "additional_code":",".join(dict.fromkeys(additional_codes)),
            "additional_description":_xi_text(additional),
            "conditions":_xi_text(conditions) or ("见官方措施条件" if conditions else ""),
            "excluded_areas":",".join(dict.fromkeys(x for x in excluded_codes if x)),
            "effective_start":_clean(start),
            "effective_end":_clean(end),
            "effective_status":_effective_status(start, end, as_of),
            "condition_ids":[_clean(x.get("id")) for x in conditions if _clean(x.get("id"))],
            "additional_code_ids":[_clean(x.get("id")) for x in additional if _clean(x.get("id"))],
            "source_id":_clean(node.get("id")),
            "source_relationship_ids":{
                name:[_clean(x.get("id")) for x in _xi_refs(node,name) if _clean(x.get("id"))]
                for name in sorted(rel)
            },
        }
        option["expression_available"] = bool(option["expression"])
        option["special_use"]=option["measure_type"]=="105"
        option["china_applicable"]=_china_applicable(option["geography"], option["excluded_areas"], china_groups)
        option["conditional"]=bool(option["additional_code"] or option["conditions"]
                                    or option["special_use"])
        option["applicability"]=(
            "default" if option["measure_type"]=="103" and option["china_applicable"]
            and not option["conditional"] else
            "authorised_use" if option["special_use"] else
            "other_measure" if option["measure_type"] not in ("103", "105") else
            "conditional"
        )
        options.append(option)
    return options



def _blank(value):
    """DBT CSV export represents missing attributes with #NA/N/A/None."""
    return value is None or str(value).strip() in NA or str(value).strip() in ("[]","{}")


def gb_default_rate(rows):
    """Select one exact unconditional duty expression, not percentages only.

    Specific and compound duties (for example GBP/kg or percentage + specific
    duty) are valid official expressions. Conditions, additional codes,
    exclusions, conflicting expressions or blank expressions never become a
    default. Missing default stays blank rather than being guessed as zero.
    """
    default=[]
    for expr,additional,conditions,excluded in rows:
        if not (_blank(additional) and _blank(conditions) and _blank(excluded)):
            continue
        expression=_expression(expr)
        if not expression:
            return ""
        default.append(expression)
    distinct=set(default)
    return next(iter(distinct)) if len(distinct)==1 else ""


def xi_rate(rows, as_of:date, original_body:bytes|None=None, china_groups=None):
    """Resolve China's unconditioned 103 default, preferring a CN-specific rate.

    Conditional/additional-code variants remain in rate_options. An exclusion
    of another country does not block a Chinese import; explicit China
    exclusions and unresolved exclusion references fail closed.
    """
    document=json.loads(original_body) if original_body is not None else {}
    related=_xi_relation_map(document)
    groups=set(china_groups or ())
    selected=[]
    for kind, geo, expr, start, end, raw in rows:
        if kind != "103" or not _china_applicable(geo, "", groups):
            continue
        try:
            begin=date.fromisoformat(start[:10])
            finish=date.fromisoformat(end[:10]) if end else None
        except (TypeError,ValueError):
            return ""
        if begin>as_of or (finish and finish<as_of):
            continue
        node=json.loads(raw) if isinstance(raw,str) else raw
        rel=node.get("relationships") or {}
        conditional=False
        for name in ("measure_conditions","measure_condition_permutation_groups",
                     "additional_code","additional_codes"):
            value=(rel.get(name) or {}).get("data")
            if isinstance(value,dict) or (isinstance(value,list) and value):
                conditional=True
                break
        if conditional:
            continue
        exclusion_refs=[]
        for name in ("excluded_countries","excluded_geographical_areas"):
            exclusion_refs.extend(_xi_refs(node,name))
        excluded_codes=set()
        unresolved_exclusion=False
        for ref in exclusion_refs:
            linked=related.get((str(ref.get("type")),str(ref.get("id"))))
            if linked is None:
                tokens=_area_tokens(ref.get("id"))
                if tokens & {"CN","CHN","CHINA"}:
                    excluded_codes.add("CN")
                else:
                    unresolved_exclusion=True
                continue
            attrs=linked.get("attributes") or {}
            tokens=set()
            for key in ("code","country_code","geographical_area_id","area_id","id"):
                tokens.update(_area_tokens(attrs.get(key)))
            description=str(attrs.get("description") or attrs.get("name") or "").strip().lower()
            if description in ("china","people's republic of china","people’s republic of china"):
                tokens.add("CN")
            if not tokens:
                unresolved_exclusion=True
            excluded_codes.update(tokens)
        if excluded_codes & ({"CN","CHN","CHINA"} | groups) or unresolved_exclusion:
            continue
        link=(rel.get("duty_expression") or {}).get("data")
        if isinstance(link,list) and len(link)!=1:
            return ""
        if isinstance(link,list):
            link=link[0]
        if isinstance(link,dict):
            duty=related.get((str(link.get("type")),str(link.get("id"))))
            if duty is None:
                expr=""
            else:
                attrs=duty.get("attributes") or {}
                linked=attrs.get("base") or attrs.get("verbose_duty") or ""
                if not isinstance(linked,str):
                    return ""
                expr=linked if linked.strip() else ""
        selected.append((geo,_expression(expr)))
    if not selected:
        return ""
    china_specific=[expr for geo,expr in selected if geo=="CN"]
    group_specific=[expr for geo,expr in selected if geo in groups and geo!="1011"]
    rates=china_specific or group_specific or [expr for geo,expr in selected if geo=="1011"]
    if not rates or any(not rate for rate in rates):
        return ""
    distinct=set(rates)
    return next(iter(distinct)) if len(distinct)==1 else ""


def _gb_rate_option_rows(conn, code, china_groups=None):
    """Project every China-applicable non-remedy row, including all date states."""
    rows=list(conn.execute("""SELECT measure_type,geography,expression,
        additional_code,NULL,conditions,excluded_areas,effective_start,effective_end,raw_json
        FROM measures WHERE region='GB' AND code=?
        AND measure_type NOT IN ('551','552','553','554')
        ORDER BY ordinal""", (code,)))
    groups=set(china_groups or ())
    return [row for row in rows if _china_applicable(row[1], row[6], groups)]


def _xi_rate_option_rows(conn, code, china_groups=None):
    """Project every China-applicable non-remedy row, including all date states."""
    rows=list(conn.execute("""SELECT measure_type,geography_id,duty_expression,
        effective_start,effective_end,raw_json
        FROM measures WHERE code=? AND measure_type NOT IN ('551','552','553','554')
        ORDER BY ordinal""", (code,)))
    groups=set(china_groups or ())
    return [row for row in rows if _china_applicable(row[1], "", groups)]


def hash_file(path:Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def build(gb_db:str, gb_catalogue:str, xi_db:str, xi_catalogue:str,
          xi_manifest:str, output_db:str, output_metadata:str,
          geo_memberships_path:str, geo_memberships_report_path:str,
          xi_geographical_areas_path:str, xi_geographical_areas_report_path:str):
    input_paths=[Path(s).resolve() for s in (gb_db,gb_catalogue,xi_db,xi_catalogue,xi_manifest)]
    out=Path(output_db).resolve()
    meta=Path(output_metadata).resolve()
    if out==meta or out in input_paths or meta in input_paths:
        raise ValueError("Never overwrite input source files")
    gb=_source(gb_db,region="GB")
    xi=_source(xi_db,region="XI")
    geo_path=Path(geo_memberships_path).resolve()
    geo_report_path=Path(geo_memberships_report_path).resolve()
    xi_geo_path=Path(xi_geographical_areas_path).resolve()
    xi_geo_report_path=Path(xi_geographical_areas_report_path).resolve()
    evidence_paths=(geo_path,geo_report_path,xi_geo_path,xi_geo_report_path)
    if (any(p in input_paths or p in (out,meta) for p in evidence_paths)
            or len(set(evidence_paths))!=len(evidence_paths)):
        raise ValueError("Geographical membership evidence must be separate source files")
    geo_payload=geo_path.read_bytes()
    geo=inspect_geo_memberships(geo_payload,gb["as_of"])
    geo_meta=json.loads(geo_report_path.read_text(encoding="utf-8"))
    if (geo_meta.get("kind")!="dbt-official-geo-area-memberships-v1"
            or geo_meta.get("version")!=gb["version_or_catalogue"]
            or geo_meta.get("as_of")!=gb["as_of"].isoformat()
            or geo_meta.get("source_sha256")!=geo["source_sha256"]
            or geo_meta.get("active_china_group_ids")!=geo["active_china_group_ids"]):
        raise ValueError("Pinned official China geographical membership evidence mismatch")
    china_groups=set(geo["active_china_group_ids"])
    xi_geo_payload=xi_geo_path.read_bytes()
    xi_geo=inspect_xi_geographical_areas(xi_geo_payload,xi["as_of"])
    xi_geo_meta=json.loads(xi_geo_report_path.read_text(encoding="utf-8"))
    if (xi_geo_meta.get("kind")!="hmrc-official-xi-geographical-area-tree-v1"
            or xi_geo_meta.get("as_of")!=xi["as_of"].isoformat()
            or xi_geo_meta.get("source_sha256")!=xi_geo["source_sha256"]
            or xi_geo_meta.get("china_applicable_group_ids")!=xi_geo["china_applicable_group_ids"]
            or xi_geo_meta.get("complete") is not True):
        raise ValueError("Pinned official XI geographical-area membership evidence mismatch")
    xi_groups=set(xi_geo["china_applicable_group_ids"])
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
            option_status_counts=Counter()
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
                gb_option_rows=[]
                xi_option_rows=[]
                if code in gb_codes:
                    source=list(gb_conn.execute("""SELECT expression,additional_code,conditions,excluded_areas,geography
                       FROM measures WHERE region='GB' AND code=?
                        AND effective_status='active' AND measure_type='103' ORDER BY ordinal""",(code,)))
                    gb_rate=gb_china_rate(source,china_groups)
                    gb_option_rows=_gb_rate_option_rows(gb_conn,code,china_groups)
                    gb_options=_gb_rate_options(gb_option_rows,gb["as_of"],china_groups)
                    if len(gb_options)!=len(gb_option_rows):
                        raise ValueError("GB rate-option projection lost official measure rows for "+code)
                    counts["gb_mfn_103_codes"]+=bool(source)
                    counts["gb_unconditional_candidates"]+=sum(
                        _blank(r[1]) and _blank(r[2]) and _blank(r[3]) for r in source)
                    counts["gb_percent"]+=bool(gb_rate)
                    remedies=list(gb_conn.execute("""SELECT measure_type,geography,expression,
                         additional_code,conditions,excluded_areas,effective_start,effective_end,raw_json
                         FROM measures WHERE region='GB' AND code=?
                         AND measure_type IN ('551','552','553','554') ORDER BY ordinal""",(code,)))
                    gb_trade=gb_remedies(remedies,gb["as_of"],china_groups)
                if code in xi_codes:
                    source=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type='103' ORDER BY ordinal""",(code,)))
                    original=xi_conn.execute(
                        "SELECT response_sha256,compressed_body FROM requests WHERE code=?",
                        (code,)).fetchone()
                    if not original or not original[0]:
                        raise ValueError("Accepted XI full source missing response for "+code)
                    raw=decompress_checked(original[1])
                    if hashlib.sha256(raw).hexdigest()!=original[0]:
                        raise ValueError("XI raw response fingerprint mismatch for "+code)
                    ni_rate=xi_rate(source,xi["as_of"],raw,xi_groups)
                    xi_option_rows=_xi_rate_option_rows(xi_conn,code,xi_groups)
                    xi_options=_xi_rate_options(xi_option_rows,raw,xi["as_of"],xi_groups,xi_geo["area_descriptions"])
                    if len(xi_options)!=len(xi_option_rows):
                        raise ValueError("XI rate-option projection lost official measure rows for "+code)
                    counts["xi_percent"]+=bool(ni_rate)
                    trade_rows=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type IN ('551','552','553','554')
                        ORDER BY ordinal""",(code,)))
                    ni_trade=xi_remedies(trade_rows,xi["as_of"],raw,xi_groups,xi_geo["area_descriptions"])
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
                counts["gb_rate_source_rows"]+=len(gb_option_rows)
                counts["xi_rate_source_rows"]+=len(xi_option_rows)
                counts["gb_rate_projected_rows"]+=len(gb_options)
                counts["xi_rate_projected_rows"]+=len(xi_options)
                for option in gb_options:
                    option_status_counts["GB:"+option["effective_status"]]+=1
                for option in xi_options:
                    option_status_counts["XI:"+option["effective_status"]]+=1
                counts["missing_rate_expressions"]+=sum(
                    1 for option in gb_options+xi_options if not option["expression_available"])
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
        if counts["gb_rate_source_rows"] != counts["gb_rate_projected_rows"]:
            raise ValueError("GB rate-option source/projection row counts differ")
        if counts["xi_rate_source_rows"] != counts["xi_rate_projected_rows"]:
            raise ValueError("XI rate-option source/projection row counts differ")
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
                           "gb_rate_option_source_rows":counts["gb_rate_source_rows"],
                           "gb_rate_option_projected_rows":counts["gb_rate_projected_rows"],
                           "xi_rate_option_source_rows":counts["xi_rate_source_rows"],
                           "xi_rate_option_projected_rows":counts["xi_rate_projected_rows"],
                           "gb_rate_option_projection_complete":counts["gb_rate_source_rows"]==counts["gb_rate_projected_rows"],
                           "xi_rate_option_projection_complete":counts["xi_rate_source_rows"]==counts["xi_rate_projected_rows"],
                           "missing_rate_expressions":counts["missing_rate_expressions"],
                           "conditional_rate_options":counts["conditional_rate_options"],
                           "china_applicable_rate_options":counts["china_applicable_rate_options"],
                           "rate_option_effective_status_counts":dict(sorted(option_status_counts.items())),
                           "gb_rate_option_projection_complete":True,
                           "xi_rate_option_projection_complete":True},
        "source_versions":{"GB":gb["version_or_catalogue"],
                           "GB_as_of":gb["as_of"].isoformat(),
                           "XI_as_of":xi["as_of"].isoformat(),
                           "XI_catalogue_sha256":xi["version_or_catalogue"]},
        "geographical_membership_source":{
            "version":geo_meta["version"],"as_of":geo_meta["as_of"],
            "sha256":geo["source_sha256"],
            "active_china_group_count":len(china_groups),
            "active_china_group_ids":sorted(china_groups)},
        "xi_geographical_membership_source":{
            "source":xi_geo["source"],"as_of":xi_geo["as_of"],
            "sha256":xi_geo["source_sha256"],
            "active_china_group_count":len(xi_groups),
            "active_china_group_ids":sorted(xi_groups)},
        "coverage":{"GB":len(gb_codes),"XI":len(xi_codes)},
        "rate_scope":"China-origin ordinary 103 duties prefer unconditional CN-specific rates, then same-as-of official GB/XI geographical groups containing CN, then Erga Omnes 1011; rate_options preserve every GB/XI non-remedy measure row whose geography is CN, 1011 or a same-as-of verified group containing CN, across active/expired/future/unknown dates; 551/552/553/554 trade-remedy types are retained separately with exporter codes and dates; VAT and live quota balances are outside this measures dataset and are not certified here",
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
                "geo-memberships","geo-memberships-report","xi-geographical-areas",
                "xi-geographical-areas-report","output-db","output-metadata"):
        parser.add_argument("--"+arg,required=True)
    a=vars(parser.parse_args())
    print(json.dumps(build(a["gb_db"],a["gb_catalogue"],a["xi_db"],
                     a["xi_catalogue"],a["xi_manifest"],
                     a["output_db"],a["output_metadata"],
                     a["geo_memberships"],a["geo_memberships_report"],
                     a["xi_geographical_areas"],a["xi_geographical_areas_report"]),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
