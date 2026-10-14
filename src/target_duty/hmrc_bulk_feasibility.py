"""HMRC Bulk Tariff inventory continuity planner, NOT a production delta importer.

The HMRC beta Bulk Data File List advertises annual, monthly and daily files,
but its list-response schema, replay operations, country scope and deletion
semantics are NOT asserted by this module. An operator supplies a normalized
manifest of *already downloaded and SHA-256 checked* files for an offline
continuity proof; real API authentication, XML/CSV parsing and replay remain
separate work and must be proved before promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

TYPES = {"TARIFF-ANNUAL", "TARIFF-MONTHLY", "TARIFF-DAILY"}
HEX = re.compile(r"[a-fA-F0-9]{64}\Z")
ID = re.compile(r"[A-Za-z0-9._-]{1,160}\Z")


def first_day(day: date) -> date:
    return day.replace(day=1)


def add_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def load_inventory(path: str) -> dict:
    """Fail closed on duplicated, malformed or unverified inventory entries."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("kind") != "hmrc-tariff-files-operator-normalized-v1":
        raise ValueError("Expected explicitly normalized HMRC file inventory")
    if manifest.get("source_semantics_verified") is not False:
        raise ValueError("This phase cannot certify HMRC replay semantics")
    records = manifest.get("files")
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError("Inventory must have at most 1000 file records")
    seen: set[tuple[str, str]] = set()
    result = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Non-object inventory record")
        kind = row.get("information_type")
        ref = row.get("reference")
        day = date.fromisoformat(row["period"])  # day for daily, first day of month/year otherwise
        if kind not in TYPES or not isinstance(ref, str) or not ID.fullmatch(ref):
            raise ValueError("Invalid information type or opaque file reference")
        if kind == "TARIFF-ANNUAL" and (day.month, day.day) != (1, 1):
            raise ValueError("Annual anchor must be January 1")
        if kind == "TARIFF-MONTHLY" and day.day != 1:
            raise ValueError("Monthly period must start on day 1")
        if not HEX.fullmatch(str(row.get("sha256", ""))) or not isinstance(row.get("size_bytes"), int) or isinstance(row.get("size_bytes"), bool) or row["size_bytes"] < 1:
            raise ValueError("Local size/hash verification evidence required")
        key = (kind, day.isoformat())
        if key in seen:
            raise ValueError(f"Duplicate inventory period {key}")
        seen.add(key)
        result.append({
            "information_type": kind, "period": day.isoformat(),
            "reference": ref, "sha256": row["sha256"].lower(),
            "size_bytes": row["size_bytes"],
        })
    return {"kind": manifest["kind"], "files": result,
            "source_semantics_verified": False}


def verified_local_file(record: dict, directory: str) -> bool:
    """Actually validate local raw file bytes, not just manifest claims."""
    base = Path(directory).resolve()
    target = (base / record["reference"]).resolve()
    if base not in target.parents:
        raise ValueError("Source file escapes verified inventory directory")
    if not target.is_file():
        return False
    digest = hashlib.sha256()
    size = 0
    with target.open("rb") as stream:
        while buf := stream.read(1024 * 1024):
            size += len(buf)
            if size > record["size_bytes"]:
                return False
            digest.update(buf)
    return size == record["size_bytes"] and digest.hexdigest() == record["sha256"]


def plan(inventory: dict, *, as_of: date, local_dir: str | None = None) -> dict:
    """Plan annual + monthly for finished months + daily for current month.

    Do not skip a date because it was a weekend or the daily file is not yet
    published. Missing days keep the chain incomplete.
    """
    if as_of < date(2021, 1, 1):
        raise ValueError("Invalid tariff date")
    files = {(row["information_type"], row["period"]): row
             for row in inventory["files"]}
    first = date(as_of.year, 1, 1)
    required = [("TARIFF-ANNUAL", first)]
    cursor = add_month(first)
    while cursor < first_day(as_of):
        # Every completed month needs a roll-up; never assume daily files
        # survived expiry or accept silent discontinuities.
        required.append(("TARIFF-MONTHLY", cursor - timedelta(days=1)))
        cursor = add_month(cursor)
    cursor = first_day(as_of)
    while cursor <= as_of:
        required.append(("TARIFF-DAILY", cursor))
        cursor += timedelta(days=1)

    # For completed months the period is the month covered, not publication
    # day. For example February's roll-up is labelled 2026-02-01.
    normalized_required = [
        (kind, first_day(day) if kind == "TARIFF-MONTHLY" else day)
        for kind, day in required
    ]
    checked = []
    missing = []
    corrupt = []
    for kind, day in normalized_required:
        key = (kind, day.isoformat())
        item = files.get(key)
        if item is None:
            missing.append({"information_type": kind, "period": day.isoformat()})
            continue
        if local_dir and not verified_local_file(item, local_dir):
            corrupt.append({"information_type": kind, "period": day.isoformat(),
                            "reference": item["reference"]})
        checked.append(item)
    # Registry alone is not evidence that an operator actually downloaded and
    # checked the bytes; the local_dir argument is essential for verified chain.
    complete = bool(local_dir) and not missing and not corrupt
    return {
        "kind": "hmrc-bulk-coverage-feasibility-v1",
        "production_eligible": False,
        "source_semantics_verified": False,
        "region_scope_verified": False,
        "delta_application_implemented": False,
        "as_of": as_of.isoformat(), "required_files": len(required),
        "inventory_matched": len(checked),
        "locally_hash_verified": bool(local_dir),
        "metadata_chain_complete": not missing,
        "verified_bytes_complete": complete,
        "missing": missing, "corrupt_or_missing_bytes": corrupt,
        "ordered_files": checked,
        "notes": ("This checks file-period continuity and local SHA-256 only; "
                  "it is NOT an HMRC schema validator, change replayer, "
                  "region-coverage proof, or production freshness SLA."),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True,
                        help="Operator-normalized file list; not raw HMRC API JSON")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--local-dir", help="Directory with previously downloaded raw files")
    parser.add_argument("--output", default="hmrc-bulk-feasibility.json")
    args = parser.parse_args()
    data = load_inventory(args.inventory)
    result = plan(data, as_of=date.fromisoformat(args.as_of), local_dir=args.local_dir)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "required": result["required_files"],
        "missing": len(result["missing"]),
        "verified": result["verified_bytes_complete"],
        "production_eligible": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
