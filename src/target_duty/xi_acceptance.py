"""Fail-closed XI snapshot audit and explicit 'Other origin' interpretation.

This is evidence QA, not a tax calculation or a verified NI "Other" tariff.
It checks independent XI nomenclature coverage and replays every stored
compressed official commodity JSON for exact checksum + measure parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import zlib
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .xi_measure_crawl import candidates, parse

CODE = re.compile(r"\d{10}\Z")
ISO = re.compile(r"[A-Z]{2}\Z")
THIRD_COUNTRY_GROUP = frozenset(("103", "105"))


def _read_only(path: str) -> sqlite3.Connection:
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"Missing XI evidence: {path}")
    return sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)


def interpret_other_origin(option: str, *, selected_country: str | None = None) -> dict:
    """UK Trade Tariff frontend's 'OTHER' is an origin selector, not a rate."""
    if option == "OTHER":
        if not selected_country or not ISO.fullmatch(selected_country):
            return {"status": "origin_required", "origin": None,
                    "duty_rate": None, "production_eligible": False}
        origin = selected_country
    elif ISO.fullmatch(option):
        origin = option
    else:
        raise ValueError("Unknown country-of-origin selection")
    return {"status": "origin_specific_official_filter_required",
            "origin": origin, "duty_rate": None,
            "candidate_measure_types": sorted(THIRD_COUNTRY_GROUP),
            "production_eligible": False}


def decompress_checked(blob: bytes, *, max_bytes: int = 8_000_000) -> bytes:
    if not isinstance(blob, bytes) or not blob:
        raise ValueError("Missing compressed response")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(blob, max_bytes + 1)
    if len(raw) > max_bytes or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Oversized, truncated or trailing compressed XI response")
    return raw


def evaluate(catalogue_db: str, measure_db: str, *, min_chapters: int = 90,
             max_unchecked: int = 0, strict: bool = False) -> dict:
    if not 1 <= min_chapters <= 99 or max_unchecked < 0:
        raise ValueError("Invalid XI acceptance gate thresholds")
    with _read_only(catalogue_db) as catalog, _read_only(measure_db) as measures:
        metadata = dict(catalog.execute("SELECT key,value FROM metadata"))
        config = dict(measures.execute("SELECT key,value FROM config"))
        as_of = date.fromisoformat(metadata["as_of"]).isoformat()
        if config.get("region") != "XI" or config.get("as_of") != as_of:
            raise ValueError("XI snapshot region/as_of mismatch")
        if config.get("catalogue_sha256") != hashlib.sha256(
                Path(catalogue_db).read_bytes()).hexdigest():
            raise ValueError("XI catalogue changed since measure collection")
        if config.get("production_eligible") != "false" or config.get("other_semantics_verified") != "false":
            raise ValueError("XI source has illegal production or Other certification marker")
        chapter_total = catalog.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        listing_scope = metadata.get("listing_scope", "unknown")
        listed_chapters = int(metadata.get("listed_chapters", "0"))
        unknown = catalog.execute("""SELECT COUNT(*) FROM nomenclature
                WHERE active=1 AND declarable IS NULL""").fetchone()[0]
        codes = candidates(catalogue_db)
        expected = set(codes)
        requested = set()
        source_failures = []
        measure_count = 0
        response_bytes = 0
        third_types: Counter = Counter()
        for code, status, fingerprint, size, err, compressed in measures.execute(
                """SELECT code,status,response_sha256,bytes,error,compressed_body
                   FROM requests ORDER BY code"""):
            if code not in expected or code in requested:
                raise ValueError(f"XI request outside independent official catalogue: {code}")
            requested.add(code)
            if status != "ok":
                if status != "source_error":
                    raise ValueError(f"Unsupported XI source status: {code}/{status}")
                if measures.execute(
                    "SELECT 1 FROM measures WHERE code=? LIMIT 1",(code,)
                ).fetchone():
                    raise ValueError(f"XI failed request incorrectly contains measures: {code}")
                source_failures.append({"code":code,"error":(err or "unspecified")[:250]})
                continue
            raw = decompress_checked(compressed)
            response_bytes += len(raw)
            if (size != len(raw) or not isinstance(fingerprint, str)
                    or hashlib.sha256(raw).hexdigest() != fingerprint):
                raise ValueError(f"XI response bytes/hash mismatch: {code}")
            original = parse(raw, code)
            persisted = list(measures.execute(
                """SELECT ordinal,measure_id,measure_type,geography_id,
                          duty_expression,effective_start,effective_end,raw_json
                   FROM measures WHERE code=? ORDER BY ordinal""", (code,)))
            if len(original) != len(persisted):
                raise ValueError(f"XI stored import measures mismatch: {code}")
            for ordinal, (source, stored) in enumerate(zip(original, persisted)):
                actual = (ordinal,source["measure_id"],source["measure_type"],
                          source["geography_id"],source["duty_expression"],
                          source["effective_start"],source["effective_end"],
                          source["raw_json"])
                if actual != stored:
                    raise ValueError(f"XI raw vs persisted measure discrepancy: {code}/{ordinal}")
                if source["measure_type"] in THIRD_COUNTRY_GROUP:
                    third_types[str(source["measure_type"])] += 1
            measure_count += len(original)
        missing = sorted(expected-requested)
        complete = not missing and not source_failures and len(requested)==len(expected)
        # A full pass of ALL XI chapter records is not equivalent to a proven
        # atomic official snapshot or a verified country-specific tax rate.
        catalogue_scope_complete = (
            chapter_total >= min_chapters and unknown == 0
            and listing_scope == "managed_full"
            and listed_chapters == chapter_total
        )
        checked = complete and catalogue_scope_complete
        report = {
            "kind": "xi-independent-catalogue-raw-measure-acceptance-v1",
            "production_eligible": False,
            "other_semantics_verified": False,
            "atomic_official_snapshot_verified": False,
            "date": as_of,
            "official_chapters_collected": chapter_total,
            "listing_scope": listing_scope,
            "official_listed_chapters": listed_chapters,
            "minimum_chapters": min_chapters,
            "unknown_active_declarability": unknown,
            "active_declarable_codes": len(expected),
            "requests_recorded": len(requested),
            "missing_codes": len(missing),
            "missing_samples": missing[:25],
            "request_failures": len(source_failures),
            "failure_samples": source_failures[:25],
            "replayed_source_measures": measure_count,
            "replayed_response_bytes": response_bytes,
            "third_country_measure_types": dict(third_types),
            "independent_catalogue_scope_ok": catalogue_scope_complete,
            "all_collected_measure_responses_verified": complete,
            "readiness": "further_business_validation_required" if checked else "source_coverage_incomplete",
            "notes": (
                "103 and 105 are frontend third-country measure groups; OTHER "
                "is also an origin-country selection sentinel. Even full raw "
                "coverage does not certify NI Other, conditions, or payable duty."
            )
        }
        if strict and (not checked or len(missing)+len(source_failures)>max_unchecked):
            raise ValueError("XI evidence does not meet strict audit coverage")
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalogue",required=True)
    p.add_argument("--measures",required=True)
    p.add_argument("--min-chapters",type=int,default=90)
    p.add_argument("--strict",action="store_true")
    p.add_argument("--output",default="xi-coverage-audit.json")
    args=p.parse_args()
    report=evaluate(args.catalogue,args.measures,min_chapters=args.min_chapters,
                    strict=args.strict)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                                 encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
