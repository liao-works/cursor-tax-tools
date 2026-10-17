"""Fetch same-version official GB condition tables and resolve remedy condition rates.

The expanded measures CSV contains condition codes but not the duty amounts attached
to each condition. This module joins the versioned normalized condition/component
tables without guessing a rate when a source link or component cannot be resolved.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/versions/{version}/tables/{table}/data?format=csv&download"
MAX_TABLE_BYTES = 512 * 1024 * 1024
TABLES = (
    "measures",
    "measure-conditions",
    "measure-condition-components",
    "measure-duty-expressions",
    "measure-monetary-units",
    "measure-measurements",
    "measure-measurements-units",
    "measure-measurement-qualifiers",
    "measure-condition-codes",
    "measure-actions",
    "certificates",
    "certificate-types",
)
PREFIXES = {
    "measures": ("measure", "measures"),
    "measure-conditions": ("measure_condition", "measure_conditions"),
    "measure-condition-components": ("measure_condition_component", "measure_condition_components"),
    "measure-duty-expressions": ("measure_duty_expression", "measure_duty_expressions"),
    "measure-monetary-units": ("measure_monetary_unit", "measure_monetary_units"),
    "measure-measurements": ("measure_measurement", "measure_measurements"),
    "measure-measurements-units": ("measure_measurements_unit", "measure_measurements_units"),
    "measure-measurement-qualifiers": ("measure_measurement_qualifier", "measure_measurement_qualifiers"),
    "measure-condition-codes": ("measure_condition_code", "measure_condition_codes"),
    "measure-actions": ("measure_action", "measure_actions"),
    "certificates": ("certificate", "certificates"),
    "certificate-types": ("certificate_type", "certificate_types"),
}


def _clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value in ("", "#NA", "NA", "null", "None") else value


def _pick(headers, candidates, *, required=True, label="field"):
    lowered = {h.lower(): h for h in headers}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    # Accept prefixed forms such as measure_condition__measure_sid while avoiding
    # ambiguous generic suffixes like "sid", "id", or "code".
    for candidate in candidates:
        if len(candidate) < 5:
            continue
        matches = [h for h in headers
                   if h.lower().endswith("__" + candidate.lower())
                   or h.lower().endswith("_" + candidate.lower())]
        if len(matches) == 1:
            return matches[0]
    if required:
        raise ValueError(f"GB condition source missing {label}; tried {candidates}; headers={headers}")
    return None


def _read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if not headers:
            raise ValueError(f"Empty CSV header in {path}")
        rows = [{str(k): (v or "").strip() for k, v in row.items() if k is not None}
                for row in reader]
    return headers, rows


def download_table(version, table, destination):
    """Download one table from the exact version and return immutable source evidence."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", str(version)):
        raise ValueError(f"Refusing non-versioned GB condition source: {version!r}")
    url = BASE_URL.format(version=version, table=table)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error = None
    for attempt in range(3):
        digest = hashlib.sha256()
        size = 0
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "liao-tool-official-tariff-data/1.0"}
            )
            with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as out:
                final_url = response.geturl()
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_TABLE_BYTES:
                    raise ValueError(f"GB condition table {table} exceeds size limit: {length}")
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_TABLE_BYTES:
                        raise ValueError(f"GB condition table {table} exceeded size limit")
                    digest.update(block)
                    out.write(block)
            partial.replace(destination)
            headers, rows = _read_csv(destination)
            return {
                "table": table, "version": version, "url": url, "final_url": final_url,
                "sha256": digest.hexdigest(), "bytes": size, "row_count": len(rows),
                "columns": headers,
            }, rows
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not download pinned GB condition table {table} at {version}: {last_error}")


def _prefix_fields(table, field, *, required=True):
    prefixes = PREFIXES[table]
    candidates = [field] + [prefix + "__" + field for prefix in prefixes]
    return candidates, required


