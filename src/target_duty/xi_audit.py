#!/usr/bin/env python3
"""Bounded, stratified XI 'Other' audit. NEVER edits any tariff database.

Compares the legacy NI page-derived value with an HMRC XI type-103/ERGA OMNES
*candidate* extracted from the official duty-expression relationship. Old
values are a reference only, not authoritative truth. No automatic promotion.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
import urllib.error
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .xi_probe import fetch_public_commodity, fetch_commodity, summarize_response
from .reconcile import classify_rate


def select_codes(legacy_db: str, limit: int) -> list[tuple[str, str]]:
    if limit < 1 or limit > 100:
        raise ValueError("Audit limit must be within 1..100")
    conn = sqlite3.connect(Path(legacy_db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT code,north_ireland_rate FROM tariffs "
            "WHERE LENGTH(code)=10 AND north_ireland_rate IS NOT NULL "
            "AND TRIM(north_ireland_rate)!='' ORDER BY code").fetchall()
    finally:
        conn.close()
    # Round-robin across chapters. Avoid only testing the first commodity family.
    chapters: dict[str, list[tuple[str, str]]] = {}
    for code, rate in rows:
        if code.isdigit():
            chapters.setdefault(code[:2], []).append((code, rate))
    selected = []
    while len(selected) < limit and chapters:
        for chapter in list(chapters):
            if not chapters[chapter]:
                del chapters[chapter]
                continue
            selected.append(chapters[chapter].pop(0))
            if len(selected) >= limit:
                break
    return selected


def audit(legacy_db: str, output: str, *, limit: int, as_of: date,
          fetcher: Callable[[str, date], dict], delay: float = 1.0) -> dict:
    if delay < 0 or delay > 30:
        raise ValueError("Delay must be between zero and 30 seconds")
    codes = select_codes(legacy_db, limit)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    results = []
    for index, (code, legacy) in enumerate(codes):
        if index and delay:
            time.sleep(delay)
        try:
            payload = fetcher(code, as_of)
            report = summarize_response(payload, code, as_of)
            candidate = report["candidate_rate"]
            if candidate:
                category = classify_rate(legacy, candidate)
                verdict = "candidate_review"
            else:
                category = "no_unambiguous_candidate"
                verdict = "needs_measure_review"
            row = {
                "code": code, "legacy_ni_rate": legacy,
                "xi_candidate": candidate, "verdict": verdict,
                "comparison": category, "candidate_measures": report["candidates"]
            }
        except urllib.error.HTTPError as error:
            if error.code in (429, 503):
                # Do not hammer HMRC once rate limiting or service issues are seen.
                raise RuntimeError(f"XI source unavailable (HTTP {error.code}); audit halted") from error
            row = {
                "code": code, "legacy_ni_rate": legacy, "xi_candidate": None,
                "verdict": "http_error", "comparison": f"HTTP {error.code}",
                "candidate_measures": []
            }
        except Exception as error:
            row = {
                "code": code, "legacy_ni_rate": legacy, "xi_candidate": None,
                "verdict": "request_error", "comparison": type(error).__name__,
                "candidate_measures": []
            }
        stats[row["verdict"]] += 1
        stats[row["comparison"]] += 1
        results.append(row)
    artifact = {
        "kind": "xi-third-country-duty-review-only",
        "as_of": as_of.isoformat(),
        "sampled_from": "legacy-ni-codes",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "coverage": len(codes), "stats": dict(stats), "entries": results,
        "production_eligible": False,
        "warning": "The XI measure 103 / 1011 candidate is NOT automatically a verified UI Other rate."
    }
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+"\n",
                           encoding="utf-8")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-db", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--public", action="store_true",
                        help="Use lower-quota public API instead of managed credentials")
    parser.add_argument("--output", default="xi-audit.json")
    args = parser.parse_args()
    when = date.fromisoformat(args.as_of)
    if args.public:
        fetcher = fetch_public_commodity
    else:
        from .xi_probe import get_token, API_URL, ACCEPT
        import os
        import urllib.parse
        import urllib.request
        token = get_token(os.environ.get("HMRC_CLIENT_ID", ""),
                          os.environ.get("HMRC_CLIENT_SECRET", ""))
        # Managed OAuth token acquired once per batch (TTL is about one day).
        def fetcher(code: str, as_of: date) -> dict:
            url = API_URL + code + "?" + urllib.parse.urlencode({"as_of": as_of.isoformat()})
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}", "Accept": ACCEPT,
                "User-Agent": "liao-target-duty-xi-batch-audit/1.0"})
            with urllib.request.urlopen(req, timeout=40) as response:
                return json.load(response)

    result = audit(args.legacy_db, args.output, limit=args.limit,
                   as_of=when, fetcher=fetcher, delay=args.delay)
    print(json.dumps({"coverage": result["coverage"], "stats": result["stats"],
                      "path": args.output}, indent=2))


if __name__ == "__main__":
    main()
