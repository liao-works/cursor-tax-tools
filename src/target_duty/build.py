#!/usr/bin/env python3
"""Build a *shadow* desktop-compatible SQLite from DBT Third country duty (103/1011).

No UK-wide dataset download, no HTML scraping and no production release mutation.
NI figures are intentionally carried from a legacy snapshot as stale, never verified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

DBT_URL = ("https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/"
           "versions/{version}/tables/measures-on-declarable-commodities/data")
COLUMNS = (
    "commodity__code", "commodity__description", "measure__sid",
    "measure__type__id", "measure__geographical_area__id",
    "measure__duty_expression", "measure__additional_code__code",
    "measure__conditions", "measure__excluded_geographical_areas__ids",
    "measure__effective_start_date", "measure__effective_end_date"
)
NA = {"", "#NA", "N/A", "null", "None", "[]", "{}", "NULL"}


def build_url(version: str = "latest") -> str:
    if not re.fullmatch(r"(?:latest|v[0-9]+(?:\.[0-9]+)*)", version):
        raise ValueError("Invalid pinned DBT version")
    params = [("format", "csv"), ("query-simple", ""),
              ("measure__type__id", "103"),
              ("measure__geographical_area__id", "1011")]
    params.extend(("_columns", field) for field in COLUMNS)
    return DBT_URL.format(version=version) + "?" + urllib.parse.urlencode(params)


def present(value: str | None) -> bool:
    return value is not None and str(value).strip() not in NA


def active(row: dict, day: date) -> bool:
    start, end = row.get("measure__effective_start_date", ""), row.get("measure__effective_end_date", "")
    if not present(start):
        raise ValueError("GB official target measure missing effective_start_date")
    try:
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end) if present(end) else None
    except ValueError as exc:
        raise ValueError("Malformed GB official measure effective date") from exc
    if end_day is not None and end_day < start_day:
        raise ValueError("GB official measure has reversed effective dates")
    return start_day <= day and (end_day is None or day <= end_day)


def choose(rows: list[dict]) -> tuple[str, str]:
    """Return only an unambiguous, unconditional rate; otherwise empty + status."""
    if not rows:
        return "", "pending"
    expressions = set()
    for row in rows:
        rate = (row.get("measure__duty_expression") or "").strip()
        if (not present(rate)
                or present(row.get("measure__additional_code__code"))
                or present(row.get("measure__conditions"))
                or present(row.get("measure__excluded_geographical_areas__ids"))):
            return "", "conditional"
        expressions.add(rate)
    if len(expressions) != 1:
        return "", "conditional"
    return next(iter(expressions)), "verified"


def legacy_ni(path: str | None) -> dict[str, tuple[str | None, str | None]]:
    if not path:
        return {}
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        return {code: (rate, url) for code, rate, url in db.execute(
            "SELECT code,north_ireland_rate,north_ireland_url FROM tariffs")}
    finally:
        db.close()


SCHEMA = """
CREATE TABLE tariffs (
  code TEXT PRIMARY KEY, description TEXT, rate TEXT NOT NULL, url TEXT,
  north_ireland_rate TEXT, north_ireland_url TEXT, other_rate TEXT,
  anti_dumping_rate TEXT, countervailing_rate TEXT, last_updated TEXT,
  uk_source_version TEXT, ni_source_version TEXT,
  uk_verified_at TEXT, ni_verified_at TEXT,
  uk_rate_status TEXT NOT NULL, ni_rate_status TEXT NOT NULL
);
CREATE INDEX idx_tariffs_uk_status ON tariffs(uk_rate_status);
"""


def build(csv_path: str, output: str, *, version: str, as_of: date,
          legacy_db: str | None = None, min_records: int = 10000,
          min_verified_ratio: float = 0.85) -> dict:
    by_code: dict[str, list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    invalid_rows = 0
    # Fingerprint the *filtered* official export. The API alias 'latest' is
    # not an immutable source version, so incorporate actual response bytes.
    source_hash = hashlib.sha256()
    with open(csv_path, "rb") as source_stream:
        while chunk := source_stream.read(1024 * 1024):
            source_hash.update(chunk)
    source_sha256 = source_hash.hexdigest()
    with open(csv_path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("Missing required DBT CSV columns")
        for row in reader:
            code = (row.get("commodity__code") or "").strip()
            if not re.fullmatch(r"\d{10}", code):
                raise ValueError("GB official source has invalid commodity code")
            if row.get("measure__type__id") != "103" or row.get("measure__geographical_area__id") != "1011":
                raise ValueError("Non Third-country/ERGA-OMNES data in source")
            if active(row, as_of):
                by_code[code].append(row)
                if present(row.get("commodity__description")):
                    names[code] = row["commodity__description"].strip()
            else:
                invalid_rows += 1

    if len(by_code) < min_records:
        raise ValueError(f"Coverage gate: only {len(by_code)} distinct codes (<{min_records})")

    previous_ni = legacy_ni(legacy_db)
    now = datetime.now(timezone.utc).isoformat()
    output_file = Path(output)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_file.parent) as tmp_dir:
        tmp_db = Path(tmp_dir) / "target-duty.db"
        conn = sqlite3.connect(tmp_db)
        conn.executescript(SCHEMA)
        verified = conditional = ni_stale = 0
        for code in sorted(by_code):
            rate, status = choose(by_code[code])
            verified += status == "verified"
            conditional += status == "conditional"
            ni, ni_url = previous_ni.get(code, (None, None))
            ni_state = "stale" if present(ni) else "pending"
            ni_stale += ni_state == "stale"
            conn.execute("""INSERT INTO tariffs
                (code,description,rate,url,north_ireland_rate,north_ireland_url,
                 other_rate,last_updated,uk_source_version,ni_source_version,
                 uk_verified_at,uk_rate_status,ni_rate_status)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, names.get(code), rate,
                 f"https://www.trade-tariff.service.gov.uk/uk/commodities/{code}",
                 ni, ni_url, None, now, version,
                 "legacy-unverified" if ni_state == "stale" else None,
                 now if status == "verified" else None, status, ni_state))
        conn.commit()
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]
        conn.close()
        if ok != "ok" or rows != len(by_code):
            raise ValueError("SQLite integrity or count gate failed")
        if verified / rows < min_verified_ratio:
            raise ValueError(f"Verified-rate gate failed: {verified}/{rows} (<{min_verified_ratio:.0%})")
        tmp_db.replace(output_file)

    digest = hashlib.sha256(output_file.read_bytes()).hexdigest()
    meta = {
        "version": f"target-duty-{version}-{as_of.isoformat()}-{source_sha256[:12]}",
        "timestamp": now, "last_modified": now,
        "file_hash": f"sha256:{digest}", "file_size": output_file.stat().st_size,
        "record_count": rows, "source": {"gb": "DBT", "version": version,
        "measure_type": "103", "geographical_area": "1011",
        "filtered_csv_sha256": source_sha256,
        "effective_date": as_of.isoformat(), "ni": "legacy-unverified"},
        "quality_metrics": {"gb_verified": verified, "gb_conditional": conditional,
                            "ni_stale": ni_stale, "ignored_inactive_or_invalid": invalid_rows}
    }
    output_file.with_suffix(".metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="Use a supplied DBT CSV for reproducible tests/offline replay")
    parser.add_argument("--version", default="latest", help="DBT dataset version, e.g. v4.0.1595")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--legacy-db", help="Old snapshot for NON-VERIFIED NI fallback only")
    parser.add_argument("--output", default="target-duty.db")
    parser.add_argument("--min-records", type=int, default=10000)
    parser.add_argument("--min-verified-ratio", type=float, default=0.85)
    args = parser.parse_args()
    if not args.csv:
        # Online latest aliases can expose a new version before its tables
        # are published. Fail closed unless official version + schema is ready.
        from .dbt_version_discovery import resolve_requested_version
        args.version=resolve_requested_version(args.version)
    url = build_url(args.version)
    if args.csv:
        meta = build(args.csv, args.output, version=args.version,
                     as_of=date.fromisoformat(args.as_of), legacy_db=args.legacy_db,
                     min_records=args.min_records, min_verified_ratio=args.min_verified_ratio)
    else:
        with tempfile.TemporaryDirectory() as directory:
            csv_file = Path(directory) / "official-filtered.csv"
            request = urllib.request.Request(url, headers={
                "User-Agent": "liao-tools-target-duty-shadow/1.0",
                "Accept": "text/csv"})
            with urllib.request.urlopen(request, timeout=180) as response, open(csv_file, "wb") as out:
                if response.status != 200:
                    raise RuntimeError(f"DBT responded {response.status}")
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            meta = build(str(csv_file), args.output, version=args.version,
                         as_of=date.fromisoformat(args.as_of), legacy_db=args.legacy_db,
                         min_records=args.min_records,
                         min_verified_ratio=args.min_verified_ratio)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