def _lookup_table(table, rows, headers):
    prefixes = PREFIXES[table]
    pk = _pick(headers, ["trackedmodel_ptr_id"] + [p + "__trackedmodel_ptr_id" for p in prefixes],
               required=False, label=f"{table}.trackedmodel_ptr_id")
    sid = _pick(headers, ["sid"] + [p + "__sid" for p in prefixes],
                required=False, label=f"{table}.sid")
    code = _pick(headers, ["code"] + [p + "__code" for p in prefixes],
                 required=False, label=f"{table}.code")
    abbreviation = _pick(headers, ["abbreviation"] + [p + "__abbreviation" for p in prefixes],
                          required=False, label=f"{table}.abbreviation")
    description = _pick(headers, ["description"] + [p + "__description" for p in prefixes],
                        required=False, label=f"{table}.description")
    lookup = {}
    for row in rows:
        item = {
            "id": _clean(row.get(pk)) if pk else "",
            "sid": _clean(row.get(sid)) if sid else "",
            "code": _clean(row.get(code)) if code else "",
            "abbreviation": _clean(row.get(abbreviation)) if abbreviation else "",
            "description": _clean(row.get(description)) if description else "",
        }
        for key in (item["id"], item["sid"]):
            if key:
                lookup[key] = item
    return lookup


def _ref(lookup, key):
    return lookup.get(_clean(key), {}) if _clean(key) else {}


def _expected_measure_sids(gb_db):
    expected = set()
    relevant = set()
    with sqlite3.connect(gb_db) as conn:
        rows = conn.execute(
            "SELECT measure_type, conditions, raw_json FROM measures "
            "WHERE CAST(measure_type AS TEXT) IN ('551','552','553','554')"
        )
        for kind, conditions, raw in rows:
            try:
                record = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, json.JSONDecodeError):
                continue
            sid = _clean(record.get("measure__sid"))
            if not sid:
                continue
            relevant.add(sid)
            if _clean(conditions):
                expected.add(sid)
    if not relevant:
        raise ValueError("GB source snapshot contains no 551/552/553/554 measure SIDs")
    return relevant, expected


def _format_component(component):
    amount = _clean(component.get("duty_amount"))
    expression_id = _clean(component.get("duty_expression_id"))
    description = _clean(component.get("duty_expression_description"))
    money = _clean(component.get("monetary_unit_code"))
    unit = _clean(component.get("measurement_unit_code"))
    qualifier = _clean(component.get("measurement_unit_qualifier_code"))
    unit_label = ((qualifier + " ") if qualifier else "") + unit
    if money and unit_label:
        base = amount + " " + money + " / " + unit_label if amount else money + " / " + unit_label
    elif money:
        base = amount + " " + money if amount else money
    elif unit_label:
        base = amount + " " + unit_label if amount else unit_label
    elif amount and expression_id in ("1", "4"):
        base = amount + " %"
    elif amount:
        base = amount + " [" + (description or ("表达式 " + expression_id)) + "]"
    else:
        base = description or ("未提供金额（表达式 " + (expression_id or "未知") + "）")
    operators = {
        "1": "", "2": "− ", "3": "替代征税：", "4": "+ ", "5": "替代减征：",
        "6": "+ ", "7": "+ ", "9": "+ ", "11": "+ ", "12": "+ ", "13": "+ ",
        "14": "+ ", "15": "MIN ", "17": "MAX ", "19": "+ ", "20": "+ ",
    }
    prefix = operators.get(expression_id, "")
    if not prefix and expression_id not in operators and description:
        return description + ": " + base
    return prefix + base


def _format_expression(components):
    ordered = sorted(components, key=lambda x: (
        int(x["duty_expression_id"]) if str(x.get("duty_expression_id", "")).isdigit() else 9999,
        str(x.get("duty_expression_id", "")),
    ))
    rendered = []
    for index, component in enumerate(ordered):
        part = _format_component(component)
        if index and part.startswith("+ "):
            rendered.append(part)
        elif index and part.startswith("− "):
            rendered.append(part)
        elif index and part.startswith("MIN "):
            rendered.append(part)
        elif index and part.startswith("MAX "):
            rendered.append(part)
        elif index:
            rendered.append("+ " + part)
        else:
            rendered.append(part)
    return " ".join(part for part in rendered if part).strip()


