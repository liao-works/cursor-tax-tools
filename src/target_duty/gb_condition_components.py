"""Same-version official GB condition-component join with revision disambiguation.

The expanded measures export carries condition codes and certificates but not
the duty amounts attached to each condition. The normalized tables
(``measure-conditions`` / ``measure-condition-components`` and their lookups)
carry the amounts, but a public measure SID can hold several tracked-model
revisions: later revisions of the same condition often repeat the structure
with the duty amounts removed. Selecting "the newest revision" therefore
returns blanks, while selecting any amount-bearing revision blindly can revive
superseded rates.

Rule implemented here (validated against the live GOV.UK commodity pages for
measures 20272576, 20272621 and 20041156 on 2026-10-17): within one condition
lineage (same measure SID + condition SID), choose the highest-transaction
revision whose components carry a non-empty duty amount; a later revision that
dropped the amounts never erases the officially displayed rate, and the choice
is recorded with ``amount_from_older_revision`` for audit. Conditions with no
amount-bearing revision at all stay blank and are counted as unresolved.

Certificate/action signatures of the chosen conditions are cross-checked
against the ``measure__conditions`` text of the expanded export; mismatches are
flagged per measure and counted so the release gate can fail closed.
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
from collections import defaultdict
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
    "common-tracked-models",
    "common-transactions",
)
# Normalized lookups expose both bare and relation-prefixed column names.
PREFIXES = {
    "measures": ("measure", "measures"),
    "measure-conditions": ("measure_condition", "measure_conditions"),
    "measure-condition-components": ("measure_condition_component", "measure_condition_components"),
    "measure-duty-expressions": ("measure_duty_expression", "measure_duty_expressions"),
    "measure-monetary-units": ("measure_monetary_unit", "measure_monetary_units"),
    "measure-measurements": ("measure_measurement", "measure_measurements"),
    "measure-measurements-units": ("measure_measurement_unit", "measure_measurements_units"),
    "measure-measurement-qualifiers": ("measure_measurement_qualifier", "measure_measurement_qualifiers"),
    "measure-condition-codes": ("measure_condition_code", "measure_condition_codes"),
    "measure-actions": ("measure_action", "measure_actions"),
    "certificates": ("certificate", "certificates"),
    "certificate-types": ("certificate_type", "certificate_types"),
}
BLANK = {"", "#NA", "N/A", "null", "None", "NULL"}
DELETE_UPDATE_TYPES = {"2"}


def _clean(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value in BLANK else value


def _pick(headers, candidates, *, required=True, label="field"):
    lowered = {h.lower(): h for h in headers}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
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


def _csv_header_and_count(path):
    """Header plus a memory-light data row count (the lineage tables are huge)."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        headers = next(reader, None) or []
        if not headers:
            raise ValueError(f"Empty CSV header in {path}")
        rows = sum(1 for _ in reader)
    return headers, rows


def download_table(version, table, destination):
    """Download one pinned table and return immutable source evidence."""
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
            headers, row_count = _csv_header_and_count(destination)
            return {
                "table": table, "version": version, "url": url, "final_url": final_url,
                "sha256": digest.hexdigest(), "bytes": size, "row_count": row_count,
                "columns": headers,
            }, rows
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not download pinned GB condition table {table} at {version}: {last_error}")


def _expected_measure_sids(gb_db):
    """Remedy measure SIDs, and per-SID (certificate, action) signatures from the expanded export."""
    expected = set()
    relevant = set()
    signatures = {}
    conn = sqlite3.connect(gb_db)
    try:
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
                signature = tuple(sorted(_condition_signature(conditions)))
                signatures.setdefault(sid, set()).add(signature)
    finally:
        conn.close()
    if not relevant:
        raise ValueError("GB source snapshot contains no 551/552/553/554 measure SIDs")
    conflicts = {sid: sigs for sid, sigs in signatures.items() if len(sigs) > 1}
    if conflicts:
        raise ValueError(f"GB expanded export carries conflicting condition signatures: {list(conflicts)[:5]}")
    return relevant, expected, {sid: next(iter(sigs)) for sid, sigs in signatures.items()}


