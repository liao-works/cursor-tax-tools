"""Lossless GB full expanded-measure importer, shadow only.

DBT's measures-on-declarable-commodities table expands applicable measures
to each declarable commodity. Download its entire *pinned version* via CSV
streaming and keep EVERY measure row. No 103/1011 prefilter, no guessed duty.
The source omits other import taxes (e.g. VAT) and quota balances.
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
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .catalog import optional
from .gap_measures import REQUIRED, effective_state
from .region_measures import SCHEMA, _measure_insert

ENDPOINT = ("https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/"
            "versions/{version}/tables/measures-on-declarable-commodities/data?format=csv")
VERSION = re.compile(r"v[0-9]+(?:\.[0-9]+)*\Z")
CODE = re.compile(r"\d{10}\Z")


def source_url(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("Full GB source must use an immutable pinned DBT version")
    return ENDPOINT.format(version=version)


def download(version: str, destination: str, *, max_bytes: int = 2_000_000_000) -> dict:
    """Bounded full export download, atomic response; never use latest alias."""
    req = urllib.request.Request(source_url(version), headers={
        "Accept": "text/csv", "User-Agent": "liao-full-gb-shadow/1.0"})
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "full-export.csv"
        with urllib.request.urlopen(req, timeout=240) as reply, stage.open("wb") as stream:
            if reply.status != 200:
                raise ValueError(f"Unexpected DBT response: {reply.status}")
            resolved = reply.geturl()
            if f"/versions/{version}/" not in resolved:
                raise ValueError("DBT redirected to another version")
            while block := reply.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise ValueError("Official full measures export exceeds safety byte limit")
                stream.write(block)
                digest.update(block)
        if size == 0:
            raise ValueError("DBT full measures export is empty")
        stage.replace(target)
    return {"bytes": size, "sha256": digest.hexdigest(),
            "source_url": source_url(version), "resolved_url": resolved}


def _catalogue(path: str, version: str, as_of: date) -> set[str]:
    file = Path(path)
    meta_path = file.with_suffix(".metadata.json")
    if not meta_path.is_file():
        raise ValueError("Pinned commodity catalogue metadata required")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("resolved_version") != version or meta.get("as_of") != as_of.isoformat():
        raise ValueError("Full GB export and commodity catalogue source/as_of mismatch")
    db = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        codes = {code for (code,) in db.execute(
            """SELECT DISTINCT c.code FROM commodities c
            WHERE c.active=1 AND c.suffix80=1
            AND NOT EXISTS (SELECT 1 FROM commodities child
              WHERE child.parent_sid=c.sid AND child.active=1)""")}
    finally:
        db.close()
    if not codes:
        raise ValueError("Empty GB official active-leaf catalogue")
    return codes


def _focused(path: str) -> set[str]:
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {code for (code,) in db.execute("SELECT code FROM tariffs")}
    finally:
        db.close()


def build(csv_path: str, catalogue: str, output: str, *, version: str,
          as_of: date, focused: str | None = None,
          min_rows: int = 100_000) -> dict:
    """Stream complete expanded CSV -> isolated region measure evidence DB.

    Active 103/1011 codes in the complete export must cover all focused codes.
    Current commodity codes absent entirely remain explicit unresolved records,
    never "0%". Every source row is retained regardless of effective date.
    """
    source_url(version)
    if min_rows < 1:
        raise ValueError("min_rows must be positive")
    active_leaf = _catalogue(catalogue, version, as_of)
    focused_codes = _focused(focused) if focused else set()
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with Path(csv_path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    source_sha = digest.hexdigest()

    with tempfile.TemporaryDirectory(dir=dest.parent) as work:
        stage = Path(work) / "gb-full.db"
        db = sqlite3.connect(stage)
        try:
            db.executescript(SCHEMA)
            db.execute("""INSERT INTO snapshots
                VALUES ('GB','DBT-expanded-measures',?,?,
                   'all-DBT-expanded-measures-not-all-import-taxes',0)""",
                (version, as_of.isoformat()))
            num_rows = 0
            types = Counter()
            current_103 = set()
            seen: dict[str, int] = {}
            with Path(csv_path).open("r", newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                fields = set(reader.fieldnames or [])
                absent = REQUIRED - fields
                if absent:
                    raise ValueError(f"Full GB export missing official fields: {sorted(absent)}")
                for row in reader:
                    if None in row:
                        raise ValueError(f"Malformed CSV row at position {num_rows+2}")
                    code = optional(row["commodity__code"])
                    sid = optional(row["measure__sid"])
                    if not code or not CODE.fullmatch(code) or not sid or not sid.isdecimal():
                        raise ValueError(f"Invalid code/measure SID at row {num_rows+2}")
                    num_rows += 1
                    if num_rows > 15_000_000:
                        raise ValueError("Full export row safety limit exceeded")
                    ordinal = seen.get(code, 0)
                    if ordinal == 0:
                        db.execute("INSERT INTO requests VALUES (?,?,?,?,?,NULL)",
                                   ("GB", code, source_sha, "has_expanded_measures", 0))
                    seen[code] = ordinal + 1
                    state = effective_state(row, as_of)
                    kind = optional(row["measure__type__id"])
                    geo = optional(row["measure__geographical_area__id"])
                    types[str(kind)] += 1
                    if state == "active" and kind == "103" and geo == "1011":
                        current_103.add(code)
                    _measure_insert(
                        db, "GB", code, ordinal, sid, kind, geo,
                        row["measure__duty_expression"],
                        row["measure__additional_code__code"],
                        row["measure__conditions"],
                        row["measure__excluded_geographical_areas__ids"],
                        row["measure__effective_start_date"],
                        row["measure__effective_end_date"], state, row)
                    if num_rows % 20_000 == 0:
                        db.commit()
            if num_rows < min_rows:
                raise ValueError(f"Full source rows too few: {num_rows} < {min_rows}")
            missing_focused = sorted(focused_codes - current_103)
            if missing_focused:
                raise ValueError(
                    f"Official same-version 103/1011 parity failed: "
                    f"{len(missing_focused)} focused codes missing; "
                    f"examples={missing_focused[:12]}")
            for code, count in seen.items():
                db.execute("UPDATE requests SET row_count=? WHERE region='GB' AND code=?",
                           (count, code))
            missing_leaf = active_leaf - seen.keys()
            for code in sorted(missing_leaf):
                db.execute("INSERT INTO requests VALUES (?,?,?,?,?,NULL)",
                           ("GB", code, source_sha,
                            "no_expanded_measure_rows_unverified", 0))
            db.execute("INSERT INTO meta VALUES ('source_sha256',?)", (source_sha,))
            db.execute("INSERT INTO meta VALUES ('catalogue_active_leaf_count',?)",
                       (str(len(active_leaf)),))
            db.execute("INSERT INTO meta VALUES ('imported_measure_rows',?)",
                       (str(num_rows),))
            db.commit()
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            if db.execute("SELECT COUNT(*) FROM measures").fetchone()[0] != num_rows:
                raise ValueError("Source measure row count discrepancy")
            if db.execute("SELECT SUM(row_count) FROM requests").fetchone()[0] != num_rows:
                raise ValueError("Source requests do not sum to measure rows")
        finally:
            db.close()
        stage.replace(dest)
    report = {
        "kind": "gb-full-expanded-measures-shadow",
        "production_eligible": False,
        "version": version, "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv_sha256": source_sha,
        "source_rows": num_rows,
        "source_commodity_codes": len(seen),
        "official_active_leaves": len(active_leaf),
        "active_leaves_without_any_export_rows": len(missing_leaf),
        "missing_leaf_samples": sorted(missing_leaf)[:100],
        "focused_codes": len(focused_codes),
        "active_103_1011_codes": len(current_103),
        "focused_parity": not missing_focused,
        "measure_types": dict(types.most_common()),
        "database_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "database_bytes": dest.stat().st_size,
        "complete_within_dbt_export_scope": not missing_focused,
        "all_import_duties_verified": False,
        "ni_other_verified": False,
        "warning": "Expanded DBT measures are not a verified payable GB duty or NI Other.",
    }
    dest.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--csv", help="Replay pinned DBT source download, no network")
    parser.add_argument("--focused", help="Same-version existing GB 103/1011 baseline")
    parser.add_argument("--version", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--min-rows", type=int, default=100_000)
    parser.add_argument("--output", default="gb-full-measures-shadow.db")
    args = parser.parse_args()
    if args.csv:
        report = build(args.csv, args.catalogue, args.output,
                       version=args.version, as_of=date.fromisoformat(args.as_of),
                       focused=args.focused, min_rows=args.min_rows)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            downloaded = Path(tmp) / "source.csv"
            download(args.version, str(downloaded))
            report = build(str(downloaded), args.catalogue, args.output,
                           version=args.version, as_of=date.fromisoformat(args.as_of),
                           focused=args.focused, min_rows=args.min_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