def build_condition_source(version, gb_db, output_path, work_dir=None):
    relevant_sids, expected_sids = _expected_measure_sids(gb_db)
    if work_dir:
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="gb-condition-source-")
        root = Path(cleanup.name)
    try:
        table_data = {}
        source_tables = {}
        for table in TABLES:
            meta, rows = download_table(version, table, root / (table + ".csv"))
            source_tables[table] = meta
            table_data[table] = (meta["columns"], rows)

        lookups = {}
        for table in (
            "measures", "measure-duty-expressions", "measure-monetary-units", "measure-measurements-units",
            "measure-measurement-qualifiers", "measure-condition-codes", "measure-actions",
            "certificates", "certificate-types",
        ):
            headers, rows = table_data[table]
            lookups[table] = _lookup_table(table, rows, headers)

        # A measurement is a pair of unit + qualifier. Component and condition rows
        # reference this table by its trackedmodel_ptr_id in the normalized source.
        measurement_headers, measurement_rows = table_data["measure-measurements"]
        measurement_prefixes = PREFIXES["measure-measurements"]
        measurement_pk = _pick(measurement_headers,
            ["trackedmodel_ptr_id"] + [p + "__trackedmodel_ptr_id" for p in measurement_prefixes],
            required=False, label="measurements primary key")
        measurement_sid = _pick(measurement_headers, ["sid"] + [p + "__sid" for p in measurement_prefixes],
                                 required=False, label="measurements SID")
        measurement_unit_fk = _pick(measurement_headers,
            ["measurement_unit_id"] + [p + "__measurement_unit_id" for p in measurement_prefixes],
            required=False, label="measurement unit foreign key")
        measurement_qualifier_fk = _pick(measurement_headers,
            ["measurement_unit_qualifier_id"] + [p + "__measurement_unit_qualifier_id" for p in measurement_prefixes],
            required=False, label="measurement qualifier foreign key")
        measurement_unit_direct = _pick(measurement_headers,
            ["measurement_unit__code", "measurement_unit_code"], required=False, label="direct measurement unit code")
        measurement_qualifier_direct = _pick(measurement_headers,
            ["measurement_unit_qualifier__code", "measurement_unit_qualifier_code"],
            required=False, label="direct measurement qualifier code")
        measurement_map = {}
        for row in measurement_rows:
            unit = _ref(lookups["measure-measurements-units"], row.get(measurement_unit_fk) if measurement_unit_fk else "")
            qualifier = _ref(lookups["measure-measurement-qualifiers"], row.get(measurement_qualifier_fk) if measurement_qualifier_fk else "")
            item = {
                "measurement_unit_code": _clean(row.get(measurement_unit_direct)) if measurement_unit_direct else
                                         (unit.get("abbreviation") or unit.get("code") or unit.get("description") or ""),
                "measurement_unit_id": _clean(row.get(measurement_unit_fk)) if measurement_unit_fk else "",
                "measurement_unit_qualifier_code": _clean(row.get(measurement_qualifier_direct)) if measurement_qualifier_direct else
                                                  (qualifier.get("code") or qualifier.get("description") or ""),
                "measurement_unit_qualifier_id": _clean(row.get(measurement_qualifier_fk)) if measurement_qualifier_fk else "",
            }
            for key in (
                _clean(row.get(measurement_pk)) if measurement_pk else "",
                _clean(row.get(measurement_sid)) if measurement_sid else "",
            ):
                if key:
                    measurement_map[key] = item

        condition_headers, condition_rows = table_data["measure-conditions"]
        condition_prefixes = PREFIXES["measure-conditions"]
        c_pk = _pick(condition_headers, ["trackedmodel_ptr_id"] + [p + "__trackedmodel_ptr_id" for p in condition_prefixes],
                     required=False, label="condition primary key")
        c_sid = _pick(condition_headers, ["sid"] + [p + "__sid" for p in condition_prefixes],
                      required=False, label="condition SID")
        c_measure = _pick(condition_headers, ["measure_sid", "measure__sid"] +
                          [p + "__measure_sid" for p in condition_prefixes] +
                          [p + "__measure__sid" for p in condition_prefixes] +
                          ["dependent_measure_id"],
                          label="condition measure foreign key")
        # The published normalized condition table exposes dependent_measure_id,
        # which points to measures.trackedmodel_ptr_id. The measure's public
        # SID (used in the expanded export) is resolved through this relation.
        using_dependent_fk = c_measure == "dependent_measure_id"
        c_code_id = _pick(condition_headers, ["condition_code_id"] + [p + "__condition_code_id" for p in condition_prefixes],
                          required=False, label="condition code ID")
        c_code_direct = _pick(condition_headers, ["condition_code", "condition_code__code"] +
                              [p + "__condition_code__code" for p in condition_prefixes],
                              required=False, label="condition code")
        c_sequence = _pick(condition_headers, ["component_sequence_number"] +
                           [p + "__component_sequence_number" for p in condition_prefixes],
                           required=False, label="condition sequence")
        c_action_id = _pick(condition_headers, ["action_id"] + [p + "__action_id" for p in condition_prefixes],
                            required=False, label="condition action ID")
        c_action_direct = _pick(condition_headers, ["action_code", "action__code"] +
                                [p + "__action__code" for p in condition_prefixes],
                                required=False, label="condition action code")
        c_cert_id = _pick(condition_headers, ["certificate_id", "required_certificate_id"] + [p + "__certificate_id" for p in condition_prefixes],
                          required=False, label="condition certificate ID")
        c_cert_direct = _pick(condition_headers, ["certificate_code", "certificate__code"] +
                              [p + "__certificate__code" for p in condition_prefixes],
                              required=False, label="condition certificate code")
        c_cert_type_id = _pick(condition_headers, ["certificate_type_id"] + [p + "__certificate_type_id" for p in condition_prefixes],
                               required=False, label="condition certificate type ID")
        c_cert_type_direct = _pick(condition_headers, ["certificate_type_code", "certificate_type__code"] +
                                   [p + "__certificate_type__code" for p in condition_prefixes],
                                   required=False, label="condition certificate type code")
        c_amount = _pick(condition_headers, ["duty_amount"] + [p + "__duty_amount" for p in condition_prefixes],
                         required=False, label="condition trigger amount")
        c_money_id = _pick(condition_headers, ["monetary_unit_id"] + [p + "__monetary_unit_id" for p in condition_prefixes],
                           required=False, label="condition monetary unit ID")
        c_money_direct = _pick(condition_headers, ["monetary_unit__code", "monetary_unit_code"] +
                               [p + "__monetary_unit__code" for p in condition_prefixes],
                               required=False, label="condition monetary unit code")
        c_measurement_id = _pick(condition_headers, ["condition_measurement_id", "measurement_id"] +
                                 [p + "__condition_measurement_id" for p in condition_prefixes] +
                                 [p + "__measurement_id" for p in condition_prefixes],
                                 required=False, label="condition measurement ID")

        condition_by_key = {}
        relevant_conditions = []
        unresolved_references = []
        for row in condition_rows:
            measure_ref = _clean(row.get(c_measure))
            if using_dependent_fk:
                measure_sid = _ref(lookups["measures"], measure_ref).get("sid", "")
                if measure_ref and not measure_sid:
                    unresolved_references.append({"relationship":"dependent_measure_id","id":measure_ref})
            else:
                measure_sid = measure_ref
            if measure_sid not in relevant_sids:
                continue
            pk = _clean(row.get(c_pk)) if c_pk else ""
            sid = _clean(row.get(c_sid)) if c_sid else ""
            code_record = _ref(lookups["measure-condition-codes"], row.get(c_code_id) if c_code_id else "")
            action_record = _ref(lookups["measure-actions"], row.get(c_action_id) if c_action_id else "")
            cert_record = _ref(lookups["certificates"], row.get(c_cert_id) if c_cert_id else "")
            cert_type_record = _ref(lookups["certificate-types"], row.get(c_cert_type_id) if c_cert_type_id else "")
            direct_code = _clean(row.get(c_code_direct)) if c_code_direct else ""
            direct_action = _clean(row.get(c_action_direct)) if c_action_direct else ""
            direct_cert = _clean(row.get(c_cert_direct)) if c_cert_direct else ""
            direct_cert_type = _clean(row.get(c_cert_type_direct)) if c_cert_type_direct else ""
            measurement = _ref(measurement_map, row.get(c_measurement_id) if c_measurement_id else "")
            money = _ref(lookups["measure-monetary-units"], row.get(c_money_id) if c_money_id else "")
            condition = {
                "id": sid or pk,
                "trackedmodel_ptr_id": pk,
                "sid": sid,
                "measure_sid": measure_sid,
                "condition_code": direct_code or code_record.get("code", ""),
                "condition": code_record.get("description", ""),
                "action_code": direct_action or action_record.get("code", ""),
                "action": action_record.get("description", ""),
                "certificate_code": direct_cert or cert_record.get("code", ""),
                "certificate_description": cert_record.get("description", ""),
                "certificate_type_code": direct_cert_type or cert_type_record.get("code", ""),
                "certificate_type_description": cert_type_record.get("description", ""),
                "condition_duty_amount": _clean(row.get(c_amount)) if c_amount else "",
                "condition_monetary_unit_code": _clean(row.get(c_money_direct)) if c_money_direct else
                                                (money.get("code") or money.get("abbreviation") or ""),
                "condition_measurement_unit_code": measurement.get("measurement_unit_code", ""),
                "condition_measurement_unit_qualifier_code": measurement.get("measurement_unit_qualifier_code", ""),
                "component_sequence_number": _clean(row.get(c_sequence)) if c_sequence else "",
                "components": [],
                "unresolved_relationships": [],
            }
            if c_code_id and _clean(row.get(c_code_id)) and not (direct_code or code_record.get("code")):
                condition["unresolved_relationships"].append({"relationship":"condition_code","id":_clean(row.get(c_code_id))})
            if c_action_id and _clean(row.get(c_action_id)) and not (direct_action or action_record.get("code")):
                condition["unresolved_relationships"].append({"relationship":"action","id":_clean(row.get(c_action_id))})
            if c_cert_id and _clean(row.get(c_cert_id)) and not (direct_cert or cert_record.get("code")):
                condition["unresolved_relationships"].append({"relationship":"certificate","id":_clean(row.get(c_cert_id))})
            for key in (pk, sid):
                if key:
                    condition_by_key[key] = condition
            relevant_conditions.append(condition)

        component_headers, component_rows = table_data["measure-condition-components"]
        component_prefixes = PREFIXES["measure-condition-components"]
        p_condition_id = _pick(component_headers,
            ["condition_id", "measure_condition_id"] +
            [p + "__condition_id" for p in component_prefixes] +
            [p + "__measure_condition_id" for p in component_prefixes] +
            ["measure_condition__trackedmodel_ptr_id", "measure_condition__sid"],
            label="condition component parent ID")
        p_expression_id = _pick(component_headers,
            ["duty_expression_id", "duty_expression__sid"] +
            [p + "__duty_expression_id" for p in component_prefixes] +
            [p + "__duty_expression__sid" for p in component_prefixes],
            label="condition component duty expression ID")
        p_amount = _pick(component_headers, ["duty_amount"] + [p + "__duty_amount" for p in component_prefixes],
                         required=False, label="condition component duty amount")
        p_money_id = _pick(component_headers, ["monetary_unit_id"] + [p + "__monetary_unit_id" for p in component_prefixes],
                           required=False, label="component monetary unit ID")
        p_money_direct = _pick(component_headers, ["monetary_unit__code", "monetary_unit_code"] +
                               [p + "__monetary_unit__code" for p in component_prefixes],
                               required=False, label="component monetary unit code")
        p_measurement_id = _pick(component_headers, ["measurement_id", "component_measurement_id"] + [p + "__measurement_id" for p in component_prefixes],
                                 required=False, label="component measurement ID")
        p_unit_id = _pick(component_headers, ["measurement_unit_id"] + [p + "__measurement_unit_id" for p in component_prefixes],
                          required=False, label="component measurement unit ID")
        p_qualifier_id = _pick(component_headers, ["measurement_unit_qualifier_id"] +
                               [p + "__measurement_unit_qualifier_id" for p in component_prefixes],
                               required=False, label="component measurement qualifier ID")
        p_unit_direct = _pick(component_headers, ["measurement_unit__code", "measurement_unit_code"] +
                              [p + "__measurement_unit__code" for p in component_prefixes],
                              required=False, label="component measurement unit code")
        p_qualifier_direct = _pick(component_headers, ["measurement_unit_qualifier__code", "measurement_unit_qualifier_code"] +
                                   [p + "__measurement_unit_qualifier__code" for p in component_prefixes],
                                   required=False, label="component measurement qualifier code")

        relevant_component_rows = 0
        orphan_component_rows = 0
        for row in component_rows:
            condition_id = _clean(row.get(p_condition_id))
            condition = condition_by_key.get(condition_id)
            if condition is None:
                orphan_component_rows += 1
                continue
            relevant_component_rows += 1
            duty_ref = _clean(row.get(p_expression_id))
            duty_record = _ref(lookups["measure-duty-expressions"], duty_ref)
            duty_id = duty_record.get("sid") or duty_ref
            money = _ref(lookups["measure-monetary-units"], row.get(p_money_id) if p_money_id else "")
            measurement = _ref(measurement_map, row.get(p_measurement_id) if p_measurement_id else "")
            unit = _ref(lookups["measure-measurements-units"], row.get(p_unit_id) if p_unit_id else "")
            qualifier = _ref(lookups["measure-measurement-qualifiers"], row.get(p_qualifier_id) if p_qualifier_id else "")
            component = {
                "duty_expression_id": duty_id,
                "duty_expression_description": duty_record.get("description", ""),
                "duty_amount": _clean(row.get(p_amount)) if p_amount else "",
                "monetary_unit_id": _clean(row.get(p_money_id)) if p_money_id else "",
                "monetary_unit_code": _clean(row.get(p_money_direct)) if p_money_direct else
                                      (money.get("code") or money.get("abbreviation") or ""),
                "measurement_id": _clean(row.get(p_measurement_id)) if p_measurement_id else "",
                "measurement_unit_code": _clean(row.get(p_unit_direct)) if p_unit_direct else
                                         (measurement.get("measurement_unit_code") or unit.get("abbreviation") or unit.get("code") or unit.get("description") or ""),
                "measurement_unit_qualifier_code": _clean(row.get(p_qualifier_direct)) if p_qualifier_direct else
                                                   (measurement.get("measurement_unit_qualifier_code") or qualifier.get("code") or qualifier.get("description") or ""),
            }
            if duty_ref and not duty_record:
                condition["unresolved_relationships"].append({"relationship":"duty_expression","id":duty_ref})
            if p_money_id and _clean(row.get(p_money_id)) and not (component["monetary_unit_code"]):
                condition["unresolved_relationships"].append({"relationship":"monetary_unit","id":_clean(row.get(p_money_id))})
            if (p_measurement_id and _clean(row.get(p_measurement_id)) and not component["measurement_unit_code"]) or (
                p_unit_id and _clean(row.get(p_unit_id)) and not component["measurement_unit_code"]
            ):
                condition["unresolved_relationships"].append({"relationship":"measurement_unit","id":_clean(row.get(p_measurement_id) or row.get(p_unit_id))})
            condition["components"].append(component)

        rates_by_measure = {}
        for condition in relevant_conditions:
            condition["components"].sort(key=lambda item: (
                int(item["duty_expression_id"]) if str(item.get("duty_expression_id", "")).isdigit() else 9999,
                str(item.get("duty_expression_id", "")),
            ))
            condition["duty_expression"] = _format_expression(condition["components"])
            condition["resolved"] = bool(condition["components"]) and not condition["unresolved_relationships"]
            condition["group_ids"] = [condition["condition_code"]] if condition["condition_code"] else []
            rates_by_measure.setdefault(condition["measure_sid"], []).append(condition)
        for measure_sid in rates_by_measure:
            rates_by_measure[measure_sid].sort(key=lambda item: (
                item.get("condition_code", ""),
                int(item["component_sequence_number"]) if str(item.get("component_sequence_number", "")).isdigit() else 9999,
                item.get("id", ""),
            ))

        found_expected = {sid for sid in rates_by_measure if sid in expected_sids}
        missing_expected = sorted(expected_sids - found_expected)
        condition_rows_without_components = sum(not condition["components"] for condition in relevant_conditions)
        unresolved_condition_links = sum(len(condition["unresolved_relationships"]) for condition in relevant_conditions)
        source = {
            "kind": "gb-condition-components-v1",
            "version": version,
            "source_tables": source_tables,
            "coverage": {
                "relevant_remedy_measure_sids": len(relevant_sids),
                "expected_measure_sids_with_conditions": len(expected_sids),
                "found_expected_measure_sids": len(found_expected),
                "missing_expected_measure_sids": missing_expected,
                "condition_rows_total": len(condition_rows),
                "relevant_condition_rows": len(relevant_conditions),
                "condition_component_rows_total": len(component_rows),
                "relevant_condition_component_rows": relevant_component_rows,
                "orphan_component_rows": orphan_component_rows,
                "condition_rows_without_components": condition_rows_without_components,
                "unresolved_condition_relationships": unresolved_condition_links,
                "condition_rows_with_rate_expression": sum(bool(c["duty_expression"]) for c in relevant_conditions),
            },
            "condition_rates_by_measure_sid": rates_by_measure,
        }
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"version":version,"source_tables":{k:{"sha256":v["sha256"],"bytes":v["bytes"],"row_count":v["row_count"]} for k,v in source_tables.items()},
                          "coverage":source["coverage"]},ensure_ascii=False,indent=2))
        if missing_expected:
            raise ValueError("GB condition source is missing expected remedy measure SIDs: " + ", ".join(missing_expected[:100]))
        return source
    finally:
        if cleanup:
            cleanup.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--gb-db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--work-dir")
    args = parser.parse_args()
    build_condition_source(args.version, args.gb_db, args.output, args.work_dir)


if __name__ == "__main__":
    main()
