"""Independent XI commodity coverage from official HMRC chapter nomenclature API.

The XI catalogue MUST NOT be copied from GB; Northern Ireland follows its own
goods nomenclature. Fetch the list of official chapters, then each chapter's
goods nomenclature entries with declarable flag and effective dates.

Output is a read-only shadow SQLite, not XI Other rates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_probe import ACCEPT, get_token

HOST = "https://api.trade-tariff.service.gov.uk/xi/api"
PUBLIC_HOST = "https://www.trade-tariff.service.gov.uk/xi/api"
CODE = re.compile(r"\d{10}\Z")
SCHEMA = """
CREATE TABLE chapters (
 chapter TEXT PRIMARY KEY, response_sha256 TEXT NOT NULL,
 source_rows INTEGER NOT NULL, loaded_at TEXT NOT NULL
);
CREATE TABLE nomenclature (
 chapter TEXT NOT NULL REFERENCES chapters(chapter),
 source_id TEXT NOT NULL, code TEXT NOT NULL, suffix TEXT,
 sid TEXT, description TEXT, declarable INTEGER,
 valid_from TEXT, valid_to TEXT, active INTEGER NOT NULL,
 raw_json TEXT NOT NULL,
 PRIMARY KEY(chapter,source_id)
);
CREATE INDEX idx_xi_code ON nomenclature(code, active, declarable);
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def active(attrs: dict, as_of: date) -> bool:
    start, end = attrs.get("validity_start_date"), attrs.get("validity_end_date")
    if not isinstance(start, str) or not start:
        return False
    try:
        first = date.fromisoformat(start[:10])
        last = date.fromisoformat(end[:10]) if end else None
    except (TypeError, ValueError):
        return False
    return first <= as_of and (last is None or as_of <= last)


def chapters(payload: dict) -> list[str]:
    collection = payload.get("data")
    if not isinstance(collection, list) or not collection:
        raise ValueError("HMRC XI chapters list missing or empty")
    values = []
    for node in collection:
        internal_id = str(node.get("id") or "")
        attrs = node.get("attributes") or {}
        goods_code = attrs.get("goods_nomenclature_item_id")
        # The actual HMRC chapter resource ID is a goods-nomenclature SID
        # (e.g. 27623), not the 2-digit chapter display code.
        if goods_code is not None:
            if not isinstance(goods_code, str) or not CODE.fullmatch(goods_code):
                raise ValueError("Malformed XI chapter goods nomenclature code")
            key = goods_code[:2]
        elif re.fullmatch(r"\d{1,2}", internal_id):
            # Fixtures and deliberately reduced canary chapter selections.
            key = internal_id.zfill(2)
        else:
            raise ValueError(f"Unexpected XI chapter resource without code: {internal_id!r}")
        if key == "00" or key in values:
            raise ValueError("Invalid or duplicate XI chapter")
        values.append(key)
    return sorted(values)


def records(chapter: str, payload: dict, as_of: date) -> list[dict]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise ValueError("XI goods nomenclature chapter result must be an array")
    if not re.fullmatch(r"\d{2}", chapter):
        raise ValueError("Invalid chapter")
    output = []
    keys = set()
    for item in items:
        attrs = item.get("attributes")
        rid = str(item.get("id") or "")
        if not isinstance(attrs, dict) or not rid or rid in keys:
            raise ValueError("Invalid/duplicate goods nomenclature source item")
        keys.add(rid)
        code = attrs.get("goods_nomenclature_item_id")
        if not isinstance(code, str) or not CODE.fullmatch(code) or code[:2] != chapter:
            raise ValueError(f"Off-chapter or malformed XI code: {code}")
        flag = attrs.get("declarable")
        if flag is not None and not isinstance(flag, bool):
            raise ValueError("XI declarability must be bool or null")
        # Never silently discard a nomenclature as inactive when the
        # official record has invalid/missing dates. Such a record might
        # be an active declarable code and would make full-source
        # completeness falsely pass.
        raw_start, raw_end = attrs.get("validity_start_date"), attrs.get("validity_end_date")
        if not isinstance(raw_start,str) or not raw_start:
            raise ValueError(f"XI nomenclature missing validity start: {code}")
        try:
            valid_from=date.fromisoformat(raw_start[:10])
            valid_to=date.fromisoformat(raw_end[:10]) if raw_end else None
        except (TypeError,ValueError) as exc:
            raise ValueError(f"Malformed XI nomenclature validity date: {code}") from exc
        if raw_end is not None and not isinstance(raw_end,str):
            raise ValueError(f"Malformed XI nomenclature validity end: {code}")
        if valid_to is not None and valid_to < valid_from:
            raise ValueError(f"XI nomenclature validity interval reversed: {code}")
        output.append({
            "chapter": chapter, "source_id": rid, "code": code,
            "suffix": attrs.get("producline_suffix"),
            "sid": str(attrs.get("goods_nomenclature_sid") or ""),
            "description": attrs.get("description"),
            "declarable": None if flag is None else int(flag),
            "valid_from": attrs.get("validity_start_date"),
            "valid_to": attrs.get("validity_end_date"),
            "active": int(active(attrs, as_of)),
            "raw_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
        })
    return output


