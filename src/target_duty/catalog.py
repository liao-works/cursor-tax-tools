"""Independent, lossless DBT GB commodity catalogue (shadow/evidence only).

The catalogue is intentionally NOT derived from 103/1011 duty measures.
It preserves suffixes, historical validity, hierarchy and all source rows.
It never changes the production tariffs.db or publishes a release.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import urllib.error
import urllib.request
import time
from datetime import date, datetime, timezone
from pathlib import Path

BASE = ("https://data.api.trade.gov.uk/v1/datasets/"
        "uk-tariff-2021-01-01/versions/{version}/tables/commodities-report/data")
FIELDS = (
    "commodity__sid", "commodity__code", "commodity__suffix",
    "commodity__description", "commodity__validity_start",
    "commodity__validity_end", "parent__sid", "parent__code",
    "parent__suffix",
)
NA = {"", "#NA", "N/A", "null", "NULL", "None"}
SCHEMA = """
CREATE TABLE commodities (
    sid TEXT NOT NULL,
    code TEXT NOT NULL,
    suffix TEXT NOT NULL,
    description TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    parent_sid TEXT,
    parent_code TEXT,
    parent_suffix TEXT,
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    suffix80 INTEGER NOT NULL CHECK(suffix80 IN (0,1)),
    PRIMARY KEY (sid, suffix)
);
CREATE INDEX idx_catalog_code ON commodities(code);
CREATE INDEX idx_catalog_parent_active ON commodities(parent_sid, active);
CREATE INDEX idx_catalog_active ON commodities(active, suffix80, code);
CREATE TABLE provenance (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def source_url(version: str) -> str:
    if not re.fullmatch(r"(?:latest|v[0-9]+(?:\.[0-9]+)*)", version):
        raise ValueError("Unsafe DBT version")
    # Use the official commodities-report projection. On current DBT v4,
    # the raw commodities table has sid/item_id but not the resolved
    # commodity__code / parent / description columns. Avoid query-simple
    # for this full catalogue export.
    return BASE.format(version=version) + "?format=csv"


def optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if value not in NA else None


def required_date(value: str | None, field: str) -> date:
    if not optional(value):
        raise ValueError(f"Missing required {field}")
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value}") from exc


def effective(start: date, end: date | None, as_of: date) -> bool:
    return start <= as_of and (end is None or as_of <= end)


def download_catalog(version: str, destination: Path, *, max_bytes: int = 80_000_000) -> str:
    req = urllib.request.Request(source_url(version), headers={
        "Accept": "text/csv", "User-Agent": "liao-target-duty-catalog-shadow/1.0"})
    for attempt in range(3):
        total = 0
        try:
            with urllib.request.urlopen(req, timeout=180) as response, destination.open("wb") as dst:
                if response.status != 200:
                    raise RuntimeError(f"DBT catalogue returned HTTP {response.status}")
                resolved_url = response.geturl()
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("DBT catalogue exceeds bounded download")
                    dst.write(chunk)
            if not total:
                raise ValueError("DBT catalogue response was empty")
            return resolved_url
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            destination.unlink(missing_ok=True)
            if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("Unexpected unreachable DBT retry state")


