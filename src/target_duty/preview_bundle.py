#!/usr/bin/env python3
"""Make an explicitly *untrusted* shadow-preview transport bundle.

This ZIP is intended for manual/local desktop comparison only. SHA-256 detects
corruption but is NOT a signature. It cannot authorize production activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import zipfile
from pathlib import Path

MAX_SQLITE_BYTES = 40 * 1024 * 1024
REQUIRED = ("code", "description", "rate", "north_ireland_rate",
            "uk_rate_status", "ni_rate_status")


def make_bundle(db_path: str, metadata_path: str, output: str) -> dict:
    db = Path(db_path)
    meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    assert db.exists(), "Shadow database is missing"
    size = db.stat().st_size
    if size <= 0 or size > MAX_SQLITE_BYTES:
        raise ValueError("Shadow snapshot size outside preview limits")
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    if meta.get("file_hash") != "sha256:" + digest or meta.get("file_size") != size:
        raise ValueError("Source metadata does not match the shadow file")
    version = str(meta.get("version", ""))
    if not re.fullmatch(r"target-duty-[A-Za-z0-9._-]{1,115}", version):
        raise ValueError("Unexpected shadow data version")
    if (meta.get("source") or {}).get("measure_type") != "103":
        raise ValueError("Not GB third country duty source")
    if (meta.get("source") or {}).get("geographical_area") != "1011":
        raise ValueError("Not ERGA OMNES source")
    uri = db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Shadow SQLite integrity failure")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tariffs)")}
        if not set(REQUIRED).issubset(columns):
            raise ValueError("Shadow schema missing required fields")
        count = conn.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0]
        if count != meta.get("record_count") or count < 100:
            raise ValueError("Shadow record count mismatch")
        gb_verified = conn.execute(
            "SELECT COUNT(*) FROM tariffs WHERE uk_rate_status='verified'").fetchone()[0]
        gb_conditional = conn.execute(
            "SELECT COUNT(*) FROM tariffs WHERE uk_rate_status='conditional'").fetchone()[0]
        ni_untrusted = conn.execute(
            "SELECT COUNT(*) FROM tariffs WHERE ni_rate_status IN ('stale','pending')").fetchone()[0]
        if (gb_verified + gb_conditional != count or ni_untrusted != count):
            raise ValueError("Shadow contains unsupported or prematurely verified NI statuses")
        if (meta.get("quality_metrics") or {}).get("gb_verified") != gb_verified:
            raise ValueError("GB quality count mismatch")

    manifest = {
        "schema_version": 2,
        "channel": "shadow-preview",
        "release_id": version,
        "published_at": meta["timestamp"],
        "as_of": meta["source"]["effective_date"],
        "source": meta["source"],
        "artifact": {"name": "target-duty.db", "size": size, "sha256": digest},
        "quality": {"record_count": count, "gb_verified": gb_verified,
                    "gb_conditional": gb_conditional, "ni_unverified": ni_untrusted},
        "signed": False,
        "production_eligible": False
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        archive.write(db, "target-duty.db")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="target-duty.db")
    parser.add_argument("--metadata", default="target-duty.metadata.json")
    parser.add_argument("--output", default="target-duty-preview.zip")
    args = parser.parse_args()
    print(json.dumps(make_bundle(args.db, args.metadata, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