def _condition_signature(conditions_text):
    """Parse 'condition:A,certificate:D008,action:01|...' into (certificate, action) pairs."""
    pairs = []
    for segment in str(conditions_text or "").split("|"):
        fields = {}
        for part in segment.split(","):
            key, _, value = part.partition(":")
            fields[key.strip()] = value.strip()
        pairs.append((fields.get("certificate", ""), fields.get("action", "")))
    return [pair for pair in pairs if any(pair)]


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
            "certificate_type_id": _clean(row.get("certificate_type_id")) if "certificate_type_id" in row else "",
        }
        for key in (item["id"], item["sid"]):
            if key:
                lookup[key] = item
    return lookup


def _ref(lookup, key):
    return lookup.get(_clean(key), {}) if _clean(key) else {}


def _format_component(component):
    amount = _clean(component.get("duty_amount"))
    expression_id = _clean(component.get("duty_expression_id"))
    description = _clean(component.get("duty_expression_description"))
    # The operator description is not a taxable amount: a blank source amount
    # must not turn into a displayable rate such as "% or amount".
    if not amount:
        return ""
    money = _clean(component.get("monetary_unit_code"))
    unit = _clean(component.get("measurement_unit_code"))
    qualifier = _clean(component.get("measurement_unit_qualifier_code"))
    unit_label = ((qualifier + " ") if qualifier else "") + unit
    if money and unit_label:
        base = amount + " " + money + " / " + unit_label
    elif money:
        base = amount + " " + money
    elif unit_label:
        base = amount + " " + unit_label
    elif amount and expression_id in ("1", "4"):
        base = amount + " %"
    elif amount:
        base = amount + " [" + (description or ("表达式 " + expression_id)) + "]"
    else:
        return ""
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
        if not part:
            return ""  # A partial compound charge cannot be a verified expression.
        if index and not part.startswith(("+ ", "− ", "MIN ", "MAX ")):
            part = "+ " + part
        rendered.append(part)
    return " ".join(rendered).strip()


