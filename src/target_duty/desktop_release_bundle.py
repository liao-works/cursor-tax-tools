"""Build a deterministic desktop database from two independently complete sources.

Only one unambiguous active China-applicable type 103 ordinary duty expression
(percentage, specific or compound) enters each default-rate field. Conditional/
special-use rates and trade remedies are preserved separately, never guessed as
zero. Both source snapshots must replay, hash-verify and cover their catalogue.
"""
from __future__ import annotations
import argparse
import hashlib
import html
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


def _geography_contains_china(geography, china_groups=None):
    """Test scope only; exclusions are retained and evaluated separately."""
    geos=_area_tokens(geography)
    groups=set(china_groups or ())
    return bool(geos & {"CN","CHN","CHINA","1011"} or geos & groups)


def _china_applicable(geography, excluded, china_groups=None):
    """Resolve China against explicit areas, groups and all source exclusions."""
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


def _xi_html_text(value):
    """Plain text of an official HTML duty expression (e.g. '<span>50.00</span> %')."""
    if value is None:
        return ""
    text=html.unescape(str(value))
    text=re.sub(r"(?i)<[^>]*>"," ",text)
    return " ".join(text.split()).strip()


def _xi_rate_options(rows, original_body, as_of, china_groups=None, area_descriptions=None):
    document=json.loads(original_body)
    index=_xi_relation_map(document)
    options=[]

    def unique_refs(refs):
        seen=set()
        result=[]
        for ref in refs:
            key=(str(ref.get("type")),str(ref.get("id")))
            if key not in seen:
                seen.add(key)
                result.append(ref)
        return result

    def condition_tree(refs, stack=()):
        result=[]
        for ref in refs:
            ref_type=str(ref.get("type") or "")
            ref_id=str(ref.get("id") or "")
            key=(ref_type,ref_id)
            node=index.get(key)
            if node is None:
                result.append({"id":ref_id,"type":ref_type,"resolved":False,"description":""})
                continue
            attrs=node.get("attributes") or {}
            item={"id":ref_id,"type":ref_type,"resolved":True,
                  "description":_xi_text([node]),"attributes":attrs}
            if key not in stack:
                children=[]
                for name in sorted(node.get("relationships") or {}):
                    if "condition" not in name.lower():
                        continue
                    children.extend(condition_tree(_xi_refs(node,name),stack+(key,)))
                if children:
                    item["children"]=children
            result.append(item)
        return result

    def condition_labels(items):
        labels=[]
        for item in items:
            if item.get("description"):
                labels.append(item["description"])
            labels.extend(condition_labels(item.get("children") or []))
        return list(dict.fromkeys(labels))

    def condition_rate_rows(items):
        """Per-condition official duty expressions (control measures carry their
        conditional amounts on measure_conditions, not on the measure itself)."""
        rates=[]
        for item in items:
            attrs=item.get("attributes") or {}
            expr=_expression(_xi_html_text(attrs.get("duty_expression")))
            if expr:
                rates.append({
                    "condition_id":item.get("id",""),
                    "group_ids":item.get("group_ids",[]),
                    "condition_code":_clean(attrs.get("condition_code")),
                    "condition":_clean(item.get("description")),
                    "action_code":_clean(attrs.get("action_code")),
                    "action":_clean(attrs.get("action")),
                    "certificate_code":_clean(attrs.get("document_code") or attrs.get("certificate_code")),
                    "certificate_description":_clean(attrs.get("certificate_description")),
                    "expression":expr,
                    "requirement":_clean(attrs.get("requirement")),
                })
            rates.extend(condition_rate_rows(item.get("children") or []))
        return rates

    for kind, geo, expression, start, end, raw in rows:
        node=json.loads(raw) if isinstance(raw,str) else raw
        rel=node.get("relationships") or {}

        expression_refs=unique_refs(_xi_refs(node,"duty_expression"))
        expression_options=[]
        unresolved=[]
        for ref in expression_refs:
            ref_type=str(ref.get("type") or "")
            ref_id=str(ref.get("id") or "")
            linked=index.get((ref_type,ref_id))
            if linked is None:
                expression_options.append({"id":ref_id,"type":ref_type,
                                           "expression":"","resolved":False})
                unresolved.append({"relationship":"duty_expression","id":ref_id})
                continue
            attrs=linked.get("attributes") or {}
            expr_value=_expression(attrs.get("base") or attrs.get("verbose_duty")
                                   or attrs.get("formatted_duty") or attrs.get("duty_amount"))
            expression_options.append({"id":ref_id,"type":ref_type,
                                       "expression":expr_value,"resolved":bool(expr_value),
                                       "attributes":attrs})
            # A linked-but-empty duty expression is the official state of
            # control measures (prohibitions, CITES, statistics): there is no
            # duty to resolve, which is distinct from a broken link.
        scalar_expression=_expression(expression)
        if len(expression_refs)>1:
            expr=""
            expression_resolution="multiple"
        elif len(expression_refs)==1:
            expr=expression_options[0]["expression"] or scalar_expression
            expression_resolution=("resolved_single" if expression_options[0]["resolved"]
                                   else "linked_but_empty")
        elif scalar_expression:
            expr=scalar_expression
            expression_resolution="source_scalar"
            expression_options=[{"id":"","type":"source_measure_field",
                                 "expression":scalar_expression,"resolved":True}]
        else:
            expr=""
            expression_resolution="missing"

        additional_refs=unique_refs(
            _xi_refs(node,"additional_code")+_xi_refs(node,"additional_codes"))
        additional=_xi_linked(index,additional_refs)
        condition_refs=unique_refs(
            _xi_refs(node,"measure_conditions")
            +_xi_refs(node,"measure_condition_permutation_groups"))
        conditions=_xi_linked(index,condition_refs)
        condition_details=condition_tree(condition_refs)
        for ref in additional_refs:
            if (str(ref.get("type")),str(ref.get("id"))) not in index:
                unresolved.append({"relationship":"additional_code","id":str(ref.get("id") or "")})
        for ref in condition_refs:
            if (str(ref.get("type")),str(ref.get("id"))) not in index:
                unresolved.append({"relationship":"measure_conditions","id":str(ref.get("id") or "")})

        excluded_refs=unique_refs(
            _xi_refs(node,"excluded_countries")
            +_xi_refs(node,"excluded_geographical_areas"))
        excluded=_xi_linked(index,excluded_refs)
        geo_refs=unique_refs(_xi_refs(node,"geographical_area"))
        geos=_xi_linked(index,geo_refs)
        type_refs=unique_refs(_xi_refs(node,"measure_type"))
        type_nodes=_xi_linked(index,type_refs)
        for name, refs in (("geographical_area",geo_refs),("measure_type",type_refs)):
            for ref in refs:
                if (str(ref.get("type")),str(ref.get("id"))) not in index:
                    unresolved.append({"relationship":name,"id":str(ref.get("id") or "")})

        additional_codes=[]
        for item in additional:
            attrs=item.get("attributes") or {}
            code=_clean(attrs.get("code") or attrs.get("additional_code"))
            if code:
                additional_codes.append(code)
        excluded_codes=[]
        for item in excluded:
            attrs=item.get("attributes") or {}
            code=(attrs.get("code") or attrs.get("country_code")
                  or attrs.get("geographical_area_id") or attrs.get("area_id")
                  or attrs.get("id") or item.get("id"))
            excluded_codes.append(_clean(code))
        linked_excluded_ids={str(x.get("id")) for x in excluded}
        unresolved_exclusion=False
        for ref in excluded_refs:
            ref_id=_clean(ref.get("id"))
            if ref_id in linked_excluded_ids:
                continue
            excluded_codes.append(ref_id)
            unresolved.append({"relationship":"excluded_geographical_area","id":ref_id})
            if (ref_id not in (area_descriptions or {})
                    and ref_id not in set(china_groups or ())
                    and not (len(ref_id)==2 and ref_id.isalpha())
                    and ref_id not in ("CN","1011")):
                unresolved_exclusion=True

        geo_options=[]
        for ref in geo_refs:
            ref_id=str(ref.get("id") or "")
            linked=index.get((str(ref.get("type")),ref_id))
            if linked is None:
                code=ref_id
                desc=_clean((area_descriptions or {}).get(ref_id,""))
                resolved=bool(desc or ref_id in set(china_groups or ()) or ref_id in ("CN","1011")
                              or (len(ref_id)==2 and ref_id.isalpha()))
                geo_options.append({"id":ref_id,"code":code,"description":desc,"resolved":resolved})
            else:
                attrs=linked.get("attributes") or {}
                code=_clean(attrs.get("code") or attrs.get("country_code")
                            or attrs.get("geographical_area_id") or attrs.get("area_id")
                            or attrs.get("id") or ref_id)
                geo_options.append({"id":ref_id,"code":code,
                                    "description":_xi_text([linked]),"resolved":True})
        geo_id=_clean(geo or (geo_options[0]["code"] if geo_options else ""))
        scope_ids=[item["code"] for item in geo_options if item.get("code")]
        if geo_id and geo_id not in scope_ids:
            scope_ids.insert(0,geo_id)
        if not scope_ids:
            scope_ids=[geo_id] if geo_id else []
        excluded_string=",".join(dict.fromkeys(x for x in excluded_codes if x))
        china_applicable=(not unresolved_exclusion and any(
            _china_applicable(scope,excluded_string,china_groups) for scope in scope_ids))
        condition_description="; ".join(condition_labels(condition_details))
        if not condition_description and condition_refs:
            condition_description="见官方措施条件（关联记录：" + ", ".join(
                str(x.get("id") or "") for x in condition_refs) + "）"
        additional_description=_xi_text(additional)
        if additional_refs and not additional_description and not additional_codes:
            additional_description="官方附加代码关联记录尚未解析"

        option={
            "region":"XI",
            "measure_type":str(kind),
            "measure_type_description":_xi_text(type_nodes),
            "geography":geo_id,
            "geography_ids":list(dict.fromkeys(scope_ids)),
            "geography_options":geo_options,
            "geography_description":("; ".join(dict.fromkeys(
                x["description"] for x in geo_options if x.get("description")))
                or _clean((area_descriptions or {}).get(geo_id,""))),
            "expression":expr,
            "expression_options":expression_options,
            "expression_count":len(expression_refs) if expression_refs else (1 if scalar_expression else 0),
            "expression_resolution":expression_resolution,
            "additional_code":",".join(dict.fromkeys(additional_codes)),
            "additional_description":additional_description,
            "conditions":condition_description,
            "condition_details":condition_details,
            "condition_rates":condition_rate_rows(condition_details),
            "excluded_areas":excluded_string,
            "effective_start":_clean(start),
            "effective_end":_clean(end),
            "effective_status":_effective_status(start,end,as_of),
            "condition_ids":[str(x.get("id") or "") for x in condition_refs if str(x.get("id") or "")],
            "additional_code_ids":[str(x.get("id") or "") for x in additional_refs if str(x.get("id") or "")],
            "source_id":_clean(node.get("id")),
            "unresolved_relationships":unresolved,
            "source_relationship_ids":{
                name:[_clean(x.get("id")) for x in _xi_refs(node,name) if _clean(x.get("id"))]
                for name in sorted(rel)
            },
        }
        option["expression_available"]=any(bool(item.get("expression")) for item in expression_options)
        option["single_expression_available"]=bool(expr) and expression_resolution!="multiple"
        option["special_use"]=option["measure_type"]=="105"
        option["china_applicable"]=china_applicable
        option["conditional"]=bool(
            additional_refs or condition_refs or option["additional_code"]
            or option["conditions"] or option["special_use"])
        # A linked-but-empty measure expression is the official state of control
        # measures (no duty exists). On a type-103 duty row it still means the
        # payable amount is unknown — unless the measure's own conditions carry
        # the official conditional amounts, in which case they are resolvable.
        has_condition_rates=bool(option["condition_rates"])
        unresolved_expression=(expression_resolution in ("multiple","unresolved_link","missing")
                               or (expression_resolution=="linked_but_empty"
                                   and option["measure_type"]=="103"
                                   and not has_condition_rates))
        option["applicability"]=(
            "default" if option["measure_type"]=="103" and option["china_applicable"]
            and not option["conditional"] and not unresolved_expression else
            "authorised_use" if option["special_use"] else
            "unresolved" if unresolved or unresolved_expression else
            "other_measure" if option["measure_type"] not in ("103","105") else
            "conditional"
        )
        options.append(option)
    return options