def build(csv_path: str, output: str, *, as_of: date, source_version: str,
          resolved_url: str | None = None, min_active: int = 10000) -> dict:
    """Stage every catalogue row; never prune inactive/non-declarable nodes."""
    if min_active < 1:
        raise ValueError("min_active must be positive")
    csv_file = Path(csv_path)
    sha = hashlib.sha256()
    with csv_file.open("rb") as stream:
        while part := stream.read(1024 * 1024):
            sha.update(part)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        staging = Path(tmp) / "commodities.db"
        conn = sqlite3.connect(staging)
        try:
            conn.executescript(SCHEMA)
            rows = 0
            with csv_file.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                missing = sorted(set(FIELDS) - set(reader.fieldnames or []))
                if missing:
                    raise ValueError(
                        f"Official commodity CSV is missing required columns: {missing}; "
                        f"available: {reader.fieldnames}")
                for row in reader:
                    rows += 1
                    sid = optional(row["commodity__sid"])
                    code = optional(row["commodity__code"])
                    suffix = optional(row["commodity__suffix"])
                    if not sid or not re.fullmatch(r"\d{10}", code or ""):
                        raise ValueError(f"Invalid commodity SID/code at row {rows}")
                    if not re.fullmatch(r"\d{2}", suffix or ""):
                        raise ValueError(f"Invalid commodity suffix at row {rows}")
                    start = required_date(row["commodity__validity_start"], "validity_start")
                    raw_end = optional(row["commodity__validity_end"])
                    end = required_date(raw_end, "validity_end") if raw_end else None
                    if end and end < start:
                        raise ValueError(f"Commodity ends before start at row {rows}")
                    parent_code = optional(row["parent__code"])
                    parent_suffix = optional(row["parent__suffix"])
                    if parent_code and not re.fullmatch(r"\d{10}", parent_code):
                        raise ValueError(f"Invalid parent code at row {rows}")
                    if parent_suffix and not re.fullmatch(r"\d{2}", parent_suffix):
                        raise ValueError(f"Invalid parent suffix at row {rows}")
                    try:
                        conn.execute("""INSERT INTO commodities VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                            sid, code, suffix, optional(row["commodity__description"]),
                            start.isoformat(), end.isoformat() if end else None,
                            optional(row["parent__sid"]), parent_code, parent_suffix,
                            int(effective(start, end, as_of)), int(suffix == "80")))
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(f"Duplicate commodity SID/suffix at row {rows}") from exc

            # Per UK Government API catalogue: only leaf nodes are declarable.
            # A suffix-80 node with any *currently active* child is structural.
            leaf_sql = """SELECT COUNT(DISTINCT c.code) FROM commodities c
                WHERE c.active=1 AND c.suffix80=1
                AND NOT EXISTS (SELECT 1 FROM commodities child
                    WHERE child.parent_sid=c.sid AND child.active=1)"""
            active_codes = conn.execute(leaf_sql).fetchone()[0]
            suffix80_codes = conn.execute(
                "SELECT COUNT(DISTINCT code) FROM commodities WHERE active=1 AND suffix80=1"
            ).fetchone()[0]
            if active_codes < min_active:
                raise ValueError(f"Coverage gate: {active_codes} active declarable codes < {min_active}")
            counts = {
                "all_catalogue_rows": rows,
                "all_distinct_codes": conn.execute(
                    "SELECT COUNT(DISTINCT code) FROM commodities").fetchone()[0],
                "active_suffix80_candidates": suffix80_codes,
                "active_declarable_leaf_codes": active_codes,
                "inactive_or_non_suffix80_rows": conn.execute(
                    "SELECT COUNT(*) FROM commodities WHERE active=0 OR suffix80=0"
                ).fetchone()[0],
            }
            generated = datetime.now(timezone.utc).isoformat()
            manifest = {
                "kind": "gb-official-commodity-catalogue-shadow",
                "production_eligible": False,
                "source": "DBT commodities table",
                "requested_version": source_version,
                "resolved_url": resolved_url,
                "source_csv_sha256": sha.hexdigest(),
                "as_of": as_of.isoformat(),
                "generated_at": generated,
                "counts": counts,
                "notes": "Declarable codes are active suffix-80 leaves (no active children); all hierarchy and history retained. Source cross-check still required.",
            }
            for key, value in (
                ("as_of", manifest["as_of"]), ("source_csv_sha256", sha.hexdigest()),
                ("requested_version", source_version), ("generated_at", generated)):
                conn.execute("INSERT INTO provenance VALUES (?,?)", (key, value))
            conn.commit()
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity_check failed")
        finally:
            conn.close()
        staging.replace(out)
    manifest["database_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
    manifest["database_bytes"] = out.stat().st_size
    out.with_suffix(".metadata.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _codes_from_tariffs(path: str) -> set[str]:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {code for (code,) in conn.execute("SELECT code FROM tariffs")}
    finally:
        conn.close()


def compare(catalog_db: str, legacy_db: str, focused_db: str, output: str) -> dict:
    """Audit incompatible scopes without treating missing 103 as a deletion."""
    conn = sqlite3.connect(Path(catalog_db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        catalog = {code for (code,) in conn.execute(
            """SELECT DISTINCT c.code FROM commodities c
               WHERE c.active=1 AND c.suffix80=1
               AND NOT EXISTS (SELECT 1 FROM commodities child
                 WHERE child.parent_sid=c.sid AND child.active=1)""")}
        non_leaf_or_historical = {code for (code,) in conn.execute(
            "SELECT DISTINCT code FROM commodities")} - catalog
        as_of = conn.execute(
            "SELECT value FROM provenance WHERE key='as_of'").fetchone()[0]
    finally:
        conn.close()
    if not catalog:
        raise ValueError("Cannot reconcile an empty active catalogue")
    old = _codes_from_tariffs(legacy_db)
    focused = _codes_from_tariffs(focused_db)
    union = catalog | old | focused
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    import csv as csv_module
    with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv_module.writer(stream)
        writer.writerow(["code", "active_leaf_declarable", "structural_or_historical",
                         "legacy_present", "focused_gb_present", "audit_status"])
        for code in sorted(union):
            current, old_present, focused_present = code in catalog, code in old, code in focused
            status = ("catalogue_leaf_not_in_focused" if current and not focused_present
                      else "focused_not_in_active_leaf" if focused_present and not current
                      else "legacy_only_not_active_leaf" if old_present and not current and not focused_present
                      else "present")
            if status != "present":
                writer.writerow([code, int(current), int(code in non_leaf_or_historical),
                                 int(old_present), int(focused_present), status])
    result = {
        "kind": "gb-catalogue-scope-reconciliation",
        "production_eligible": False,
        "as_of": as_of,
        "active_official_leaf_catalogue": len(catalog),
        "legacy_codes": len(old),
        "focused_gb_codes": len(focused),
        "active_leaf_missing_focused": len(catalog - focused),
        "focused_not_active_leaf": len(focused - catalog),
        "legacy_not_active_leaf": len(old - catalog),
        "legacy_not_focused": len(old - focused),
        "csv": out.with_suffix(".csv").name,
        "note": ("Official active suffix-80 leaf catalogue, legacy history and GB "
                 "103/1011 duties have different scopes; discrepancies are not deletions. "
                 "Cross-source confirmation and NI are not yet production-verified."),
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    create = command.add_parser("build")
    create.add_argument("--csv", help="Local official CSV for deterministic replay")
    create.add_argument("--version", default="latest")
    create.add_argument("--as-of", default=date.today().isoformat())
    create.add_argument("--output", default="gb-commodity-catalogue.db")
    create.add_argument("--min-active", type=int, default=10000)
    audit = command.add_parser("compare")
    audit.add_argument("--catalogue", required=True)
    audit.add_argument("--legacy", required=True)
    audit.add_argument("--focused", required=True)
    audit.add_argument("--output", default="gb-catalogue-coverage.json")
    args = parser.parse_args()
    if args.command == "compare":
        result = compare(args.catalogue, args.legacy, args.focused, args.output)
    elif args.csv:
        result = build(args.csv, args.output, as_of=date.fromisoformat(args.as_of),
                       source_version=args.version, min_active=args.min_active)
    else:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "commodities.csv"
            resolved = download_catalog(args.version, path)
            result = build(str(path), args.output, as_of=date.fromisoformat(args.as_of),
                           source_version=args.version, resolved_url=resolved,
                           min_active=args.min_active)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
