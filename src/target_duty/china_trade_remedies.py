"""Project China-origin GB/XI anti-dumping and countervailing source measures.

Anti-dumping: 551 provisional / 552 definitive; countervailing: 553/554.
A commodity may have different exporter-specific additional codes. Keep every
applicable source row and never claim an exporter-specific rate applies to
all Chinese goods. These charges are separate from the standard MFN duty.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from typing import Any

from .catalog import NA

TYPES = {"551": "anti_dumping", "552": "anti_dumping",
         "553": "countervailing", "554": "countervailing"}
PERCENT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%\s*\Z")
ZERO = {"", "[]", "{}", "null", "None", "#NA", "N/A", "NA"}
RESIDUAL = ("all other", "residual amount", "other overseas exporter",
            "other companies", "other exporters")
CN = "CN"
WORLD = "1011"


def _value(value: Any) -> str:
    return "" if value is None or str(value).strip() in ZERO or str(value).strip() in NA else str(value).strip()


def _eligible_geo(geo: str, excluded: str, china_groups: set[str] | None = None) -> bool:
    """Return true only for explicit CN, Erga Omnes or a verified China group."""
    countries = {token.strip().upper() for token in re.split(r"[|,;\s]+", excluded)
                 if token.strip()} if excluded else set()
    groups = set(china_groups or ())
    if countries & ({"CN", "CHN", "CHINA"} | groups):
        return False
    return geo in (CN, WORLD) or geo in groups


def _effective_status(start: str, end: str, as_of: date) -> str:
    if not _value(start):
        return "unknown"
    try:
        first = date.fromisoformat(str(start)[:10])
        last = date.fromisoformat(str(end)[:10]) if _value(end) else None
        if last and last < first:
            return "unknown"
    except (TypeError, ValueError):
        return "unknown"
    if first > as_of:
        return "future"
    if last and last < as_of:
        return "expired"
    return "active"


def gb_remedies(rows, as_of:date, china_groups=None):
    """Keep China-applicable GB trade-remedy measures and their source evidence."""
    result=[]
    for kind,geo,expr,additional,conditions,excluded,start,end,raw in rows:
        if kind not in TYPES:
            continue
        if not _eligible_geo(_value(geo),_value(excluded),china_groups):
            continue
        status=_effective_status(start,end,as_of)
        record=json.loads(raw) if isinstance(raw,str) else (raw or {})
        expression=_value(expr)
        code=_value(additional)
        desc=_value(record.get("measure__additional_code__description"))
        condition_text=_value(conditions)
        regulation_id=_value(record.get("measure__regulation__id"))
        regulation_url=_value(record.get("measure__regulation__url"))
        footnotes=_value(record.get("measure__footnotes"))
        result.append({
            "region":"GB",
            "type":"anti_dumping" if kind in ("551","552") else "countervailing",
            "measure_type":kind,
            "geography":_value(geo),
            "geography_description":_value(record.get("measure__geographical_area__description")),
            "expression":expression,
            "expression_resolution":"source_value" if expression else "source_blank",
            "expression_options":([{"id":"","type":"source_scalar","expression":expression,
                                    "resolved":True,"link_resolved":True}] if expression else []),
            "expression_count":1 if expression else 0,
            "additional_code":code,
            "additional_code_ids":[code] if code else [],
            "additional_description":desc,
            "conditions":condition_text,
            "condition_details":[],
            "condition_rates":[],
            "condition_groups":[],
            "excluded_areas":_value(excluded),
            "footnotes":footnotes,
            "footnote_ids":[],
            "regulation_id":regulation_id,
            "regulation_url":regulation_url,
            "legal_acts":[],
            "source_relationship_ids":{},
            "unresolved_relationships":[],
            "applicability":"china_applicable",
            "source_id":_value(record.get("measure__sid")),
            "effective_start":_value(start),
            "effective_end":_value(end),
            "effective_status":status,
        })
    for item in result:
        item["enterprise_rates"] = enterprise_rate_rows(item)
    return result


def _plain_text(value):
    if value is None:
        return ""
    text=html.unescape(str(value))
    text=re.sub(r"(?i)<br\s*/?>"," ",text)
    text=re.sub(r"<[^>]*>","",text)
    return " ".join(text.split()).strip()


def _relationship_id_map(rel):
    result={}
    for name,entry in sorted((rel or {}).items()):
        data=entry.get("data") if isinstance(entry,dict) else None
        refs=[data] if isinstance(data,dict) else (data if isinstance(data,list) else [])
        ids=[_value(ref.get("id")) for ref in refs if isinstance(ref,dict) and _value(ref.get("id"))]
        if ids:
            result[name]=list(dict.fromkeys(ids))
    return result


def _relationship_refs(rel: dict, name: str) -> list[dict]:
    entry=(rel.get(name) or {}).get("data")
    if isinstance(entry,dict):
        return [entry]
    if isinstance(entry,list):
        return [item for item in entry if isinstance(item,dict)]
    return []



def enterprise_rate_rows(measure: dict) -> list[dict]:
    """Describe every official exporter/enterprise rate without assigning a tariff by guess.

    A measure with one additional code can be attributed to that code. Multiple
    codes are not cross-joined to expressions: the original source would have
    to provide an explicit per-code relationship. Condition expressions remain
    labelled as conditional, never a universally payable rate.
    """
    details = list(measure.get("additional_code_details") or [])
    combined_code = _value(measure.get("additional_code"))
    combined_description = _value(measure.get("additional_description"))
    if details:
        candidate_codes = [item for item in details if item.get("code")]
    elif combined_code:
        codes = [part.strip() for part in combined_code.split("|") if part.strip()]
        descriptions = [part.strip() for part in combined_description.split("|")]
        candidate_codes = [
            {"code": code, "description": descriptions[i] if i < len(descriptions) else "",
             "resolved": True, "id": code}
            for i, code in enumerate(codes)
        ]
    else:
        candidate_codes = []
    unique_codes = {}
    for item in candidate_codes:
        unique_codes.setdefault(_value(item.get("code")), item)
    candidate_codes = list(unique_codes.values())
    company_bound = len(candidate_codes) == 1 and candidate_codes[0].get("resolved", True)
    if len(candidate_codes) == 1:
        entry = candidate_codes[0]
        code = _value(entry.get("code"))
        company = _value(entry.get("description"))
        matched = bool(company_bound)
    elif len(candidate_codes) > 1:
        code = "|".join(entry["code"] for entry in candidate_codes)
        company = "多企业附加代码，原始措施未标识每个税率的对应关系"
        matched = False
    elif measure.get("conditions"):
        code, company, matched = "", "未指定企业（有条件适用）", False
    else:
        code, company, matched = "", "全部适用企业（All）", True
    if not company:
        company = "官方未提供企业名称"
    company_lower = company.strip().lower()
    is_other = bool(
        code and (
            any(token in company_lower for token in RESIDUAL)
            or company_lower in ("other", "others", "其他", "其他企业")
            and code.upper().endswith("999")
        )
    )
    base = {
        "region": measure.get("region", ""),
        "type": measure.get("type", ""),
        "measure_type": measure.get("measure_type", ""),
        "enterprise": "其他企业（Other）" if is_other else company,
        "enterprise_description": company,
        "additional_code": code,
        "additional_code_resolved": matched,
        "other_enterprises": is_other,
        "effective_status": measure.get("effective_status", "unknown"),
        "applicability": measure.get("applicability", "china_applicable"),
        "source_id": measure.get("source_id", ""),
        "condition": "",
        "condition_id": "",
        "condition_code": "",
        "certificate_code": "",
        "certificate_description": "",
        "action_code": "",
        "action": "",
        "condition_group_ids": [],
        "condition_required": bool(measure.get("conditions")),
    }
    rows = []
    for condition in measure.get("condition_rates") or []:
        row = dict(base)
        row.update({
            "expression": _value(condition.get("expression")),
            "expression_origin": "condition",
            "condition": _value(condition.get("condition")),
            "condition_id": _value(condition.get("condition_id")),
            "condition_code": _value(condition.get("condition_code")),
            "certificate_code": _value(condition.get("certificate_code")),
            "certificate_description": _value(condition.get("certificate_description")),
            "action_code": _value(condition.get("action_code")),
            "action": _value(condition.get("action")),
            "condition_group_ids": condition.get("group_ids") or [],
            "condition_required": True,
        })
        rows.append(row)
    expression = _value(measure.get("expression"))
    # Preserve the base duty even if conditional expressions exist; these are
    # distinct official source records and their purposes must not be confused.
    if expression or not rows:
        row = dict(base)
        row.update({"expression": expression,
                    "expression_origin": "measure" if expression else "unavailable",
                    "condition": _value(measure.get("conditions"))})
        rows.append(row)
    for row in rows:
        if row["applicability"] != "china_applicable":
            row["selection_status"] = "unresolved_applicability"
        elif not row["additional_code_resolved"]:
            row["selection_status"] = "enterprise_or_condition_unresolved"
        elif not row["expression"]:
            row["selection_status"] = "expression_unavailable"
        elif row["condition_required"]:
            row["selection_status"] = "requires_condition"
        else:
            row["selection_status"] = "exact_official_expression"
    return rows

def xi_remedies(rows, as_of:date, original_body:bytes, china_groups=None, area_descriptions=None):
    """Preserve XI trade-remedy expressions, condition rates, and unresolved source links."""
    doc=json.loads(original_body.decode("utf-8"))
    included={(x.get("type"),str(x.get("id"))):x for x in doc.get("included",[])
              if isinstance(x,dict) and x.get("id") is not None}
    area_descriptions=area_descriptions or {}
    known_area_ids={"CN","1011","WORLD","XI","GB"}|set(china_groups or ())|set(area_descriptions)
    result=[]
    for kind,geo,expr,start,end,raw in rows:
        if kind not in TYPES:
            continue
        node=json.loads(raw) if isinstance(raw,str) else (raw or {})
        rel=node.get("relationships") or {}
        excluded=set()
        unresolved_exclusion=False
        unresolved=[]
        def mark_unresolved(relationship, ref):
            ref_id=_value(ref.get("id")) if isinstance(ref,dict) else _value(ref)
            item={"relationship":relationship,"id":ref_id}
            if item not in unresolved:
                unresolved.append(item)
        for key in ("excluded_countries","excluded_geographical_areas"):
            for ref in _relationship_refs(rel,key):
                ref_id=_value(ref.get("id"))
                linked=included.get((ref.get("type"),ref_id))
                if linked is None:
                    if ref_id:
                        excluded.add(ref_id)
                    if ref_id not in known_area_ids:
                        unresolved_exclusion=True
                        mark_unresolved(key,ref)
                    continue
                attrs=linked.get("attributes") or {}
                area=_value(attrs.get("code")) or ref_id
                if area:
                    excluded.add(area)
        geography=_value(geo)
        if not _eligible_geo(geography,"",china_groups):
            continue
        if not _eligible_geo(geography,"|".join(sorted(excluded)),china_groups):
            continue
        applicability="unresolved" if unresolved_exclusion else "china_applicable"

        source_relationship_ids=_relationship_id_map(rel)
        expression_refs=_relationship_refs(rel,"duty_expression")
        expression_options=[]
        linked_expression_values=[]
        for ref in expression_refs:
            ref_id=_value(ref.get("id"))
            linked=included.get((ref.get("type"),ref_id))
            if linked is None:
                expression_options.append({"id":ref_id,"type":_value(ref.get("type")) or "duty_expression",
                                           "expression":"","resolved":False,"link_resolved":False})
                mark_unresolved("duty_expression",ref)
                linked_expression_values.append("")
                continue
            attrs=linked.get("attributes") or {}
            value=_plain_text(attrs.get("base") or attrs.get("formatted_base") or
                              attrs.get("verbose_duty") or attrs.get("duty_amount"))
            expression_options.append({"id":ref_id,"type":_value(ref.get("type")) or "duty_expression",
                                       "expression":value,"resolved":bool(value),"link_resolved":True})
            linked_expression_values.append(value)
        source_expression=_plain_text(_value(expr))
        if len(expression_refs)>1:
            expression=""
            expression_resolution="multiple"
        elif len(expression_refs)==1:
            expression=linked_expression_values[0] if linked_expression_values else ""
            if not expression_options[0]["link_resolved"]:
                expression_resolution="unresolved_link"
            elif expression:
                expression_resolution="resolved_single"
            else:
                expression_resolution="linked_but_empty"
        elif source_expression:
            expression=source_expression
            expression_resolution="source_scalar"
            expression_options=[{"id":"","type":"source_scalar","expression":expression,
                                 "resolved":True,"link_resolved":True}]
        else:
            expression=""
            expression_resolution="source_blank"

        group_refs=_relationship_refs(rel,"measure_condition_permutation_groups")
        condition_refs=list(_relationship_refs(rel,"measure_conditions"))
        condition_group_ids={}
        condition_groups=[]
        for group_ref in group_refs:
            group_id=_value(group_ref.get("id"))
            group=included.get((group_ref.get("type"),group_id))
            if group is None:
                mark_unresolved("measure_condition_permutation_groups",group_ref)
                condition_groups.append({"id":group_id,"resolved":False,"condition_code":"","condition_ids":[]})
                continue
            attrs=group.get("attributes") or {}
            members=_relationship_refs(group.get("relationships") or {},"measure_conditions")
            member_ids=[]
            for member in members:
                member_id=_value(member.get("id"))
                if member_id:
                    member_ids.append(member_id)
                    condition_group_ids.setdefault(member_id,[]).append(group_id)
                    if not any(_value(existing.get("id"))==member_id for existing in condition_refs):
                        condition_refs.append(member)
                    if included.get((member.get("type"),member_id)) is None:
                        mark_unresolved("measure_conditions",member)
            condition_groups.append({
                "id":group_id,"resolved":True,
                "condition_code":_value(attrs.get("condition_code")),
                "condition_ids":list(dict.fromkeys(member_ids)),
            })
        unique_condition_refs=[]
        seen_conditions=set()
        for ref in condition_refs:
            key=(str(ref.get("type")),str(ref.get("id")))
            if key not in seen_conditions:
                seen_conditions.add(key)
                unique_condition_refs.append(ref)
        condition_details=[]
        for ref in unique_condition_refs:
            condition_id=_value(ref.get("id"))
            linked=included.get((ref.get("type"),condition_id))
            if linked is None:
                mark_unresolved("measure_conditions",ref)
                condition_details.append({
                    "id":condition_id,"resolved":False,"condition_code":"","condition":"",
                    "action_code":"","action":"","certificate_code":"","certificate_description":"",
                    "duty_expression":"","condition_duty_amount":"","condition_monetary_unit_code":"",
                    "condition_measurement_unit_code":"","condition_measurement_unit_qualifier_code":"",
                    "requirement":"","group_ids":condition_group_ids.get(condition_id,[]),
                })
                continue
            attrs=linked.get("attributes") or {}
            condition_details.append({
                "id":condition_id,"resolved":True,
                "condition_code":_value(attrs.get("condition_code")),
                "condition":_plain_text(attrs.get("condition") or attrs.get("description")),
                "action_code":_value(attrs.get("action_code")),
                "action":_plain_text(attrs.get("action")),
                "certificate_code":_value(attrs.get("document_code") or attrs.get("certificate_code")),
                "certificate_description":_plain_text(attrs.get("certificate_description")),
                "duty_expression":_plain_text(attrs.get("duty_expression")),
                "condition_duty_amount":_value(attrs.get("condition_duty_amount")),
                "condition_monetary_unit_code":_value(attrs.get("condition_monetary_unit_abbreviation") or
                                                       attrs.get("condition_monetary_unit_code")),
                "condition_measurement_unit_code":_value(attrs.get("condition_measurement_unit_code")),
                "condition_measurement_unit_qualifier_code":_value(attrs.get("condition_measurement_unit_qualifier_code")),
                "requirement":_plain_text(attrs.get("requirement")),
                "component_sequence_number":attrs.get("component_sequence_number"),
                "group_ids":condition_group_ids.get(condition_id,[]),
            })
        condition_rates=[]
        for condition in condition_details:
            if condition.get("duty_expression"):
                condition_rates.append({
                    "condition_id":condition["id"],
                    "group_ids":condition.get("group_ids",[]),
                    "condition_code":condition.get("condition_code",""),
                    "condition":condition.get("condition",""),
                    "action_code":condition.get("action_code",""),
                    "action":condition.get("action",""),
                    "certificate_code":condition.get("certificate_code",""),
                    "certificate_description":condition.get("certificate_description",""),
                    "expression":condition["duty_expression"],
                    "requirement":condition.get("requirement",""),
                })

        additional_details=[]
        for ref in _relationship_refs(rel,"additional_code"):
            ref_id=_value(ref.get("id"))
            linked=included.get((ref.get("type"),ref_id))
            if linked is None:
                mark_unresolved("additional_code",ref)
                additional_details.append({"id":ref_id,"code":ref_id,"description":"","resolved":False})
                continue
            attrs=linked.get("attributes") or {}
            code=_value(attrs.get("code") or attrs.get("additional_code")) or ref_id
            desc=_plain_text(attrs.get("description") or attrs.get("formatted_description"))
            additional_details.append({"id":ref_id,"code":code,"description":desc,"resolved":True})
        additional_codes=list(dict.fromkeys(x["code"] for x in additional_details if x.get("code")))
        additional_descriptions=list(dict.fromkeys(x["description"] for x in additional_details if x.get("description")))

        footnote_details=[]
        for ref in _relationship_refs(rel,"footnotes"):
            ref_id=_value(ref.get("id"))
            linked=included.get((ref.get("type"),ref_id))
            if linked is None:
                mark_unresolved("footnotes",ref)
                footnote_details.append({"id":ref_id,"code":ref_id,"resolved":False})
                continue
            attrs=linked.get("attributes") or {}
            footnote_details.append({"id":ref_id,"code":_value(attrs.get("code")) or ref_id,"resolved":True})
        footnote_codes=list(dict.fromkeys(x["code"] for x in footnote_details if x.get("code")))

        legal_acts=[]
        for ref in _relationship_refs(rel,"legal_acts"):
            ref_id=_value(ref.get("id"))
            linked=included.get((ref.get("type"),ref_id))
            if linked is None:
                mark_unresolved("legal_acts",ref)
                legal_acts.append({"id":ref_id,"regulation_id":ref_id,"regulation_url":"","resolved":False})
                continue
            attrs=linked.get("attributes") or {}
            legal_acts.append({
                "id":ref_id,
                "regulation_id":_value(attrs.get("regulation_code") or attrs.get("code")) or ref_id,
                "regulation_url":_value(attrs.get("regulation_url") or attrs.get("url")),
                "description":_plain_text(attrs.get("description") or attrs.get("title")),
                "resolved":True,
            })
        regulation_ids=list(dict.fromkeys(x["regulation_id"] for x in legal_acts if x.get("regulation_id")))
        regulation_urls=list(dict.fromkeys(x["regulation_url"] for x in legal_acts if x.get("regulation_url")))
        conditions="见官方措施条件" if unique_condition_refs or group_refs else ""
        unresolved.sort(key=lambda item:(item["relationship"],item["id"]))
        result.append({
            "region":"XI",
            "type":"anti_dumping" if kind in ("551","552") else "countervailing",
            "measure_type":kind,
            "geography":geography,
            "geography_description":_value(area_descriptions.get(geography)),
            "expression":expression,
            "expression_resolution":expression_resolution,
            "expression_options":expression_options,
            "expression_count":len(expression_options),
            "additional_code":"|".join(additional_codes),
            "additional_code_ids":list(dict.fromkeys(x["id"] for x in additional_details if x.get("id"))),
            "additional_description":"|".join(additional_descriptions),
            "additional_code_details":additional_details,
            "conditions":conditions,
            "condition_details":condition_details,
            "condition_rates":condition_rates,
            "condition_groups":condition_groups,
            "excluded_areas":"|".join(sorted(excluded)),
            "footnotes":"|".join(footnote_codes),
            "footnote_ids":list(dict.fromkeys(x["id"] for x in footnote_details if x.get("id"))),
            "regulation_id":"|".join(regulation_ids),
            "regulation_url":"|".join(regulation_urls),
            "legal_acts":legal_acts,
            "source_relationship_ids":source_relationship_ids,
            "unresolved_relationships":unresolved,
            "applicability":applicability,
            "unresolved_exclusion":unresolved_exclusion,
            "source_id":_value(node.get("id")),
            "effective_start":_value(start),
            "effective_end":_value(end),
            "effective_status":_effective_status(start,end,as_of),
        })
    for item in result:
        item["enterprise_rates"] = enterprise_rate_rows(item)
    return result

def summary(measures: list[dict], kind: str) -> str:
    """Display only an unambiguous rate, a clearly labelled residual, or a
    company-dependent indication. No measures -> blank (not zero duty).
    """
    applicable = [r for r in measures if r["type"] == kind
                  and r.get("effective_status", "active") == "active"
                  and r.get("applicability", "china_applicable") == "china_applicable"]
    if not applicable:
        return ""
    # Prefer definitive over provisional when both exist for the same item.
    definitive = "552" if kind == "anti_dumping" else "554"
    if any(r["measure_type"] == definitive for r in applicable):
        applicable = [r for r in applicable if r["measure_type"] == definitive]
    normal = [r for r in applicable if not r["conditions"]]
    general = [r for r in normal if not r["additional_code"] and r["expression"]]
    if len({r["expression"] for r in general}) == 1 and general:
        if not any(r["additional_code"] for r in applicable):
            return general[0]["expression"]
    residual = [r for r in normal if r["additional_code"] and r["expression"]
                and (any(token in r["additional_description"].lower() for token in RESIDUAL)
                     or (r["additional_description"].strip().lower() in ("other", "others")
                         and r["additional_code"].upper().endswith("999")))]
    if len(residual) == 1:
        r = residual[0]
        return f'{r["expression"]}（其他企业，{r["additional_code"]}）'
    # More than one producer rate is not a single payable percentage. Rows are
    # preserved in the source-review detail table, not silently discarded.
    return "按企业/条件适用（见详情）"