def build_condition_source(version, gb_db, output_path, work_dir=None):
    relevant_sids, expected_sids, expected_signatures = _expected_measure_sids(gb_db)
    if work_dir:
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="gb-condition-source-")
        root = Path(cleanup.name)
    try:
        source_tables = {}
        for table in TABLES:
            meta, _rows = download_table(version, table, root / (table + ".csv"))
            source_tables[table] = meta

        # --- Pass 1: measure revisions for the relevant SIDs (streamed). ---
        measures_path = root / "measures.csv"
        with measures_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            m_pk = _pick(reader.fieldnames or [], ["trackedmodel_ptr_id", "measure__trackedmodel_ptr_id"],
                         label="measures primary key")
            m_sid = _pick(reader.fieldnames or [], ["sid", "measure__sid"], label="measures sid")
            measure_sid_by_rev = {}
            for row in reader:
                sid = _clean(row.get(m_sid))
                if sid in relevant_sids:
                    measure_sid_by_rev[_clean(row.get(m_pk))] = sid
        if not measure_sid_by_rev:
            raise ValueError("Normalized measures table has no revision for the snapshot remedy SIDs")

        # --- Pass 2: condition revisions attached to those measure revisions. ---
        conditions_path = root / "measure-conditions.csv"
        with conditions_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            c_pk = _pick(headers, ["trackedmodel_ptr_id"] + [p + "__trackedmodel_ptr_id" for p in PREFIXES["measure-conditions"]],
                         required=False, label="condition primary key")
            c_sid = _pick(headers, ["sid"] + [p + "__sid" for p in PREFIXES["measure-conditions"]],
                          required=False, label="condition sid")
            c_measure = _pick(headers, ["dependent_measure_id", "measure_sid", "measure__sid"] +
                              [p + "__measure_sid" for p in PREFIXES["measure-conditions"]] +
                              [p + "__dependent_measure_id" for p in PREFIXES["measure-conditions"]],
                              label="condition measure foreign key")
            c_code_id = _pick(headers, ["condition_code_id"] + [p + "__condition_code_id" for p in PREFIXES["measure-conditions"]],
                              required=False, label="condition code ID")
            c_code_direct = _pick(headers, ["condition_code", "condition_code__code"] +
                                  [p + "__condition_code__code" for p in PREFIXES["measure-conditions"]],
                                  required=False, label="condition code")
            c_sequence = _pick(headers, ["component_sequence_number"] +
                               [p + "__component_sequence_number" for p in PREFIXES["measure-conditions"]],
                               required=False, label="condition sequence")
            c_action_id = _pick(headers, ["action_id"] + [p + "__action_id" for p in PREFIXES["measure-conditions"]],
                                required=False, label="condition action ID")
            c_action_direct = _pick(headers, ["action_code", "action__code"] +
                                    [p + "__action__code" for p in PREFIXES["measure-conditions"]],
                                    required=False, label="condition action code")
            c_cert_id = _pick(headers, ["required_certificate_id", "certificate_id"] +
                              [p + "__required_certificate_id" for p in PREFIXES["measure-conditions"]] +
                              [p + "__certificate_id" for p in PREFIXES["measure-conditions"]],
                              required=False, label="condition certificate ID")
            c_amount = _pick(headers, ["duty_amount"] + [p + "__duty_amount" for p in PREFIXES["measure-conditions"]],
                             required=False, label="condition trigger amount")
            condition_revisions = {}
            for row in reader:
                dep = _clean(row.get(c_measure))
                if dep not in measure_sid_by_rev:
                    continue
                pk = _clean(row.get(c_pk)) if c_pk else ""
                if not pk:
                    continue
                condition_revisions[pk] = {
                    "id": pk,
                    "sid": _clean(row.get(c_sid)) if c_sid else "",
                    "measure_rev": dep,
                    "condition_code_id": _clean(row.get(c_code_id)) if c_code_id else "",
                    "condition_code_direct": _clean(row.get(c_code_direct)) if c_code_direct else "",
                    "action_id": _clean(row.get(c_action_id)) if c_action_id else "",
                    "action_direct": _clean(row.get(c_action_direct)) if c_action_direct else "",
                    "certificate_id": _clean(row.get(c_cert_id)) if c_cert_id else "",
                    "sequence": _clean(row.get(c_sequence)) if c_sequence else "",
                    "trigger_amount": _clean(row.get(c_amount)) if c_amount else "",
                }
        if not condition_revisions:
            raise ValueError("Normalized measure-conditions has no rows for the snapshot remedy measures")

        # --- Pass 3: components attached to those condition revisions. ---
        components_path = root / "measure-condition-components.csv"
        with components_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            p_condition = _pick(headers, ["condition_id", "measure_condition_id"] +
                                [p + "__condition_id" for p in PREFIXES["measure-condition-components"]] +
                                [p + "__measure_condition_id" for p in PREFIXES["measure-condition-components"]],
                                label="condition component parent ID")
            p_expression = _pick(headers, ["duty_expression_id", "duty_expression__sid"] +
                                 [p + "__duty_expression_id" for p in PREFIXES["measure-condition-components"]] +
                                 [p + "__duty_expression__sid" for p in PREFIXES["measure-condition-components"]],
                                 label="condition component duty expression ID")
            p_amount = _pick(headers, ["duty_amount"] + [p + "__duty_amount" for p in PREFIXES["measure-condition-components"]],
                             required=False, label="condition component duty amount")
            p_money_id = _pick(headers, ["monetary_unit_id"] + [p + "__monetary_unit_id" for p in PREFIXES["measure-condition-components"]],
                               required=False, label="component monetary unit ID")
            p_money_direct = _pick(headers, ["monetary_unit__code", "monetary_unit_code"] +
                                   [p + "__monetary_unit__code" for p in PREFIXES["measure-condition-components"]],
                                   required=False, label="component monetary unit code")
            p_unit_id = _pick(headers, ["measurement_unit_id"] + [p + "__measurement_unit_id" for p in PREFIXES["measure-condition-components"]],
                              required=False, label="component measurement unit ID")
            p_qualifier_id = _pick(headers, ["measurement_unit_qualifier_id"] +
                                   [p + "__measurement_unit_qualifier_id" for p in PREFIXES["measure-condition-components"]],
                                   required=False, label="component measurement qualifier ID")
            p_unit_direct = _pick(headers, ["measurement_unit__code", "measurement_unit_code"] +
                                  [p + "__measurement_unit__code" for p in PREFIXES["measure-condition-components"]],
                                  required=False, label="component measurement unit code")
            p_qualifier_direct = _pick(headers, ["measurement_unit_qualifier__code", "measurement_unit_qualifier_code"] +
                                       [p + "__measurement_unit_qualifier__code" for p in PREFIXES["measure-condition-components"]],
                                       required=False, label="component measurement qualifier code")
            components_by_condition = defaultdict(list)
            orphan_components = 0
            for row in reader:
                cid = _clean(row.get(p_condition))
                if cid not in condition_revisions:
                    orphan_components += 1
                    continue
                money = _clean(row.get(p_money_direct)) if p_money_direct else ""
                unit = _clean(row.get(p_unit_direct)) if p_unit_direct else ""
                qualifier = _clean(row.get(p_qualifier_direct)) if p_qualifier_direct else ""
                components_by_condition[cid].append({
                    "duty_expression_id": _clean(row.get(p_expression)),
                    "duty_amount": _clean(row.get(p_amount)) if p_amount else "",
                    "monetary_unit_id": _clean(row.get(p_money_id)) if p_money_id else "",
                    "monetary_unit_code": money,
                    "measurement_unit_code": unit,
                    "measurement_unit_qualifier_code": qualifier,
                })

        # --- Pass 4: transaction lineage for the condition revisions. ---
        tracked_path = root / "common-tracked-models.csv"
        with tracked_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            t_id = _pick(reader.fieldnames or [], ["id"], label="tracked model id")
            t_tx = _pick(reader.fieldnames or [], ["transaction_id"], label="tracked model transaction")
            t_type = _pick(reader.fieldnames or [], ["update_type"], required=False, label="tracked model update type")
            tx_of = {}
            update_type_of = {}
            for row in reader:
                rid = _clean(row.get(t_id))
                if rid in condition_revisions:
                    tx_of[rid] = _clean(row.get(t_tx))
                    update_type_of[rid] = _clean(row.get(t_type))
        missing_lineage = sorted(set(condition_revisions) - set(tx_of))
        if missing_lineage:
            raise ValueError(f"Tracked-model lineage missing for {len(missing_lineage)} condition revisions")

        transactions_path = root / "common-transactions.csv"
        with transactions_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            x_id = _pick(reader.fieldnames or [], ["id"], label="transaction id")
            x_order = _pick(reader.fieldnames or [], ["order"], label="transaction order")
            needed = set(tx_of.values())
            order_of = {}
            for row in reader:
                rid = _clean(row.get(x_id))
                if rid in needed:
                    order_of[rid] = int(_clean(row.get(x_order)) or -1)
        missing_tx = {tx for tx in needed if tx not in order_of}
        if missing_tx:
            raise ValueError(f"Transaction order missing for {len(missing_tx)} condition transactions")

        # --- Lookups for condition code / action / certificate rendering. ---
        lookup_tables = (
            "measure-duty-expressions", "measure-monetary-units", "measure-measurements-units",
            "measure-measurement-qualifiers", "measure-condition-codes", "measure-actions",
            "certificates", "certificate-types",
        )
        lookups = {}
        for table in lookup_tables:
            headers, rows = _read_csv(root / (table + ".csv"))
            lookups[table] = _lookup_table(table, rows, headers)
        certificates = lookups["certificates"]
        certificate_types = lookups["certificate-types"]

        def certificate_code(ref):
            cert = certificates.get(_clean(ref))
            if not cert:
                return ""
            cert_type = certificate_types.get(cert.get("certificate_type_id", ""))
            if not cert_type:
                return ""
            return cert_type.get("sid", "") + cert.get("sid", "")

        # --- Selection: highest-transaction amount-bearing revision per lineage. ---
        lineage = defaultdict(list)
        for pk, rev in condition_revisions.items():
            msid = measure_sid_by_rev[rev["measure_rev"]]
            lineage[(msid, rev["sid"] or pk)].append(pk)

        def has_amount(pk):
            return any(_clean(c["duty_amount"]) for c in components_by_condition.get(pk, ()))

        rates_by_measure = defaultdict(list)
        coverage = {
            "relevant_remedy_measure_sids": len(relevant_sids),
            "expected_measure_sids_with_conditions": len(expected_sids),
            "condition_entities": 0,
            "condition_entities_with_amount": 0,
            "condition_entities_without_amount": 0,
            "amount_from_older_revision": 0,
            "condition_rows_streamed": len(condition_revisions),
            "component_rows_orphaned": orphan_components,
            "duplicate_component_expression_conflicts": 0,
            "signature_mismatch_measure_sids": 0,
            "missing_expected_measure_sids": [],
            "unresolved_condition_relationships": 0,
            "condition_rows_with_rate_expression": 0,
        }
        signature_mismatches = []
        for (msid, csid), revisions in lineage.items():
            coverage["condition_entities"] += 1
            live = [pk for pk in revisions if _clean(update_type_of.get(pk)) not in DELETE_UPDATE_TYPES]
            ordered = sorted(live, key=lambda pk: order_of[tx_of[pk]], reverse=True)
            amount_bearing = [pk for pk in ordered if has_amount(pk)]
            chosen = (amount_bearing or ordered)[0]
            latest = ordered[0] if ordered else chosen
            if amount_bearing:
                coverage["condition_entities_with_amount"] += 1
                if chosen != latest:
                    coverage["amount_from_older_revision"] += 1
            else:
                coverage["condition_entities_without_amount"] += 1
            rev = condition_revisions[chosen]
            components = components_by_condition.get(chosen, [])
            # Conflicting duplicate amounts for the same duty expression are not resolvable.
            seen_expr = {}
            conflict = False
            for component in components:
                key = component["duty_expression_id"]
                if key in seen_expr and seen_expr[key] != component["duty_amount"]:
                    conflict = True
                seen_expr[key] = component["duty_amount"]
            if conflict:
                coverage["duplicate_component_expression_conflicts"] += 1
            unresolved = []
            code_record = _ref(lookups["measure-condition-codes"], rev["condition_code_id"])
            action_record = _ref(lookups["measure-actions"], rev["action_id"])
            condition_code = rev["condition_code_direct"] or code_record.get("code", "")
            action_code = rev["action_direct"] or action_record.get("code", "")
            cert_ref = rev["certificate_id"]
            cert = certificate_code(cert_ref) if cert_ref else ""
            if rev["condition_code_id"] and not condition_code:
                unresolved.append({"relationship": "condition_code", "id": rev["condition_code_id"]})
            if rev["action_id"] and not action_code:
                unresolved.append({"relationship": "action", "id": rev["action_id"]})
            if cert_ref and not cert:
                unresolved.append({"relationship": "certificate", "id": cert_ref})
            resolved_components = []
            for component in components:
                item = dict(component)
                expr_ref = component["duty_expression_id"]
                expr_record = _ref(lookups["measure-duty-expressions"], expr_ref)
                item["duty_expression_id"] = expr_record.get("sid") or expr_ref
                item["duty_expression_description"] = expr_record.get("description", "")
                if not item["monetary_unit_code"]:
                    money = _ref(lookups["measure-monetary-units"], component.get("monetary_unit_id"))
                    item["monetary_unit_code"] = money.get("code") or money.get("abbreviation") or ""
                if expr_ref and not expr_record:
                    unresolved.append({"relationship": "duty_expression", "id": expr_ref})
                resolved_components.append(item)
            expression = "" if conflict else _format_expression(resolved_components)
            if expression:
                coverage["condition_rows_with_rate_expression"] += 1
            entry = {
                "condition_id": rev["sid"] or chosen,
                "condition_trackedmodel_id": chosen,
                "condition_code": condition_code,
                "condition": code_record.get("description", ""),
                "action_code": action_code,
                "action": action_record.get("description", ""),
                "certificate_code": cert,
                "certificate_description": certificates.get(_clean(cert_ref), {}).get("description", ""),
                "requirement": (certificates.get(_clean(cert_ref), {}).get("description", "")
                                if cert_ref else "No document provided"),
                "component_sequence_number": rev["sequence"],
                "duty_expression": expression,
                "components": resolved_components,
                "resolved": bool(expression) and not unresolved and not conflict,
                "amount_from_older_revision": bool(amount_bearing) and chosen != latest,
                "latest_revision_has_amount": has_amount(latest),
                "chosen_transaction_order": order_of[tx_of[chosen]],
                "latest_transaction_order": order_of[tx_of[latest]],
                "unresolved_relationships": unresolved,
            }
            coverage["unresolved_condition_relationships"] += len(unresolved)
            rates_by_measure[msid].append(entry)

        # Certificate/action signature parity against the expanded export text.
        for msid, entries in rates_by_measure.items():
            expected_signature = expected_signatures.get(msid)
            if expected_signature is None:
                continue
            actual = tuple(sorted((e["certificate_code"], e["action_code"]) for e in entries
                                  if e["certificate_code"] or e["action_code"]))
            if actual != expected_signature:
                coverage["signature_mismatch_measure_sids"] += 1
                if len(signature_mismatches) < 20:
                    signature_mismatches.append({
                        "measure_sid": msid,
                        "expanded_signature": list(expected_signature),
                        "join_signature": list(actual),
                    })
                for entry in rates_by_measure[msid]:
                    entry["signature_mismatch"] = True

        found_expected = {sid for sid in rates_by_measure if sid in expected_sids}
        coverage["missing_expected_measure_sids"] = sorted(expected_sids - found_expected)
        coverage["condition_signature_mismatches"] = signature_mismatches
        source = {
            "kind": "gb-condition-components-v2",
            "version": version,
            "source_tables": source_tables,
            "coverage": coverage,
            "condition_rates_by_measure_sid": {
                sid: sorted(entries, key=lambda e: (
                    e["certificate_code"], e["condition_code"],
                    int(e["component_sequence_number"]) if str(e["component_sequence_number"]).isdigit() else 9999,
                    e["condition_id"],
                ))
                for sid, entries in rates_by_measure.items()
            },
        }
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "version": version,
            "source_tables": {k: {"sha256": v["sha256"], "bytes": v["bytes"], "row_count": v["row_count"]}
                              for k, v in source_tables.items()},
            "coverage": coverage,
        }, ensure_ascii=False, indent=2))
        if coverage["missing_expected_measure_sids"]:
            raise ValueError("GB condition source is missing expected remedy measure SIDs: "
                             + ", ".join(coverage["missing_expected_measure_sids"][:100]))
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
