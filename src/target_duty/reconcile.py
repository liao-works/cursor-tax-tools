#!/usr/bin/env python3
"""Compare a filtered DBT GB candidate snapshot against the legacy production DB.

Different source scopes are EXPECTED; missing codes are review items, not deletion
instructions. Never changes either database or silently accepts the new rates.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import re
from decimal import Decimal, InvalidOperation
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def open_readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def percent_value(text: str):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", text)
    if not match:
        return None
    return Decimal(match.group(1))


def specific_value(text: str):
    """Conservative currency/units normalization; never collapse mixed formulas."""
    text = text.strip()
    british = re.fullmatch(r"£\s*(\d[\d,.]*)\s*/\s*(.+)", text)
    iso = re.fullmatch(r"(\d[\d,.]*)\s+GBP\s*/\s*(.+)", text)
    match = british or iso
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    unit = re.sub(r"\s+", " ", match.group(2).strip().lower())
    return amount, unit


def classify_rate(old: str, new: str) -> str:
    left, right = old.strip(), new.strip()
    if left.casefold() == right.casefold():
        return "gb_rate_same"
    a, b = percent_value(left), percent_value(right)
    if a is not None and b is not None:
        return "gb_format_equivalent" if a == b else "gb_percentage_changed"
    a_sp, b_sp = specific_value(left), specific_value(right)
    if a_sp is not None and b_sp is not None and a_sp == b_sp:
        return "gb_format_equivalent"
    if a is not None and b is None:
        return "gb_new_complex_duty"
    return "gb_complex_or_missing_review"


def compare(old_path: str, new_path: str, output_path: str,
            max_examples: int = 40) -> dict:
    old_db = open_readonly(old_path)
    new_db = open_readonly(new_path)
    try:
        old = {code: (rate or "", ni or "")
               for code, rate, ni in old_db.execute(
                 "SELECT code,rate,north_ireland_rate FROM tariffs")}
        new = {code: (rate or "", ni or "", status, ni_status)
               for code, rate, ni, status, ni_status in new_db.execute(
                 "SELECT code,rate,north_ireland_rate,uk_rate_status,ni_rate_status FROM tariffs")}
    finally:
        old_db.close()
        new_db.close()

    changes = Counter()
    examples = []
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    detail = out.with_suffix(".csv")
    with open(detail, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["code", "difference", "old_uk", "new_uk",
                         "old_ni", "new_ni", "new_uk_status", "new_ni_status"])
        for code in sorted(old.keys() | new.keys()):
            prev = old.get(code)
            current = new.get(code)
            if prev is None:
                kind = "new_code"
            elif current is None:
                kind = "legacy_only_code"
            elif current[2] != "verified":
                kind = "ambiguous_new_uk"
            else:
                kind = classify_rate(prev[0], current[0])
            changes[kind] += 1
            if kind not in ("gb_rate_same", "gb_format_equivalent"):
                record = [code, kind, prev[0] if prev else "", current[0] if current else "",
                          prev[1] if prev else "", current[1] if current else "",
                          current[2] if current else "", current[3] if current else ""]
                writer.writerow(record)
                if len(examples) < max_examples:
                    examples.append(dict(zip(
                        ["code", "difference", "old_uk", "new_uk", "old_ni",
                         "new_ni", "new_uk_status", "new_ni_status"], record)))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "old_codes": len(old), "new_codes": len(new), "summary": dict(changes),
        "examples": examples,
        "all_differences_csv": detail.name,
        "note": "Format-only equivalents excluded from difference CSV. Missing codes, percentage changes and new complex duties require review. NI is legacy-stale."
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--output", default="target-duty-diff.json")
    args = parser.parse_args()
    summary = compare(args.old, args.new, args.output)
    print(json.dumps({k: v for k,v in summary.items() if k != "examples"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