def _blank(value):
    """DBT CSV export represents missing attributes with #NA/N/A/None."""
    return value is None or str(value).strip() in NA or str(value).strip() in ("[]","{}")


def _default_rate_gap(region, code, selected_rate, source_rows, options, china_groups=None):
    """Explain every blank China-origin default rate without inventing a rate."""
    if not _blank(selected_rate):
        return None
    all_103=[o for o in options if str(o.get("measure_type"))=="103"]
    applicable=[o for o in all_103 if o.get("china_applicable")]
    active=[o for o in applicable if o.get("effective_status")=="active"]
    unconditional=[o for o in active if not o.get("conditional")]
    groups=set(china_groups or ())
    selected=[]
    if not all_103:
        reason="no_103_source_rows" if not source_rows else "no_china_applicable_103_rows"
    elif not applicable:
        reason="excluded_or_unresolved_china_applicability"
    elif not active:
        reason="no_active_103_measure_on_snapshot_date"
    elif not unconditional:
        reason="active_103_requires_conditions_or_additional_code"
    else:
        cn=[o for o in unconditional if _area_tokens(o.get("geography")) & {"CN","CHN","CHINA"}]
        group=[o for o in unconditional
               if (_area_tokens(o.get("geography")) & groups)
               and not (_area_tokens(o.get("geography")) & {"1011"})]
        world=[o for o in unconditional if "1011" in _area_tokens(o.get("geography"))]
        selected=cn or group or world or unconditional
        resolutions={o.get("expression_resolution") for o in selected}
        if "multiple" in resolutions:
            reason="selected_103_measure_has_multiple_duty_expressions"
        elif "unresolved_link" in resolutions:
            reason="selected_103_measure_has_unresolved_duty_expression"
        elif any(not o.get("expression_available") for o in selected):
            reason="selected_103_measure_missing_expression"
        elif len({o.get("expression") for o in selected})>1:
            reason="conflicting_unconditional_103_rates_at_selected_scope"
        else:
            reason="default_rate_selection_unresolved"
    fields=("source_id","geography","geography_description","effective_status",
            "expression","expression_options","expression_count","expression_resolution",
            "expression_available","conditional","additional_code","additional_code_ids",
            "additional_description","condition_ids","conditions","condition_details",
            "condition_rates","unresolved_relationships","excluded_areas","applicability",
            "measure_type_description","china_applicable")
    return {
        "region":region,
        "code":code,
        "reason":reason,
        "source_103_row_count":len(source_rows),
        "china_applicable_103_option_count":len(applicable),
        "active_103_option_count":len(active),
        "active_conditional_103_option_count":len(active)-len(unconditional),
        "active_unconditional_103_option_count":len(unconditional),
        "selected_option_count":len(selected),
        "candidate_expressions":sorted({str(o.get("expression") or "") for o in selected}),
        "options":[{key:o.get(key) for key in fields} for o in all_103],
    }


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


