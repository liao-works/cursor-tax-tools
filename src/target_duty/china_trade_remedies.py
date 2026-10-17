"""Project China-origin GB/XI anti-dumping and countervailing source measures.

Anti-dumping: 551 provisional / 552 definitive; countervailing: 553/554.
A commodity may have different exporter-specific additional codes. Keep every
applicable source row and never claim an exporter-specific rate applies to
all Chinese goods. These charges are separate from the standard MFN duty.
"""
from __future__ import annotations

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


def _eligible_geo(geo: str, excluded: str) -> bool:
    # Explicit China and Erga Omnes (only when China is not excluded).
    if geo not in (CN, WORLD):
        return False
    countries = set(re.split(r"[|,;\s]+", excluded.upper())) if excluded else set()
    return CN not in countries


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


def gb_remedies(rows: list[tuple], as_of: date) -> list[dict]:
    """Project every source remedy row and classify validity; summary uses active only."""
    result = []
    for kind, geo, expr, extra, conditions, excluded, start, end, raw in rows:
        if kind not in TYPES:
            continue
        if not _eligible_geo(_value(geo), _value(excluded)):
            continue
        record = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(record, dict):
            raise ValueError("Malformed GB original measure JSON")
        result.append({
            "type": TYPES[kind], "measure_type": kind,
            "region": "GB", "geography": _value(geo),
            "expression": _value(expr), "additional_code": _value(extra),
            "additional_description": _value(record.get("measure__additional_code__description")),
            "conditions": _value(conditions),
            "effective_start": _value(start), "effective_end": _value(end),
            "effective_status": _effective_status(start, end, as_of),
            "source_id": _value(record.get("measure__sid")),
        })
    return result


def _relationship_refs(rel: dict, name: str) -> list[dict]:
    entry = (rel.get(name) or {}).get("data")
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, list):
        return [e for e in entry if isinstance(e, dict)]
    return []


def xi_remedies(rows: list[tuple], as_of: date, original_body: bytes) -> list[dict]:
    """XI measures use JSON:API linked expressions, conditions and extra codes."""
    doc = json.loads(original_body)
    included = {(str(n.get("type")), str(n.get("id"))): n
                for n in doc.get("included", []) if isinstance(n, dict)}
    result = []
    for kind, geo, expr, start, end, raw in rows:
        if kind not in TYPES:
            continue
        node = json.loads(raw)
        rel = node.get("relationships") or {}
        # Relationship exclusions must not hide CN; a missing referenced
        # geography cannot justify projecting a general China rate.
        excluded = set()
        unresolved_exclusion = False
        for name in ("excluded_countries", "excluded_geographical_areas"):
            for r in _relationship_refs(rel, name):
                ref = included.get((str(r.get("type")), str(r.get("id"))))
                if ref is None:
                    if str(r.get("id", "")).upper() in ("CN", "CHN", "CHINA"):
                        excluded.add("CN")
                    else:
                        unresolved_exclusion = True
                    continue
                attrs = ref.get("attributes") or {}
                values = [attrs.get(key) for key in
                          ("code", "country_code", "geographical_area_id", "area_id", "id")]
                tokens = {token.strip().upper() for value in values if value
                          for token in re.split(r"[|,;\s]+", str(value)) if token.strip()}
                description = str(attrs.get("description") or attrs.get("name") or "").strip().lower()
                if description in ("china", "people's republic of china", "people’s republic of china"):
                    tokens.add("CN")
                if not tokens:
                    unresolved_exclusion = True
                excluded.update(tokens)
        if unresolved_exclusion or not _eligible_geo(_value(geo), "|".join(excluded)):
            continue
        linked_expr = _relationship_refs(rel, "duty_expression")
        if len(linked_expr) > 1:
            expr = ""  # Multiple expressions are not safely a single rate.
        elif linked_expr:
            ref = linked_expr[0]
            linked = included.get((str(ref.get("type")), str(ref.get("id"))))
            if linked is not None:
                attrs = linked.get("attributes") or {}
                expr = attrs.get("base") or attrs.get("verbose_duty") or expr
        extras = []
        descriptions = []
        for name in ("additional_code", "additional_codes"):
            for ref in _relationship_refs(rel, name):
                linked = included.get((str(ref.get("type")), str(ref.get("id"))), {})
                attrs = linked.get("attributes") or {}
                extras.append(_value(attrs.get("code") or attrs.get("additional_code") or ref.get("id")))
                descriptions.append(_value(attrs.get("description") or attrs.get("formatted_description")))
        conds = any(_relationship_refs(rel, k) for k in
                    ("measure_conditions", "measure_condition_permutation_groups"))
        result.append({
            "type": TYPES[kind], "measure_type": kind,
            "region": "XI", "geography": _value(geo),
            "expression": _value(expr),
            "additional_code": ",".join(sorted(set(c for c in extras if c))),
            "additional_description": "; ".join(d for d in descriptions if d),
            "conditions": "见官方措施条件" if conds else "",
            "effective_start": _value(start), "effective_end": _value(end),
            "effective_status": _effective_status(start, end, as_of),
            "source_id": _value(node.get("id")),
        })
    return result


def summary(measures: list[dict], kind: str) -> str:
    """Display only an unambiguous rate, a clearly labelled residual, or a
    company-dependent indication. No measures -> blank (not zero duty).
    """
    applicable = [r for r in measures if r["type"] == kind
                  and r.get("effective_status", "active") == "active"]
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