def fetch(path: str, token: str | None, *, public: bool = False,
          retries: int = 2) -> bytes:
    if not (path == "/chapters" or re.fullmatch(
            r"/goods_nomenclatures/chapter/\d{2}", path)):
        raise ValueError("Unsupported XI endpoint")
    url = (PUBLIC_HOST if public else HOST) + path
    headers = {"Accept": ACCEPT, "User-Agent": "liao-xi-catalogue-shadow/1.0"}
    if not public:
        if not token:
            raise ValueError("Official XI access requires managed HMRC OAuth token")
        headers["Authorization"] = "Bearer " + token
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=60) as response:
                data = response.read(20_000_001)
                if len(data) > 20_000_000:
                    raise ValueError("XI chapter response exceeds safety limit")
                if response.status != 200:
                    raise ValueError("Unexpected XI chapter response")
                return data
        except urllib.error.HTTPError as ex:
            if ex.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except urllib.error.URLError:
            if attempt == retries:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("Unexpected XI fetch loop state")


def build(chapter_list: dict, chapter_payloads: dict[str, dict], output: str,
          *, as_of: date, public: bool = False,
          full_managed_listing: bool = False,
          official_listed_total: int | None = None) -> dict:
    expected = chapters(chapter_list)
    if full_managed_listing and (public or official_listed_total != len(expected)):
        raise ValueError("Full managed XI inventory must match original official chapter list")
    if set(chapter_payloads) != set(expected):
        missing = sorted(set(expected)-set(chapter_payloads))
        extra = sorted(set(chapter_payloads)-set(expected))
        raise ValueError(f"XI chapter coverage mismatch missing={missing} extra={extra}")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        tmp = Path(folder) / "xi-catalogue.db"
        db = sqlite3.connect(tmp)
        total, active_decl, unknown = 0, 0, 0
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            for chapter in expected:
                doc = chapter_payloads[chapter]
                normalized = json.dumps(doc,sort_keys=True,ensure_ascii=False).encode()
                data = records(chapter,doc,as_of)
                db.execute("INSERT INTO chapters VALUES (?,?,?,?)",
                           (chapter,hashlib.sha256(normalized).hexdigest(),
                            len(data),datetime.now(timezone.utc).isoformat()))
                for row in data:
                    db.execute("""INSERT INTO nomenclature
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                      tuple(row[key] for key in
                         ("chapter","source_id","code","suffix","sid","description",
                          "declarable","valid_from","valid_to","active","raw_json")))
                    total += 1
                    active_decl += row["declarable"] == 1 and row["active"] == 1
                    unknown += row["declarable"] is None
            db.execute("INSERT INTO metadata VALUES ('as_of',?)",
                       (as_of.isoformat(),))
            db.execute("INSERT INTO metadata VALUES ('listing_scope',?)",
                       ("managed_full" if full_managed_listing else "partial_or_fixture",))
            db.execute("INSERT INTO metadata VALUES ('listed_chapters',?)",
                       (str(official_listed_total or len(expected)),))
            db.execute("INSERT INTO metadata VALUES ('production_eligible','false')")
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("XI catalogue integrity failure")
        finally:
            db.close()
        tmp.replace(destination)
    summary = {
        "kind": "xi-official-nomenclature-shadow-v1",
        "production_eligible": False, "public_canary": public,
        "as_of": as_of.isoformat(), "chapters_complete": len(expected),
        "nomenclature_rows": total, "active_declarable_entries": active_decl,
        "unknown_declarability_entries": unknown,
        "other_semantics_verified": False, "measures_imported": 0,
        "atomic_version_proven": False,
        "listing_scope": "managed_full" if full_managed_listing else "partial_or_fixture",
        "official_listed_chapters": official_listed_total,
        "sqlite_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "warning": "Independent XI code coverage is not XI Other rates; verify UKIMS/origin conditions.",
    }
    destination.with_suffix(".json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output", default="xi-catalogue-shadow.db")
    parser.add_argument("--fixtures", help="JSON with chapters and chapter_payloads (offline replay)")
    parser.add_argument("--public-canary", action="store_true")
    parser.add_argument("--max-chapters", type=int, default=0,
                        help="Canary cap; 0 means all officially enumerated chapters")
    parser.add_argument("--min-chapter-interval", type=float, default=1.0,
                        help="Delay between managed official chapter requests")
    args = parser.parse_args()
    if args.max_chapters < 0 or args.max_chapters > 99:
        raise ValueError("Invalid canary cap")
    if args.min_chapter_interval < 0.5 or args.min_chapter_interval > 60:
        raise ValueError("XI chapter API interval must be 0.5-60 seconds")
    if args.public_canary and not 1 <= args.max_chapters <= 5:
        raise ValueError("Public XI API limited to at most five canary chapters")
    if args.fixtures:
        raw = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
        listing, payloads = raw["chapters"], raw["chapter_payloads"]
        official_total = None
    else:
        token = None if args.public_canary else get_token(
            os.getenv("HMRC_CLIENT_ID",""), os.getenv("HMRC_CLIENT_SECRET",""))
        listing = json.loads(fetch("/chapters", token, public=args.public_canary))
        all_chapters = chapters(listing)
        official_total = len(all_chapters)
        if args.max_chapters:
            selected = all_chapters[:args.max_chapters]
            listing = {"data": [{"id":c} for c in selected]}
        else:
            selected = all_chapters
        payloads = {}
        for index, chapter in enumerate(selected):
            if index:
                time.sleep(args.min_chapter_interval)
            payloads[chapter] = json.loads(fetch(
                f"/goods_nomenclatures/chapter/{chapter}",token,public=args.public_canary))
    result = build(listing,payloads,args.output,
                   as_of=date.fromisoformat(args.as_of),public=args.public_canary,
                   full_managed_listing=(not args.fixtures and not args.public_canary
                                         and args.max_chapters == 0),
                   official_listed_total=official_total)
    result["partial_canary"] = bool(args.max_chapters)
    # The output JSON must match the CLI's actual coverage flag.
    Path(args.output).with_suffix(".json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