def xi_rate(rows, as_of:date, original_body:bytes|None=None, china_groups=None, area_descriptions=None):
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
                ref_id=str(ref.get("id") or "")
                tokens=_area_tokens(ref_id)
                if tokens & {"CN","CHN","CHINA"}:
                    excluded_codes.add("CN")
                elif (ref_id in (area_descriptions or {}) or
                      ref_id in groups or
                      (len(ref_id)==2 and ref_id.isalpha())):
                    excluded_codes.update(tokens)
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
    # Keep rows whose geographic scope includes China even when an exclusion
    # makes them inapplicable; the option UI and gap audit must explain why.
    return [row for row in rows if _geography_contains_china(row[1], groups)]


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


def load_gb_condition_source(path, gb_version):
    """Load and pin-verify the same-version GB condition-component join (fail closed)."""
    source_path = Path(path).resolve()
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("GB condition-component source is not valid JSON") from exc
    # v2 = normalized join only; v3 adds the official live JSON:API recovery
    # of blank condition amounts. Both are accepted so an artifact produced by
    # the previous capture still publishes during rollout.
    if source.get("kind") not in ("gb-condition-components-v2", "gb-condition-components-v3"):
        raise ValueError("Unrecognised GB condition-component source kind")
    if source.get("version") != gb_version:
        raise ValueError("GB condition-component source is not the pinned same version")
    coverage = source.get("coverage") or {}
    if coverage.get("missing_expected_measure_sids"):
        raise ValueError("GB condition-component join missed remedy measure SIDs: "
                         + ", ".join(coverage["missing_expected_measure_sids"][:20]))
    if coverage.get("signature_mismatch_measure_sids"):
        raise ValueError("GB condition-component join disagrees with the expanded "
                         f"condition signature on {coverage['signature_mismatch_measure_sids']} measure SIDs")
    meta = {
        "version": source["version"],
        "source_tables_sha256": {name: table["sha256"] for name, table
                                 in (source.get("source_tables") or {}).items()},
        "coverage": coverage,
    }
    return source, meta


