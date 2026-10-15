"""Pinned DBT all-measure evidence for catalogue leaf codes absent from GB 103/1011.

This is a deliberately read-only, shadow audit. It retains every returned
measure row including conditional, non-103, non-1011 and expired measures.
Never infer a zero duty, payable duty, or NI "Other" from these records.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .catalog import optional

BASE = ("https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/"
        "versions/{version}/tables/measures-on-declarable-commodities/data")
# Enumerated official DBT columns; no prematurely flattened duty.
FIELDS = (
    "commodity__code", "measure__sid", "measure__type__id",
    "measure__geographical_area__id", "measure__duty_expression",
    "measure__additional_code__code", "measure__conditions",
    "measure__excluded_geographical_areas__ids",
    "measure__effective_start_date", "measure__effective_end_date",
    "measure__type__description", "measure__quota__order_number",
    "measure__footnotes", "measure__regulation__id",
)
REQUIRED = frozenset(FIELDS[:10])
SCHEMA = """
CREATE TABLE source_requests (
 code TEXT PRIMARY KEY, response_sha256 TEXT,
 row_count INTEGER NOT NULL, status TEXT NOT NULL, error TEXT
);
CREATE TABLE measures (
 code TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 measure_sid TEXT,
 measure_type TEXT,
 geographical_area TEXT,
 expression TEXT,
 additional_code TEXT,
 conditions TEXT,
 excluded_areas TEXT,
 starts TEXT,
 ends TEXT,
 effective_state TEXT NOT NULL,
 raw_json TEXT NOT NULL,
 PRIMARY KEY (code, ordinal),
 FOREIGN KEY (code) REFERENCES source_requests(code)
);
CREATE INDEX idx_measure_code_type ON measures(code, measure_type, geographical_area);
CREATE TABLE audit_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def source_url(version: str, code: str) -> str:
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("Audit requires a pinned DBT version, not latest")
    if not re.fullmatch(r"\d{10}", code):
        raise ValueError("Invalid 10-digit commodity code")
    query = [("format", "csv"), ("query-simple", ""), ("commodity__code", code)]
    query.extend(("_columns", field) for field in FIELDS)
    return BASE.format(version=version) + "?" + urllib.parse.urlencode(query)


def fetch_csv(code: str, version: str, *, max_bytes: int = 4_000_000) -> bytes:
    req = urllib.request.Request(source_url(version, code), headers={
        "Accept": "text/csv", "User-Agent": "liao-gb-measure-evidence/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"DBT status {resp.status}")
                payload = resp.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError("Measure response exceeds bounded size")
            if not payload:
                raise ValueError("Empty measure response")
            return payload
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("Unreachable network retry state")


def parse_csv(payload: bytes, code: str) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("Binary content in official CSV")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Official measure columns missing: {sorted(missing)}")
    result = []
    for number, row in enumerate(reader, 2):
        if None in row:
            raise ValueError(f"Malformed official CSV at row {number}")
        if row["commodity__code"] != code:
            raise ValueError(f"Off-target commodity at row {number}")
        if not re.fullmatch(r"\d+", optional(row.get("measure__sid")) or ""):
            raise ValueError(f"Missing/invalid measure SID at row {number}")
        result.append(row)
    return result


def effective_state(row: dict, day: date) -> str:
    start = optional(row.get("measure__effective_start_date"))
    end = optional(row.get("measure__effective_end_date"))
    if not start:
        return "unknown"
    try:
        first = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10]) if end else None
    except ValueError:
        return "unknown"
    if last and last < first:
        return "unknown"
    return "active" if first <= day and (last is None or day <= last) else "inactive"


def classify(rows: list[dict], day: date) -> str:
    if not rows:
        return "no_measures_in_official_export"
    active = [r for r in rows if effective_state(r, day) == "active"]
    unknown = any(effective_state(r, day) == "unknown" for r in rows)
    if any(r.get("measure__type__id") == "103"
           and r.get("measure__geographical_area__id") == "1011" for r in active):
        return "active_103_1011_conflicts_with_focused"
    if unknown:
        return "unresolved_effective_date"
    if not active:
        return "historical_measures_only"
    if any(r.get("measure__type__id") == "103" for r in active):
        return "active_103_other_geography"
    return "active_other_measure_types"


