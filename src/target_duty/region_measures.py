"""Canonical GB/XI measure evidence store (isolated, NEVER a tariffs.db).

GB input is the pinned all-measure gap audit SQLite, not a complete GB export.
XI input is the sampled HMRC audit JSON, not verified NI Other.
Source scope and confidence remain explicit; no implicit union across regions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
CODE = re.compile(r"\d{10}\Z")
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE snapshots (
 region TEXT PRIMARY KEY CHECK(region IN ('GB','XI')),
 source_kind TEXT NOT NULL,
 source_version TEXT NOT NULL,
 as_of TEXT NOT NULL,
 scope TEXT NOT NULL,
 production_eligible INTEGER NOT NULL DEFAULT 0 CHECK(production_eligible=0)
);
CREATE TABLE requests (
 region TEXT NOT NULL REFERENCES snapshots(region),
 code TEXT NOT NULL,
 response_sha256 TEXT,
 status TEXT NOT NULL,
 row_count INTEGER NOT NULL CHECK(row_count>=0),
 error TEXT,
 PRIMARY KEY(region,code)
);
CREATE TABLE measures (
 region TEXT NOT NULL,
 code TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 measure_sid TEXT,
 measure_type TEXT,
 geography TEXT,
 expression TEXT,
 additional_code TEXT,
 conditions TEXT,
 excluded_areas TEXT,
 effective_start TEXT,
 effective_end TEXT,
 effective_status TEXT NOT NULL,
 raw_json TEXT NOT NULL,
 PRIMARY KEY(region,code,ordinal),
 FOREIGN KEY(region,code) REFERENCES requests(region,code)
);
CREATE INDEX idx_canonical_measure_lookup
 ON measures(region,code,measure_type,geography);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _db_readonly(path: str) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise ValueError("Missing evidence database: " + path)
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _hex(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError("Source SHA-256 must be exactly 64 hex characters")
    return value.lower()


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _measure_insert(db: sqlite3.Connection, region: str, code: str,
                    ordinal: int, sid: str | None, kind: str | None,
                    geography: str | None, expression: str | None,
                    additional: str | None, conditions: str | None,
                    excluded: str | None, start: str | None, end: str | None,
                    status: str, raw: dict) -> None:
    if not CODE.fullmatch(code) or ordinal < 0:
        raise ValueError("Invalid measure commodity code or ordinal")
    if not isinstance(raw, dict):
        raise ValueError("Each source measure must have raw object evidence")
    db.execute("""INSERT INTO measures
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (region, code, ordinal, sid, kind, geography, expression, additional,
       conditions, excluded, start, end, status,
       json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))


def import_gb(db: sqlite3.Connection, evidence_path: str) -> dict:
    source = _db_readonly(evidence_path)
    try:
        info = dict(source.execute("SELECT key,value FROM audit_meta"))
        version, as_of = info["version"], _day(info["as_of"])
        if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+)*", version):
            raise ValueError("Unpinned GB evidence")
        db.execute("""INSERT INTO snapshots VALUES
            ('GB',?,?,?,'gap-only-366-candidates',0)""",
            ("DBT-measures-on-declarable-commodities", version, as_of))
        requests = 0
        count = 0
        statuses = Counter()
        for code, digest, rows, status, error in source.execute(
                "SELECT code,response_sha256,row_count,status,error FROM source_requests ORDER BY code"):
            if not isinstance(code, str) or not CODE.fullmatch(code):
                raise ValueError("Malformed GB request code")
            if error or status == "source_error":
                raise ValueError(f"GB source request unresolved: {code}")
            if not isinstance(rows, int) or rows < 0:
                raise ValueError("Invalid GB source measure count")
            if rows and digest is None:
                raise ValueError("Nonempty GB response lacks source hash")
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,NULL)",
                       ("GB", code, _hex(digest, optional=True), status, rows))
            requests += 1
            statuses[status] += 1
            found = 0
            for (ordinal, sid, kind, geo, rate, additional, conditions,
                 excluded, start, end, active, raw_json) in source.execute(
                """SELECT ordinal,measure_sid,measure_type,geographical_area,
                          expression,additional_code,conditions,excluded_areas,
                          starts,ends,effective_state,raw_json
                   FROM measures WHERE code=? ORDER BY ordinal""", (code,)):
                raw = json.loads(raw_json)
                if not isinstance(raw, dict) or raw.get("commodity__code") != code:
                    raise ValueError("GB raw evidence and code disagree")
                _measure_insert(db, "GB", code, ordinal, sid, kind, geo, rate,
                                additional, conditions, excluded, start, end, active, raw)
                found += 1
                count += 1
            if found != rows:
                raise ValueError(f"GB row count mismatch at {code}: {found}!={rows}")
        if not requests:
            raise ValueError("Empty GB audit is not a valid snapshot")
        return {"region": "GB", "requests": requests, "measure_rows": count,
                "statuses": dict(statuses), "scope": "gap-only-366-candidates"}
    finally:
        source.close()


