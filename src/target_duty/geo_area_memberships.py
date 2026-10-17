"""Pinned official DBT geographical-area membership source for China applicability.

Country-group membership changes over time (for example preference tiers).
A duty assigned to a group applies to China only when CN is an active member
of that exact group on the tariff snapshot date and CN is not explicitly
excluded by the measure.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.error
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
        raise ValueError("Cannot unambiguously identify official geo membership column: "
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
                  or n == "validity_start")
    end = _choose(fields,
        ("membership__effective_end_date", "membership__validity_end_date",
         "membership__end_date", "geo_area_membership__end_date",
         "membership_ends", "ends", "effective_end_date", "end_date",
         "validity_end"),
        lambda n: "end" in n and (
                  ("date" in n and ("membership" in n or "member" in n))
                  or n == "validity_end")
    return {"group": group, "member": member, "start": start, "end": end}


def inspect_csv(payload: bytes, as_of: date, *, min_rows: int = 1_000) -> dict:
    if not payload or b"\x00" in payload:
        raise ValueError("Empty or binary geo-area-memberships CSV")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    fields = list(reader.fieldnames or [])
    if not fields or len(fields) != len(set(fields)):
        raise ValueError("Missing or duplicate official geo membership CSV headers")
    cols = _columns(fields)
    active_groups = set()
    active_rows = 0
    cn_rows = 0
    total_rows = 0
    for number, row in enumerate(reader, 2):
        total_rows += 1
        if None in row:
            raise ValueError(f"Malformed geo membership CSV row {number}")
        group = _clean(row.get(cols["group"]))
        member = _clean(row.get(cols["member"])).upper()
        start = _clean(row.get(cols["start"]))
        end = _clean(row.get(cols["end"]))
        if not group or not member or not start:
            raise ValueError(f"Missing group/member/start date at row {number}")
        try:
            first = date.fromisoformat(start[:10])
            last = date.fromisoformat(end[:10]) if end else None
        except ValueError as error:
            raise ValueError(f"Invalid geo membership validity date at row {number}") from error
        if last and last < first:
            raise ValueError(f"Geo membership ends before start at row {number}")
        if member == "CN":
            cn_rows += 1
        if first <= as_of and (last is None or as_of <= last):
            active_rows += 1
            if member == "CN":
                active_groups.add(group)
    if total_rows < min_rows:
        raise ValueError(f"Official geographical membership table is suspiciously small: {total_rows} < {min_rows}")
    if not active_groups:
        raise ValueError("Official membership source reports no active China groups")
    return {
        "kind": "dbt-official-geo-area-memberships-v1",
        "as_of": as_of.isoformat(),
        "row_count": total_rows,
        "active_membership_rows": active_rows,
        "china_membership_rows": cn_rows,
        "active_china_group_count": len(active_groups),
        "active_china_group_ids": sorted(active_groups),
        "source_columns": cols,
        "source_header": fields,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "production_eligible": True,
    }


def download(version: str, as_of: date, output: str, report_path: str) -> dict:
    url = source_url(version)
    req = urllib.request.Request(url, headers={
        "Accept": "text/csv", "User-Agent": "liao-china-geo-membership/1.0"})
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
    result = inspect_csv(target.read_bytes(), as_of)
    if result["source_sha256"] != digest.hexdigest():
        raise ValueError("Geo membership SHA changed after download")
    result.update({"version": version, "source_url": url,
                   "resolved_url": resolved, "bytes": size})
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