def gap_codes(catalog_db: str, focused_db: str) -> list[str]:
    c = sqlite3.connect(Path(catalog_db).resolve().as_uri() + "?mode=ro", uri=True)
    f = sqlite3.connect(Path(focused_db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        active_leaf = {code for (code,) in c.execute(
            """SELECT DISTINCT c.code FROM commodities c
            WHERE c.active=1 AND c.suffix80=1
            AND NOT EXISTS (SELECT 1 FROM commodities child
                WHERE child.parent_sid=c.sid AND child.active=1)""")}
        focused = {code for (code,) in f.execute("SELECT code FROM tariffs")}
        return sorted(active_leaf - focused)
    finally:
        c.close()
        f.close()


def audit(catalog_db: str, focused_db: str, output: str, *,
          version: str, as_of: date, limit: int = 366, workers: int = 3,
          fetcher: Callable[[str, str], bytes] = fetch_csv) -> dict:
    """Check up to limit deterministic missing codes; all failures are explicit.

    Does not call the network until the pinned-version and input checks pass.
    A bounded subset is never treated as an exhaustive completed audit.
    """
    source_url(version, "0101210000")
    if not (1 <= workers <= 4 and 1 <= limit <= 50000):
        raise ValueError("Audit worker/limit bounds invalid")
    candidates = gap_codes(catalog_db, focused_db)
    # Prevent a misleading partial run from masking unexpected growth in gaps.
    selected = candidates[:limit]
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    collected: dict[str, tuple[bytes | None, str | None]] = {}

    def get(code: str):
        try:
            return code, fetcher(code, version), None
        except Exception as error:
            return code, None, f"{type(error).__name__}: {str(error)[:250]}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(get, code) for code in selected]
        for future in as_completed(futures):
            code, payload, error = future.result()
            collected[code] = payload, error

    with tempfile.TemporaryDirectory(dir=out.parent) as tmpdir:
        staging = Path(tmpdir) / "measures.db"
        db = sqlite3.connect(staging)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            counts: Counter = Counter()
            measure_rows = 0
            with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as report_stream:
                writer = csv.writer(report_stream)
                writer.writerow(["code", "status", "rows", "active_rows",
                                 "measure_types", "response_sha256", "error"])
                for code in selected:
                    payload, err = collected[code]
                    raw_rows: list[dict] = []
                    if err is None:
                        try:
                            raw_rows = parse_csv(payload, code)
                        except (UnicodeError, ValueError, csv.Error) as error:
                            err = f"{type(error).__name__}: {str(error)[:250]}"
                    status = "source_error" if err else classify(raw_rows, as_of)
                    digest = hashlib.sha256(payload).hexdigest() if payload is not None else None
                    db.execute("INSERT INTO source_requests VALUES (?,?,?,?,?)",
                               (code, digest, len(raw_rows) if not err else 0, status, err))
                    types = set()
                    active_rows = 0
                    if not err:
                        for index, row in enumerate(raw_rows):
                            state = effective_state(row, as_of)
                            active_rows += state == "active"
                            types.add(row["measure__type__id"])
                            db.execute(
                                "INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (code, index, row["measure__sid"], row["measure__type__id"],
                                 row["measure__geographical_area__id"],
                                 row["measure__duty_expression"],
                                 row["measure__additional_code__code"], row["measure__conditions"],
                                 row["measure__excluded_geographical_areas__ids"],
                                 row["measure__effective_start_date"],
                                 row["measure__effective_end_date"], state,
                                 json.dumps(row, ensure_ascii=False, sort_keys=True)))
                        measure_rows += len(raw_rows)
                    counts[status] += 1
                    writer.writerow([code, status, len(raw_rows) if not err else 0, active_rows,
                                     "|".join(sorted(types)), digest, err or ""])
            db.execute("INSERT INTO audit_meta VALUES ('version',?)", (version,))
            db.execute("INSERT INTO audit_meta VALUES ('as_of',?)", (as_of.isoformat(),))
            db.execute("INSERT INTO audit_meta VALUES ('generated_at',?)", (now,))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Measure SQLite failed integrity_check")
            if db.execute("SELECT COUNT(*) FROM source_requests").fetchone()[0] != len(selected):
                raise ValueError("Not all attempted source requests were accounted for")
        finally:
            db.close()
        staging.replace(out.with_suffix(".db"))
    report = {
        "kind": "gb-gap-measure-evidence-v1",
        "production_eligible": False,
        "source": "DBT pinned measures-on-declarable-commodities",
        "version": version, "as_of": as_of.isoformat(), "generated_at": now,
        "candidate_gap_codes": len(candidates),
        "attempted_codes": len(selected),
        "complete": len(selected) == len(candidates) and counts["source_error"] == 0,
        "source_error_codes": counts["source_error"],
        "retained_measure_rows": measure_rows,
        "classification": dict(sorted(counts.items())),
        "database_sha256": hashlib.sha256(out.with_suffix(".db").read_bytes()).hexdigest(),
        "warning": "Other measures do not prove a GB Third-country rate. Never infer zero duty or XI Other.",
        "csv": out.with_suffix(".csv").name,
        "sqlite": out.with_suffix(".db").name,
    }
    out.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--focused", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--limit", type=int, default=366)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--output", default="gb-gap-measures.json")
    args = parser.parse_args()
    report = audit(args.catalogue, args.focused, args.output,
                   version=args.version, as_of=date.fromisoformat(args.as_of),
                   limit=args.limit, workers=args.workers)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["complete"]:
        raise SystemExit("Measure evidence incomplete (some queries failed or limit truncated)")


if __name__ == "__main__":
    main()