def import_xi(db: sqlite3.Connection, path: str) -> dict:
    source_bytes = Path(path).read_bytes()
    doc = json.loads(source_bytes)
    if doc.get("kind") != "xi-third-country-duty-review-only":
        raise ValueError("Expected original XI audit JSON, not review gate summary")
    if doc.get("production_eligible") is not False:
        raise ValueError("XI audit must never certify production")
    as_of = _day(doc["as_of"])
    entries = doc.get("entries")
    if not isinstance(entries, list) or doc.get("coverage") != len(entries) or not entries:
        raise ValueError("XI review coverage is incomplete")
    db.execute("""INSERT INTO snapshots VALUES
       ('XI','HMRC-XI-V2','sample-review',?,'sample-not-other-certified',0)""",
       (as_of,))
    seen: set[str] = set()
    count = 0
    statuses = Counter()
    for item in entries:
        code = item.get("code")
        if not isinstance(code, str) or not CODE.fullmatch(code) or code in seen:
            raise ValueError("Invalid or repeated XI sampled code")
        seen.add(code)
        rows = item.get("candidate_measures")
        if not isinstance(rows, list):
            raise ValueError("XI measure list must be present for every entry")
        sha = item.get("response_sha256")
        if rows:
            _hex(sha)
            if item.get("source") != "HMRC-XI-v2" or item.get("effective_date_checked") != as_of:
                raise ValueError("XI sample source/date evidence missing")
        # Failed requests may lack a response hash, and MUST stay explicit.
        status = str(item.get("verdict") or "unresolved")
        if sha is None and rows:
            raise ValueError("XI measure source is missing response fingerprint")
        db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)",
                   ("XI", code, _hex(sha, optional=True), status, len(rows),
                    None if sha else "missing-source-evidence"))
        statuses[status] += 1
        for ordinal, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("XI measure has wrong structure")
            expr = row.get("duty_expression")
            _measure_insert(
                db, "XI", code, ordinal, str(row.get("measure_id") or ""),
                row.get("measure_type"), row.get("geographical_area_id"),
                expr if isinstance(expr, str) else None,
                "present" if row.get("additional_code_present") else None,
                "present" if row.get("has_conditions") else None,
                "present" if row.get("excluded_countries_present") else None,
                row.get("effective_start_date"), row.get("effective_end_date"),
                "review_only", row)
            count += 1
    db.execute("INSERT INTO meta VALUES ('xi_source_json_sha256',?)",
               (hashlib.sha256(source_bytes).hexdigest(),))
    return {"region": "XI", "requests": len(seen), "measure_rows": count,
            "statuses": dict(statuses), "scope": "sample-not-other-certified"}


def build(output: str, *, gb: str | None = None, xi: str | None = None) -> dict:
    if not gb and not xi:
        raise ValueError("At least one independent source is required")
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        stage = Path(tmp) / "measure-evidence.sqlite"
        db = sqlite3.connect(stage)
        try:
            db.executescript(SCHEMA)
            results = []
            if gb:
                results.append(import_gb(db, gb))
            if xi:
                results.append(import_xi(db, xi))
            db.execute("INSERT INTO meta VALUES ('production_eligible','false')")
            db.execute("INSERT INTO meta VALUES ('created_at',?)",
                       (datetime.now(timezone.utc).isoformat(),))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Canonical source SQLite integrity failure")
            # Crucial: do not allow rows to cross region boundaries.
            if db.execute("""SELECT COUNT(*) FROM measures m
                LEFT JOIN requests r ON r.region=m.region AND r.code=m.code
                WHERE r.code IS NULL""").fetchone()[0]:
                raise ValueError("Orphaned canonical measure")
        finally:
            db.close()
        stage.replace(dest)
    result = {
        "kind": "gb-xi-separate-canonical-evidence",
        "production_eligible": False,
        "gb_complete": False, "xi_other_verified": False,
        "regions": results,
        "sqlite_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "sqlite_bytes": dest.stat().st_size,
    }
    dest.with_suffix(".json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def lookup(path: str, region: str, code: str) -> dict:
    if region not in ("GB", "XI") or not CODE.fullmatch(code):
        raise ValueError("Explicit GB/XI and valid 10-digit code required")
    db = _db_readonly(path)
    try:
        record = db.execute(
            "SELECT scope,as_of FROM snapshots WHERE region=?", (region,)).fetchone()
        if record is None:
            return {"region": region, "code": code, "found": False,
                    "reason": "region_not_ingested", "measures": []}
        request = db.execute(
            "SELECT status,row_count,error FROM requests WHERE region=? AND code=?",
            (region, code)).fetchone()
        if request is None:
            return {"region": region, "code": code, "found": False,
                    "reason": "outside_evidence_scope", "scope": record[0], "measures": []}
        rows = list(db.execute(
            """SELECT measure_sid,measure_type,geography,expression,
                      additional_code,conditions,effective_status,raw_json
               FROM measures WHERE region=? AND code=? ORDER BY ordinal""",
            (region, code)))
        return {
            "region": region, "code": code, "found": True,
            "scope": record[0], "as_of": record[1],
            "status": request[0], "evidence_error": request[2],
            "row_count": request[1],
            "measures": [{"sid": r[0], "type": r[1], "geography": r[2],
                          "duty_expression": r[3], "additional_code": r[4],
                          "conditions": r[5], "effective_status": r[6],
                          "raw": json.loads(r[7])} for r in rows],
            "production_eligible": False,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="cmd", required=True)
    create = commands.add_parser("build")
    create.add_argument("--gb", help="Pinned GB gap-measure evidence SQLite")
    create.add_argument("--xi", help="Original XI review-only JSON sample")
    create.add_argument("--output", default="region-measures-shadow.db")
    read = commands.add_parser("query")
    read.add_argument("--database", required=True)
    read.add_argument("--region", choices=("GB", "XI"), required=True)
    read.add_argument("--code", required=True)
    args = parser.parse_args()
    result = (build(args.output, gb=args.gb, xi=args.xi) if args.cmd == "build"
              else lookup(args.database, args.region, args.code))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