def build(gb_db:str, gb_catalogue:str, xi_db:str, xi_catalogue:str,
          xi_manifest:str, output_db:str, output_metadata:str,
          geo_memberships_path:str, geo_memberships_report_path:str,
          geo_areas_path:str, geo_area_descriptions_path:str,
          xi_geographical_areas_path:str, xi_geographical_areas_report_path:str,
          gb_condition_components_path:str|None=None):
    input_paths=[Path(s).resolve() for s in (gb_db,gb_catalogue,xi_db,xi_catalogue,xi_manifest)]
    out=Path(output_db).resolve()
    meta=Path(output_metadata).resolve()
    audit_path=meta.with_name("rate-gap-audit.json")
    if (out==meta or out in input_paths or meta in input_paths
            or audit_path in input_paths or audit_path in (out,meta)):
        raise ValueError("Never overwrite input source files or collide with release outputs")
    gb=_source(gb_db,region="GB")
    xi=_source(xi_db,region="XI")
    geo_path=Path(geo_memberships_path).resolve()
    geo_report_path=Path(geo_memberships_report_path).resolve()
    geo_areas=Path(geo_areas_path).resolve()
    geo_area_descriptions=Path(geo_area_descriptions_path).resolve()
    xi_geo_path=Path(xi_geographical_areas_path).resolve()
    xi_geo_report_path=Path(xi_geographical_areas_report_path).resolve()
    evidence_paths=(geo_path,geo_report_path,geo_areas,geo_area_descriptions,
                    xi_geo_path,xi_geo_report_path)
    if (any(p in input_paths or p in (out,meta) for p in evidence_paths)
            or len(set(evidence_paths))!=len(evidence_paths)):
        raise ValueError("Geographical membership evidence must be separate source files")
    geo_payload=geo_path.read_bytes()
    geo_areas_payload=geo_areas.read_bytes()
    geo_area_descriptions_payload=geo_area_descriptions.read_bytes()
    geo=inspect_geo_memberships(geo_payload,gb["as_of"],
        area_payload=geo_areas_payload,descriptions_payload=geo_area_descriptions_payload)
    geo_meta=json.loads(geo_report_path.read_text(encoding="utf-8"))
    if (geo_meta.get("kind")!="dbt-official-geo-area-memberships-v2"
            or geo_meta.get("version")!=gb["version_or_catalogue"]
            or geo_meta.get("as_of")!=gb["as_of"].isoformat()
            or geo_meta.get("source_sha256")!=geo["source_sha256"]
            or geo_meta.get("geo_areas_source_sha256")!=geo["geo_areas_source_sha256"]
            or geo_meta.get("geo_area_descriptions_source_sha256")!=geo["geo_area_descriptions_source_sha256"]
            or geo_meta.get("geo_areas_row_count")!=geo["geo_areas_row_count"]
            or geo_meta.get("geo_area_descriptions_row_count")!=geo["geo_area_descriptions_row_count"]
            or geo_meta.get("geo_area_descriptions_missing_description_rows")!=geo["geo_area_descriptions_missing_description_rows"]
            or geo_meta.get("active_china_group_ids")!=geo["active_china_group_ids"]
            or geo_meta.get("unresolved_active_membership_count")!=geo["unresolved_active_membership_count"]):
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
    gb_condition_source=None
    gb_condition_meta=None
    if gb_condition_components_path:
        gb_condition_source,gb_condition_meta=load_gb_condition_source(
            gb_condition_components_path,gb["version_or_catalogue"])
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
            gap_records=[]
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
                    counts["gb_default_duty_codes"]+=bool(gb_rate)
                    counts["gb_percent_only"]+=bool(gb_rate and PERCENT.fullmatch(gb_rate))
                    if not gb_rate:
                        gap=_default_rate_gap("GB",code,gb_rate,source,gb_options,china_groups)
                        if gap:
                            gap_records.append(gap)
                    remedies=list(gb_conn.execute("""SELECT measure_type,geography,expression,
                         additional_code,conditions,excluded_areas,effective_start,effective_end,raw_json
                         FROM measures WHERE region='GB' AND code=?
                         AND measure_type IN ('551','552','553','554') ORDER BY ordinal""",(code,)))
                    gb_trade=gb_remedies(remedies,gb["as_of"],china_groups,condition_source=gb_condition_source)
                    counts["gb_remedy_source_rows"]+=len(remedies)
                    counts["gb_remedy_projected_rows"]+=len(gb_trade)
                    counts["gb_remedy_blank_expression_rows"]+=sum(not item.get("expression") for item in gb_trade)
                    counts["gb_remedy_condition_rate_rows"]+=sum(bool(item.get("condition_rates")) for item in gb_trade)
                    counts["gb_remedy_condition_rate_expressions"]+=sum(len(item.get("condition_rates",[])) for item in gb_trade)
                    counts["gb_remedy_condition_older_revision_amounts"]+=sum(
                        bool(detail.get("amount_from_older_revision"))
                        for item in gb_trade for detail in item.get("condition_details",[]))
                    counts["gb_remedy_condition_unresolved_relationships"]+=sum(
                        len(item.get("unresolved_relationships") or []) for item in gb_trade)
                    counts["gb_remedy_condition_missing_join_rows"]+=sum(
                        bool(item.get("conditions")) and not item.get("condition_details")
                        and gb_condition_source is not None for item in gb_trade)
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
                    ni_rate=xi_rate(source,xi["as_of"],raw,xi_groups,xi_geo["area_descriptions"])
                    xi_option_rows=_xi_rate_option_rows(xi_conn,code,xi_groups)
                    xi_options=_xi_rate_options(xi_option_rows,raw,xi["as_of"],xi_groups,xi_geo["area_descriptions"])
                    if len(xi_options)!=len(xi_option_rows):
                        raise ValueError("XI rate-option projection lost official measure rows for "+code)
                    counts["xi_default_duty_codes"]+=bool(ni_rate)
                    counts["xi_percent_only"]+=bool(ni_rate and PERCENT.fullmatch(ni_rate))
                    if not ni_rate:
                        gap=_default_rate_gap("XI",code,ni_rate,source,xi_options,xi_groups)
                        if gap:
                            gap_records.append(gap)
                    trade_rows=list(xi_conn.execute("""SELECT measure_type,geography_id,
                        duty_expression,effective_start,effective_end,raw_json
                        FROM measures WHERE code=? AND measure_type IN ('551','552','553','554')
                        ORDER BY ordinal""",(code,)))
                    ni_trade=xi_remedies(trade_rows,xi["as_of"],raw,xi_groups,xi_geo["area_descriptions"])
                    counts["xi_remedy_source_rows"]+=len(trade_rows)
                    counts["xi_remedy_projected_rows"]+=len(ni_trade)
                    counts["xi_remedy_blank_expression_rows"]+=sum(not item.get("expression") for item in ni_trade)
                    counts["xi_remedy_condition_rate_rows"]+=sum(bool(item.get("condition_rates")) for item in ni_trade)
                    counts["xi_remedy_condition_rate_expressions"]+=sum(len(item.get("condition_rates",[])) for item in ni_trade)
                    counts["xi_remedy_unresolved_relationships"]+=sum(len(item.get("unresolved_relationships",[])) for item in ni_trade)
                    counts["xi_remedy_unresolved_applicability_rows"]+=sum(item.get("applicability")=="unresolved" for item in ni_trade)
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
                for region, options in (("GB", gb_options), ("XI", xi_options)):
                    for option in options:
                        option_status_counts[region+":"+option["effective_status"]]+=1
                        option_status_counts[region+":type:"+str(option["measure_type"])]+=1
                        option_status_counts[region+":conditional:"+str(bool(option["conditional"])).lower()]+=1
                        option_status_counts[region+":expression:"+str(bool(option["expression_available"])).lower()]+=1
                        option_status_counts[region+":china_applicable:"+str(bool(option["china_applicable"])).lower()]+=1
                        option_status_counts[region+":applicability:"+str(option["applicability"])]+=1
                counts["missing_rate_expressions"]+=sum(
                    1 for option in gb_options+xi_options if not option["expression_available"])
                counts["multiple_duty_expression_options"]+=sum(
                    1 for option in gb_options+xi_options
                    if option.get("expression_resolution")=="multiple")
                counts["unresolved_rate_options"]+=sum(
                    1 for option in gb_options+xi_options if option.get("applicability")=="unresolved")
                counts["xi_option_condition_rate_options"]+=sum(
                    bool(o.get("condition_rates")) for o in xi_options)
                counts["xi_option_condition_rate_expressions"]+=sum(
                    len(o.get("condition_rates") or []) for o in xi_options)
                counts["xi_control_options_without_duty_expression"]+=sum(
                    1 for o in xi_options
                    if o.get("expression_resolution")=="linked_but_empty"
                    and not o.get("expression_available"))
                counts["unresolved_rate_option_relationships"]+=sum(
                    len(option.get("unresolved_relationships") or [])
                    for option in gb_options+xi_options)
                counts["conditional_rate_options"]+=sum(
                    1 for option in gb_options+xi_options if option["conditional"])
                counts["china_applicable_rate_options"]+=sum(
                    1 for option in gb_options+xi_options if option["china_applicable"])
                counts["active_conditional_options"]+=sum(
                    1 for option in gb_options+xi_options
                    if option["effective_status"]=="active" and option["conditional"])
                counts["active_conditional_with_expression"]+=sum(
                    1 for option in gb_options+xi_options
                    if option["effective_status"]=="active" and option["conditional"]
                    and option["expression_available"])
                counts["active_conditional_without_expression"]+=sum(
                    1 for option in gb_options+xi_options
                    if option["effective_status"]=="active" and option["conditional"]
                    and not option["expression_available"])
                counts["active_china_conditional_options"]+=sum(
                    1 for option in gb_options+xi_options
                    if option["effective_status"]=="active" and option["conditional"]
                    and option["china_applicable"])
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
    gap_records.sort(key=lambda item:(item["region"],item["code"]))
    expected_gap_count = (len(gb_codes)-counts["gb_default_duty_codes"]) + (len(xi_codes)-counts["xi_default_duty_codes"])
    if len(gap_records) != expected_gap_count:
        raise ValueError("Not every blank default-duty field has a reasoned audit record")
    gap_counts=Counter(item["region"]+":"+item["reason"] for item in gap_records)
    audit_doc={
        "kind":"china-origin-default-rate-gap-audit-v1",
        "as_of":day.isoformat(),
        "source_versions":{"GB":gb["version_or_catalogue"],"GB_as_of":gb["as_of"].isoformat(),
                           "XI_as_of":xi["as_of"].isoformat(),
                           "XI_catalogue_sha256":xi["version_or_catalogue"]},
        "record_count":len(gap_records),
        "by_region_reason":dict(sorted(gap_counts.items())),
        "records":gap_records,
    }
    audit_bytes=(json.dumps(audit_doc,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode("utf-8")
    audit_hash=hashlib.sha256(audit_bytes).hexdigest()
    audit_path.write_bytes(audit_bytes)
    report={
        "version":version,
        "timestamp":stamp,
        "last_modified":stamp,
        "file_hash":"sha256:"+digest,
        "file_size":out.stat().st_size,
        "record_count":len(gb_codes|xi_codes),
        "changes_summary":{"total_default_duty_entries":counts["gb_default_duty_codes"]+counts["xi_default_duty_codes"],
                           "gb_available_default_duty_codes":counts["gb_default_duty_codes"],
                           "xi_available_default_duty_codes":counts["xi_default_duty_codes"],
                           "gb_available_simple_percent":counts["gb_percent_only"],
                           "xi_available_simple_percent":counts["xi_percent_only"],
                           "gb_anti_dumping_codes":counts["gb_anti_dumping_codes"],
                           "gb_countervailing_codes":counts["gb_countervailing_codes"],
                           "xi_anti_dumping_codes":counts["xi_anti_dumping_codes"],
                           "xi_countervailing_codes":counts["xi_countervailing_codes"],
                           "gb_trade_remedy_source_rows":counts["gb_remedy_source_rows"],
                           "gb_trade_remedy_projected_rows":counts["gb_remedy_projected_rows"],
                           "gb_remedy_condition_rate_rows":counts["gb_remedy_condition_rate_rows"],
                           "gb_remedy_condition_rate_expressions":counts["gb_remedy_condition_rate_expressions"],
                           "gb_remedy_condition_older_revision_amounts":counts["gb_remedy_condition_older_revision_amounts"],
                           "gb_remedy_condition_unresolved_relationships":counts["gb_remedy_condition_unresolved_relationships"],
                           "gb_remedy_condition_missing_join_rows":counts["gb_remedy_condition_missing_join_rows"],
                           "xi_trade_remedy_source_rows":counts["xi_remedy_source_rows"],
                           "xi_trade_remedy_projected_rows":counts["xi_remedy_projected_rows"],
                           "gb_rate_option_codes":counts["gb_rate_option_codes"],
                           "xi_rate_option_codes":counts["xi_rate_option_codes"],
                           "gb_rate_option_source_rows":counts["gb_rate_source_rows"],
                           "gb_rate_option_projected_rows":counts["gb_rate_projected_rows"],
                           "xi_rate_option_source_rows":counts["xi_rate_source_rows"],
                           "xi_rate_option_projected_rows":counts["xi_rate_projected_rows"],
                           "gb_rate_option_projection_complete":counts["gb_rate_source_rows"]==counts["gb_rate_projected_rows"],
                           "xi_rate_option_projection_complete":counts["xi_rate_source_rows"]==counts["xi_rate_projected_rows"],
                           "missing_rate_expressions":counts["missing_rate_expressions"],
                           "multiple_duty_expression_options":counts["multiple_duty_expression_options"],
                           "unresolved_rate_options":counts["unresolved_rate_options"],
                           "unresolved_rate_option_relationships":counts["unresolved_rate_option_relationships"],
                           "xi_option_condition_rate_options":counts["xi_option_condition_rate_options"],
                           "xi_option_condition_rate_expressions":counts["xi_option_condition_rate_expressions"],
                           "xi_control_options_without_duty_expression":counts["xi_control_options_without_duty_expression"],
                           "conditional_rate_options":counts["conditional_rate_options"],
                           "active_conditional_options":counts["active_conditional_options"],
                           "active_conditional_with_expression":counts["active_conditional_with_expression"],
                           "active_conditional_without_expression":counts["active_conditional_without_expression"],
                           "active_china_conditional_options":counts["active_china_conditional_options"],
                           "china_applicable_rate_options":counts["china_applicable_rate_options"],
                           "rate_option_audit_counts":dict(sorted(option_status_counts.items())),
                           "rate_option_effective_status_counts":dict(sorted(option_status_counts.items())),
                           "conditional_rate_policy":"All China-applicable GB/XI non-remedy measure options are retained with dates, geography, duty expression, additional codes and source conditions. Conditional options are shown separately and never silently replace the default. Automatic eligibility requires shipment/authorised-use facts not present in a commodity code alone.",
                        },
        "source_versions":{"GB":gb["version_or_catalogue"],
                           "GB_as_of":gb["as_of"].isoformat(),
                           "XI_as_of":xi["as_of"].isoformat(),
                           "XI_catalogue_sha256":xi["version_or_catalogue"]},
        "geographical_membership_source":{
            "version":geo_meta["version"],"as_of":geo_meta["as_of"],
            "sha256":geo["source_sha256"],
            "geo_areas_sha256":geo["geo_areas_source_sha256"],
            "geo_area_descriptions_sha256":geo["geo_area_descriptions_source_sha256"],
            "geo_area_descriptions_missing_description_rows":geo["geo_area_descriptions_missing_description_rows"],
            "unresolved_active_membership_count":geo["unresolved_active_membership_count"],
            "unresolved_active_memberships":geo["unresolved_active_memberships"],
            "unresolved_historical_memberships":geo["unresolved_historical_memberships"],
            "active_china_group_count":len(china_groups),
            "active_china_group_ids":sorted(china_groups)},
        "xi_geographical_membership_source":{
            "source":xi_geo["source"],"as_of":xi_geo["as_of"],
            "sha256":xi_geo["source_sha256"],
            "active_china_group_count":len(xi_groups),
            "active_china_group_ids":sorted(xi_groups)},
        "coverage":{"GB":len(gb_codes),"XI":len(xi_codes)},
        "default_duty_coverage":{
            "meaning":"Number of commodity codes with one unambiguous active China-applicable default duty expression. Includes percentage, specific and compound duties; excludes conditional/special-use rates.",
            "GB":{"catalogue_codes":len(gb_codes),"codes_with_default_duty":counts["gb_default_duty_codes"],
                 "codes_without_default_duty":len(gb_codes)-counts["gb_default_duty_codes"]},
            "XI":{"catalogue_codes":len(xi_codes),"codes_with_default_duty":counts["xi_default_duty_codes"],
                 "codes_without_default_duty":len(xi_codes)-counts["xi_default_duty_codes"]},
        },
        "trade_remedy_coverage":{
            "GB":{"source_rows_before_china_filter":counts["gb_remedy_source_rows"],
                 "projected_china_applicable_rows":counts["gb_remedy_projected_rows"],
                 "blank_expression_rows":counts["gb_remedy_blank_expression_rows"],
                 "condition_rate_components_collected":gb_condition_source is not None,
                 "condition_rate_measures":counts["gb_remedy_condition_rate_rows"],
                 "condition_rate_expressions":counts["gb_remedy_condition_rate_expressions"],
                 "condition_older_revision_amounts":counts["gb_remedy_condition_older_revision_amounts"],
                 "condition_unresolved_relationship_count":counts["gb_remedy_condition_unresolved_relationships"],
                 "condition_missing_join_rows":counts["gb_remedy_condition_missing_join_rows"],
                 "condition_source":gb_condition_meta,
                 "limitation":(None if gb_condition_source is not None else
                               "The GB expanded-measures source contains condition codes but not condition-component duty amounts; a same-version official condition-component join is still required.")},
            "XI":{"source_rows_before_china_filter":counts["xi_remedy_source_rows"],
                 "projected_china_scope_rows":counts["xi_remedy_projected_rows"],
                 "blank_measure_expression_rows":counts["xi_remedy_blank_expression_rows"],
                 "rows_with_condition_duty_expressions":counts["xi_remedy_condition_rate_rows"],
                 "condition_duty_expression_count":counts["xi_remedy_condition_rate_expressions"],
                 "unresolved_relationship_count":counts["xi_remedy_unresolved_relationships"],
                 "unresolved_applicability_rows":counts["xi_remedy_unresolved_applicability_rows"],
                 "condition_rate_links_preserved":True},
        },
        "rate_option_coverage":{
            "GB":{"catalogue_codes_with_options":counts["gb_rate_option_codes"],
                 "source_rows":counts["gb_rate_source_rows"],
                 "projected_rows":counts["gb_rate_projected_rows"],
                 "projection_complete":counts["gb_rate_source_rows"]==counts["gb_rate_projected_rows"]},
            "XI":{"catalogue_codes_with_options":counts["xi_rate_option_codes"],
                 "source_rows":counts["xi_rate_source_rows"],
                 "projected_rows":counts["xi_rate_projected_rows"],
                 "projection_complete":counts["xi_rate_source_rows"]==counts["xi_rate_projected_rows"]},
            "conditional_options":counts["conditional_rate_options"],
            "missing_expression_options":counts["missing_rate_expressions"],
            "china_applicable_options":counts["china_applicable_rate_options"],
            "effective_status_counts":dict(sorted(option_status_counts.items())),
        },
        "default_rate_gap_audit":{
            "file":audit_path.name,
            "download_url":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/rate-gap-audit.json",
            "sha256":"sha256:"+audit_hash,
            "record_count":len(gap_records),
            "by_region_reason":dict(sorted(gap_counts.items())),
        },
        "rate_scope":"China-origin ordinary 103 duties prefer unconditional CN-specific rates, then same-as-of official GB/XI geographical groups containing CN, then Erga Omnes 1011; rate_options preserve every GB/XI non-remedy measure row whose geography is CN, 1011 or a same-as-of verified group containing CN, across active/expired/future/unknown dates; 551/552/553/554 trade-remedy types are retained separately with exporter codes, per-company conditional duty expressions from the same-version official condition-component join (GB revision-disambiguated), and dates; VAT and live quota balances are outside this measures dataset and are not certified here",
        "download_urls":{
            "primary":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/tariffs.db",
            "metadata":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/metadata.json",
            "audit":"https://github.com/liao-works/cursor-tax-tools/releases/download/latest-data/rate-gap-audit.json"},
    }
    if len(gb_codes)<10_000 or len(xi_codes)<10_000:
        raise ValueError("Suspiciously incomplete official commodity catalogue")
    # Do not overwrite a mostly populated desktop DB with mostly blank rates
    # if the official expression layout changes unexpectedly.
    print("Verified default-duty coverage (percentage, specific and compound expressions) plus exact source-option projection:",dict(counts))
    if counts["gb_default_duty_codes"]<10_000 or counts["xi_default_duty_codes"]<10_000:
        raise ValueError("Insufficient unambiguous GB/XI percentage coverage for desktop release")
    meta.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser()
    for arg in ("gb-db","gb-catalogue","xi-db","xi-catalogue","xi-manifest",
                "geo-memberships","geo-memberships-report","geo-areas","geo-area-descriptions",
                "xi-geographical-areas","xi-geographical-areas-report","output-db","output-metadata"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("--gb-condition-components",default=None,
                        help="Optional same-version GB condition-component join produced by "
                             "src.target_duty.gb_condition_components")
    a=vars(parser.parse_args())
    print(json.dumps(build(a["gb_db"],a["gb_catalogue"],a["xi_db"],
                     a["xi_catalogue"],a["xi_manifest"],
                     a["output_db"],a["output_metadata"],
                     a["geo_memberships"],a["geo_memberships_report"],
                     a["geo_areas"],a["geo_area_descriptions"],
                     a["xi_geographical_areas"],a["xi_geographical_areas_report"],
                     gb_condition_components_path=a.get("gb_condition_components")),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
