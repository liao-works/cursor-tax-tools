"""Pinned official DBT geographical-area memberships for China applicability.

Current DBT exports store geo_group_id/member_id as numeric foreign keys.
Resolve those IDs against the same immutable version's geo-areas table before
using the membership tree. Never compare numeric foreign keys directly to CN.
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

ROOT = "https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/versions/{version}/tables/{table}/data?format=csv"
VERSION = re.compile(r"v[0-9]+(?:\\.[0-9]+)*\\Z")
NULLS = {"", "#NA", "N/A", "NA", "None", "null", "NULL", "[]"}


def _table_url(version: str, table: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("Geographical source download requires immutable DBT version")
    if table not in ("geo-area-memberships", "geo-areas"):
        raise ValueError("Unexpected official geographical table")
    return ROOT.format(version=version, table=table)


def source_url(version: str) -> str:
    return _table_url(version, "geo-area-memberships")


def area_source_url(version: str) -> str:
    return _table_url(version, "geo-areas")


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


def _columns(fields: list[str]) -> dict[str, str]:
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


def _geo_area_map(payload: bytes) -> tuple[dict[str, str], dict]:
    fields, rows = _parse_csv(payload, "geo-areas")
    id_col = _choose(fields,
        ("trackedmodel_ptr_id", "geo_area_ptr_id", "geo_area_pk", "id"),
        lambda n: n in ("trackedmodel_ptr_id", "geo_area_ptr_id", "geo_area_pk", "id"))
    preferred = ("geo_area__id", "geographical_area__id", "geo_area_id",
                 "geographical_area_id", "geo_area_code", "geographical_area_code",
                 "area_code", "code")
    candidates = [name for name in preferred if name in fields and name != id_col]
    candidates += [name for name in fields if name != id_col and name not in candidates
                   and ("geo_area" in name.lower() or "geographical_area" in name.lower())
                   and "id" in name.lower() and not any(
                       token in name.lower() for token in ("type", "description", "trackedmodel", "ptr"))]
    selected = None
    mapping: dict[str, str] = {}
    diagnostics = []
    for code_col in candidates:
        trial: dict[str, str] = {}
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
        diagnostics.append({"column": code_col, "count": len(trial),
                            "has_CN": "CN" in values, "has_1011": "1011" in values,
                            "conflict": conflict})
        if not conflict and "CN" in values and "1011" in values and len(trial) >= 50:
            selected = code_col
            mapping = trial
            break
    if selected is None:
        raise ValueError("Cannot resolve official geo-area foreign keys to codes; "
                         + json.dumps({"headers": fields, "id_column": id_col,
                                       "candidates": diagnostics}, ensure_ascii=False))
    report = {
        "source_header": fields,
        "source_columns": {"id": id_col, "code": selected},
        "row_count": len(rows),
        "mapped_area_count": len(mapping),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "has_china_code": "CN" in set(mapping.values()),
        "has_erga_omnes_code": "1011" in set(mapping.values()),
    }
    return mapping, report


def inspect_csv(payload: bytes, as_of: date, *, min_rows: int = 1_000,
                area_payload: bytes | None = None) -> dict:
    fields, rows = _parse_csv(payload, "geo-area-memberships")
    cols = _columns(fields)
    area_map = None
    area_report = None
    if area_payload is not None:
        area_map, area_report = _geo_area_map(area_payload)
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
        if area_map is None:
            # Backward-compatible unit fixtures may use human-readable codes.
            # Numeric foreign keys are never treated as geography codes.
            if raw_member.isdigit() or (raw_group.isdigit() and raw_group != "1011"):
                raise ValueError("Numeric geo membership IDs require same-version official geo-areas map")
            group = raw_group.upper()
            member = raw_member.upper()
        else:
            group = area_map.get(raw_group, raw_group.upper() if not raw_group.isdigit() else "")
            member = area_map.get(raw_member, raw_member.upper() if not raw_member.isdigit() else "")
            if (not group or not member) and is_active:
                unresolved_active.append({"row": number, "group_id": raw_group,
                                          "member_id": raw_member})
                continue
            if not group or not member:
                unresolved_historical += 1
                continue
        if member == "CN":
            china_rows += 1
        if is_active:
            active_rows += 1
            if member == "CN":
                active_groups.add(group)
    if total_rows < min_rows:
        raise ValueError(f"Official geographical membership table is suspiciously small: {total_rows} < {min_rows}")
    if unresolved_active:
        raise ValueError("Active geographical memberships contain unresolved official foreign keys: "
                         + json.dumps(unresolved_active[:20], ensure_ascii=False))
    if not active_groups:
        raise ValueError("Official membership source reports no active China groups after geo-area ID resolution")
    result = {
        "kind": "dbt-official-geo-area-memberships-v2",
        "as_of": as_of.isoformat(),
        "row_count": total_rows,
        "active_membership_rows": active_rows,
        "china_membership_rows": china_rows,
        "active_china_group_count": len(active_groups),
        "active_china_group_ids": sorted(active_groups),
        "source_columns": cols,
        "source_header": fields,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "unresolved_historical_memberships": unresolved_historical,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "production_eligible": True,
    }
    if area_report:
        result.update({
            "geo_areas_source_sha256": area_report["source_sha256"],
            "geo_areas_row_count": area_report["row_count"],
            "geo_areas_mapped_area_count": area_report["mapped_area_count"],
            "geo_areas_source_columns": area_report["source_columns"],
            "geo_areas_source_header": area_report["source_header"],
        })
    return result


def _download_csv(url: str, version: str, target: Path, label: str) -> tuple[str, int, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    req = urllib.request.Request(url, headers={
        "Accept": "text/csv", "User-Agent": "liao-china-geo-membership/2.0"})
    with urllib.request.urlopen(req, timeout=120) as response, target.open("wb") as stream:
        if response.status != 200:
            raise ValueError(f"Official {label} source returned {response.status}")
        resolved = response.geturl()
        if f"/versions/{version}/tables/" not in resolved:
            raise ValueError(f"DBT {label} response changed pinned version")
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 100_000_000:
                raise ValueError(f"Official {label} CSV exceeds 100 MB bound")
            stream.write(block)
            digest.update(block)
    if size == 0:
        raise ValueError(f"Official {label} CSV is empty")
    return resolved, size, digest.hexdigest()


def download(version: str, as_of: date, output: str, report_path: str) -> dict:
    membership_url = source_url(version)
    areas_url = area_source_url(version)
    target = Path(output)
    areas_target = target.with_name("gb-weekly-geo-areas.csv")
    resolved_areas, areas_size, areas_digest = _download_csv(
        areas_url, version, areas_target, "geo-areas")
    resolved_membership, membership_size, membership_digest = _download_csv(
        membership_url, version, target, "geo-area-memberships")
    membership_payload = target.read_bytes()
    areas_payload = areas_target.read_bytes()
    result = inspect_csv(membership_payload, as_of, area_payload=areas_payload)
    if result["source_sha256"] != membership_digest:
        raise ValueError("Geo membership SHA changed after download")
    if result["geo_areas_source_sha256"] != areas_digest:
        raise ValueError("Geo-areas SHA changed after download")
    result.update({
        "version": version,
        "source_url": membership_url,
        "resolved_url": resolved_membership,
        "bytes": membership_size,
        "geo_areas_source_url": areas_url,
        "geo_areas_resolved_url": resolved_areas,
        "geo_areas_bytes": areas_size,
    })
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",
                                 encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", default="gb-weekly-geo-area-memberships.csv")
    parser.add_argument("--report", default="gb-weekly-geo-area-memberships.json")
    args = parser.parse_args()
    result = download(args.version, date.fromisoformat(args.as_of), args.output, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
