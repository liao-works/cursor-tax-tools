"""Pinned official DBT geographical-area memberships for China applicability.

DBT's current membership table stores geo_group_id/member_id as numeric foreign
keys. Resolve them against the same-version geo-areas table before identifying
which official groups contain China. Keep orphaned active links in the audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

BASE = ("https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/"
        "versions/{version}/tables/geo-area-memberships/data?format=csv")
VERSION = re.compile(r"v[0-9]+(?:\.[0-9]+)*\Z")
NULLS = {"", "#NA", "N/A", "NA", "None", "null", "NULL", "[]"}


def source_url(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("Geographical membership download requires immutable DBT version")
    return BASE.format(version=version)


def _clean(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result in NULLS else result


def _choose(fields: list[str], candidates: tuple[str, ...], predicate) -> str:
    for name in candidates:
        if name in fields:
            return name
    matches = [name for name in fields if predicate(name.lower())]
    if len(matches) != 1:
        raise ValueError("Cannot unambiguously identify official geographical column: "
                         + repr(matches or fields))
    return matches[0]


def _parse_csv(payload: bytes, label: str):
    if not payload or b"\\x00" in payload:
        raise ValueError("Empty or binary official " + label + " CSV")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    fields = list(reader.fieldnames or [])
    if not fields or len(fields) != len(set(fields)):
        raise ValueError("Missing or duplicate official " + label + " CSV headers")
    rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("Malformed official " + label + " CSV row")
    return fields, rows


def _membership_columns(fields: list[str]) -> dict[str, str]:
    group = _choose(fields,
        ("geographical_area__id", "geo_area__id", "group__id",
         "geographical_area_group__id", "geo_area_group__id", "geo_group_id"),
        lambda n: (("geographical_area" in n or "geo_area" in n or "group" in n)
                   and "id" in n and "member" not in n and "parent" not in n
                   and "excluded" not in n and "sid" not in n))
    member = _choose(fields,
        ("member__id", "member_geographical_area__id",
         "member__geographical_area__id", "geographical_area_member__id",
         "geo_area_member__id", "member_id"),
        lambda n: "member" in n and "id" in n and "sid" not in n)
    start = _choose(fields,
        ("membership__effective_start_date", "membership__validity_start_date",
         "membership__start_date", "geo_area_membership__start_date",
         "membership_starts", "starts", "effective_start_date", "start_date",
         "validity_start"),
        lambda n: "start" in n and (
                  ("date" in n and ("membership" in n or "member" in n))
                  or n == "validity_start"))
    end = _choose(fields,
        ("membership__effective_end_date", "membership__validity_end_date",
         "membership__end_date", "geo_area_membership__end_date",
         "membership_ends", "ends", "effective_end_date", "end_date",
         "validity_end"),
        lambda n: "end" in n and (
                  ("date" in n and ("membership" in n or "member" in n))
                  or n == "validity_end"))
    return {"group": group, "member": member, "start": start, "end": end}


def _geo_area_map(payload: bytes) -> tuple[dict[str, str], dict]:
    fields, rows = _parse_csv(payload, "geo-areas")
    id_col = _choose(fields, ("trackedmodel_ptr_id", "geo_area_ptr_id", "geo_area_pk", "id"),
                     lambda n: n in ("trackedmodel_ptr_id", "geo_area_ptr_id", "geo_area_pk", "id"))
    candidates = [name for name in (
        "area_id", "geo_area__id", "geographical_area__id", "geo_area_id",
        "geographical_area_id", "area_code", "code")
        if name in fields and name != id_col]
    mapping: dict[str, str] = {}
    selected = ""
    diagnostics = []
    for code_col in candidates:
        trial = {}
        conflict = False
        for row in rows:
            key = _clean(row.get(id_col))
            value = _clean(row.get(code_col)).upper()
            if not key or not value:
                continue
            if key in trial and trial[key] != value:
                conflict = True
                break
            trial[key] = value
        values = set(trial.values())
        diagnostics.append({"column":code_col,"rows":len(trial),
                            "has_CN":"CN" in values,"has_1011":"1011" in values,
                            "conflict":conflict})
        if not conflict and len(trial) >= 50 and "CN" in values and "1011" in values:
            selected, mapping = code_col, trial
            break
    if not selected:
        raise ValueError("Cannot resolve DBT geo-area foreign keys to area codes: "
                         + json.dumps({"headers":fields,"id_column":id_col,
                                       "candidates":diagnostics},ensure_ascii=False))
    return mapping, {
        "source_columns":{"id":id_col,"code":selected},
        "source_header":fields,
        "row_count":len(rows),
        "mapped_area_count":len(mapping),
        "source_sha256":hashlib.sha256(payload).hexdigest(),
    }


def _description_report(payload: bytes, as_of: date) -> dict:
    fields, rows = _parse_csv(payload, "geo-area-descriptions")
    id_col = _choose(fields,
        ("described_geographicalarea_id","described_geo_area_id","geo_area_id"),
        lambda n: "described" in n and "id" in n)
    desc_col = _choose(fields,("description","geo_area_description","geographical_area_description"),
                       lambda n: "description" in n)
    start_col = _choose(fields,("validity_start","effective_start_date","start_date"),
                        lambda n: "start" in n and "date" in n or n=="validity_start")
    active = 0
    missing_descriptions = 0
    for row in rows:
        start = _clean(row.get(start_col))
        if not start:
            raise ValueError("Official geo-area description missing validity start")
        try:
            if date.fromisoformat(start[:10]) <= as_of:
                active += 1
        except ValueError as error:
            raise ValueError("Invalid official geo-area description validity date") from error
        if not _clean(row.get(id_col)):
            raise ValueError("Official geo-area description missing area ID")
        # DBT legitimately emits #NA descriptions for some area records. They
        # do not affect numeric FK resolution; count rather than reject them.
        if not _clean(row.get(desc_col)):
            missing_descriptions += 1
    return {
        "source_columns":{"area_id":id_col,"description":desc_col,"start":start_col},
        "source_header":fields,
        "row_count":len(rows),
        "active_description_rows":active,
        "missing_description_rows":missing_descriptions,
        "source_sha256":hashlib.sha256(payload).hexdigest(),
    }


def inspect_csv(payload: bytes, as_of: date, *, min_rows: int = 1_000,
                area_payload: bytes | None = None,
                descriptions_payload: bytes | None = None) -> dict:
    fields, rows = _parse_csv(payload, "geo-area-memberships")
    cols = _membership_columns(fields)
    area_map = None
    area_report = None
    description_report = None
    if area_payload is not None:
        area_map, area_report = _geo_area_map(area_payload)
    if descriptions_payload is not None:
        description_report = _description_report(descriptions_payload, as_of)

    active_groups = set()
    active_rows = 0
    china_rows = 0
    total_rows = 0
    unresolved_active = []
    unresolved_historical = 0
    for number, row in enumerate(rows, 2):
        total_rows += 1
        raw_group = _clean(row.get(cols["group"]))
        raw_member = _clean(row.get(cols["member"]))
        start = _clean(row.get(cols["start"]))
        end = _clean(row.get(cols["end"]))
        if not raw_group or not raw_member or not start:
            raise ValueError(f"Missing group/member/start date at row {number}")
        try:
            first = date.fromisoformat(start[:10])
            last = date.fromisoformat(end[:10]) if end else None
        except ValueError as error:
            raise ValueError(f"Invalid geo membership validity date at row {number}") from error
        if last and last < first:
            raise ValueError(f"Geo membership ends before start at row {number}")
        is_active = first <= as_of and (last is None or as_of <= last)
        if is_active:
            active_rows += 1
        if area_map is None:
            # Older fixture schemas contain literal area codes. Current DBT
            # numeric foreign keys must be resolved using the pinned geo-areas.
            if raw_member.isdigit() or (raw_group.isdigit() and raw_group != "1011"):
                raise ValueError("Numeric geo membership IDs require same-version official geo-areas map")
            group, member = raw_group.upper(), raw_member.upper()
        else:
            group = area_map.get(raw_group, raw_group.upper() if not raw_group.isdigit() else "")
            member = area_map.get(raw_member, raw_member.upper() if not raw_member.isdigit() else "")
            if not group or not member:
                if is_active:
                    unresolved_active.append({"row":number,"group_id":raw_group,
                                              "group_code":group,"member_id":raw_member,
                                              "member_code":member})
                    if member == "CN" and not group:
                        raise ValueError("Active China membership has unresolved group foreign key")
                else:
                    unresolved_historical += 1
                # Unknown non-China member IDs are retained in the audit but
                # are not guessed to be China. The pinned geo-areas table maps
                # China's unique primary key to CN independently.
                continue
        if member == "CN":
            china_rows += 1
        if is_active and member == "CN":
            active_groups.add(group)
    if total_rows < min_rows:
        raise ValueError(f"Official geographical membership table is suspiciously small: {total_rows} < {min_rows}")
    if area_map is not None and "CN" not in set(area_map.values()):
        raise ValueError("Pinned official geo-areas map lacks China code CN")
    if area_map is not None and "1011" not in set(area_map.values()):
        raise ValueError("Pinned official geo-areas map lacks Erga Omnes code 1011")
    if not active_groups:
        raise ValueError("Official membership source reports no active China groups after geo-area ID resolution")
    result = {
        "kind": "dbt-official-geo-area-memberships-v2",
        "as_of": as_of.isoformat(),
        "row_count": total_rows,
        "active_membership_rows": active_rows,
        "resolved_active_membership_rows": active_rows-len(unresolved_active),
        "china_membership_rows": china_rows,
        "active_china_group_count": len(active_groups),
        "active_china_group_ids": sorted(active_groups),
        "source_columns": cols,
        "source_header": fields,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "unresolved_active_membership_count": len(unresolved_active),
        "unresolved_active_memberships": unresolved_active[:100],
        "unresolved_historical_memberships": unresolved_historical,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "production_eligible": True,
    }
    if area_report:
        result.update({
            "geo_areas_source_sha256":area_report["source_sha256"],
            "geo_areas_row_count":area_report["row_count"],
            "geo_areas_mapped_area_count":area_report["mapped_area_count"],
            "geo_areas_source_columns":area_report["source_columns"],
            "geo_areas_source_header":area_report["source_header"],
        })
    if description_report:
        result.update({
            "geo_area_descriptions_source_sha256":description_report["source_sha256"],
            "geo_area_descriptions_row_count":description_report["row_count"],
            "geo_area_descriptions_active_rows":description_report["active_description_rows"],
            "geo_area_descriptions_source_columns":description_report["source_columns"],
            "geo_area_descriptions_source_header":description_report["source_header"],
        })
    return result


def download(version: str, as_of: date, output: str, report_path: str,
             geo_areas_path: str, geo_area_descriptions_path: str,
             reference_path: str) -> dict:
    url = source_url(version)
    req = urllib.request.Request(url, headers={
        "Accept": "text/csv", "User-Agent": "liao-china-geo-membership/2.0"})
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=120) as response, target.open("wb") as stream:
        if response.status != 200:
            raise ValueError(f"Official geo membership source returned {response.status}")
        resolved = response.geturl()
        if f"/versions/{version}/" not in resolved:
            raise ValueError("DBT geo membership response changed pinned version")
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 100_000_000:
                raise ValueError("Official geo membership CSV exceeds 100 MB bound")
            stream.write(block)
            digest.update(block)
    if size == 0:
        raise ValueError("Official geo membership CSV is empty")

    areas_payload = Path(geo_areas_path).read_bytes()
    descriptions_payload = Path(geo_area_descriptions_path).read_bytes()
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    for table, payload in (("geo-areas",areas_payload),
                           ("geo-area-descriptions",descriptions_payload)):
        evidence = reference.get(table) or {}
        if evidence.get("version") != version or evidence.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise ValueError("Same-version official geo reference table SHA/version mismatch: " + table)
    result = inspect_csv(target.read_bytes(), as_of, area_payload=areas_payload,
                         descriptions_payload=descriptions_payload)
    if result["source_sha256"] != digest.hexdigest():
        raise ValueError("Geo membership SHA changed after download")
    result.update({"version":version,"source_url":url,"resolved_url":resolved,"bytes":size,
                   "geo_areas_source_url":reference["geo-areas"]["source_url"],
                   "geo_areas_resolved_url":reference["geo-areas"]["resolved_url"],
                   "geo_area_descriptions_source_url":reference["geo-area-descriptions"]["source_url"],
                   "geo_area_descriptions_resolved_url":reference["geo-area-descriptions"]["resolved_url"]})
    Path(report_path).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                                 encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", default="gb-weekly-geo-area-memberships.csv")
    parser.add_argument("--report", default="gb-weekly-geo-area-memberships.json")
    parser.add_argument("--geo-areas", default="gb-weekly-geo-areas.csv")
    parser.add_argument("--geo-area-descriptions", default="gb-weekly-geo-area-descriptions.csv")
    parser.add_argument("--reference", default="gb-weekly-geo-area-reference.json")
    args = parser.parse_args()
    result = download(args.version,date.fromisoformat(args.as_of),args.output,args.report,
                      args.geo_areas,args.geo_area_descriptions,args.reference)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
