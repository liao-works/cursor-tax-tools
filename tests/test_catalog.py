"""Offline catalogue/coverage regression tests; no HMRC access is required."""
import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.catalog import FIELDS, build, compare, effective, source_url

DAY = date(2026, 10, 14)


def item(code, suffix="80", *, sid=None, start="2020-01-01", end="#NA",
         parent=None, description="Item"):
    return {
        "commodity__sid": sid or code + suffix,
        "commodity__code": code,
        "commodity__suffix": suffix,
        "commodity__description": description,
        "commodity__validity_start": start,
        "commodity__validity_end": end,
        "parent__sid": "#NA" if parent is None else parent,
        "parent__code": "#NA", "parent__suffix": "#NA",
    }


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def tariff_db(path, codes):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY, rate TEXT)")
    conn.executemany("INSERT INTO tariffs VALUES(?, '3%')", [(code,) for code in codes])
    conn.commit()
    conn.close()


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.csv = self.directory / "commodities.csv"
        self.db = self.directory / "catalogue.db"

    def build_fixture(self, rows, min_active=1):
        write_csv(self.csv, rows)
        return build(str(self.csv), str(self.db), as_of=DAY,
                     source_version="v4.0.1595", min_active=min_active)

    def test_suffixes_history_and_bounds_are_preserved(self):
        report = self.build_fixture([
            item("0101210000", "10", sid="1"),
            item("0101210000", "80", sid="2", description="Horse"),
            item("0207412000", "80", sid="3", end="2026-10-13"),
            item("0207412010", "80", sid="4", start="2026-10-14"),
        ], min_active=2)
        self.assertEqual(report["counts"]["all_catalogue_rows"], 4)
        self.assertEqual(report["counts"]["active_suffix80_candidates"], 2)
        self.assertEqual(report["counts"]["active_declarable_leaf_codes"], 2)
        self.assertEqual(report["counts"]["inactive_or_non_suffix80_rows"], 2)
        self.assertFalse(report["production_eligible"])
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT active,suffix80 FROM commodities WHERE sid='1'"
            ).fetchone(), (1, 0))
            self.assertEqual(conn.execute(
                "SELECT active,suffix80 FROM commodities WHERE sid='3'"
            ).fetchone(), (0, 1))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 4)
        finally:
            conn.close()
        self.assertEqual(json.loads(self.db.with_suffix(".metadata.json").read_text())
                         ["source_csv_sha256"], report["source_csv_sha256"])

    def test_structural_suffix80_node_is_not_declarable(self):
        report = self.build_fixture([
            item("0101000000", "80", sid="parent"),
            item("0101210000", "10", sid="inner", parent="parent"),
            item("0101210000", "80", sid="child", parent="inner"),
            item("0101290000", "80", sid="other", parent="parent"),
        ])
        self.assertEqual(report["counts"]["active_suffix80_candidates"], 3)
        self.assertEqual(report["counts"]["active_declarable_leaf_codes"], 2)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                """SELECT COUNT(*) FROM commodities c
                   WHERE c.active=1 AND c.suffix80=1
                     AND NOT EXISTS (SELECT 1 FROM commodities d
                       WHERE d.parent_sid=c.sid AND d.active=1)"""
            ).fetchone()[0], 2)
        finally:
            conn.close()

    def test_start_and_end_dates_are_inclusive(self):
        self.assertTrue(effective(date(2026, 10, 14), date(2026, 10, 14), DAY))
        self.assertFalse(effective(date(2026, 10, 15), None, DAY))
        self.assertFalse(effective(date(2020, 1, 1), date(2026, 10, 13), DAY))

    def test_invalid_row_fails_closed(self):
        for row in (
            item("0207412010", sid="1", start="bad-date"),
            item("0207412010", sid="1", end="2020-01-01"),
            item("020741201", sid="1"),
            item("0207412010", suffix="X", sid="1"),
        ):
            with self.subTest(row=row):
                if self.db.exists():
                    self.db.unlink()
                with self.assertRaises(ValueError):
                    self.build_fixture([row])
                self.assertFalse(self.db.exists())

    def test_duplicate_sid_suffix_does_not_silently_overwrite(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build_fixture([item("0101210000", sid="dup"),
                                item("0207412010", sid="dup")])
        self.assertFalse(self.db.exists())

    def test_minimum_active_coverage_gate(self):
        with self.assertRaisesRegex(ValueError, "Coverage gate"):
            self.build_fixture([item("0101210000")], min_active=2)
        self.assertFalse(self.db.exists())

    def test_compare_identifies_scope_without_deleting(self):
        self.build_fixture([
            item("0101210000", sid="1"),
            item("0207412010", sid="2"),
            item("0207412000", sid="3", end="2026-10-13")
        ])
        old, focused = self.directory / "old.db", self.directory / "focused.db"
        tariff_db(old, ["0101210000", "0207412000", "0301110000"])
        tariff_db(focused, ["0101210000", "0301110000"])
        path = self.directory / "coverage.json"
        report = compare(str(self.db), str(old), str(focused), str(path))
        self.assertEqual(report["active_official_leaf_catalogue"], 2)
        self.assertEqual(report["active_leaf_missing_focused"], 1)
        self.assertEqual(report["focused_not_active_leaf"], 1)
        self.assertEqual(report["legacy_not_active_leaf"], 2)
        self.assertEqual(report["legacy_not_focused"], 1)
        self.assertFalse(report["production_eligible"])
        with path.with_suffix(".csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 3)

    def test_url_is_unfiltered_and_restricts_version(self):
        url = source_url("latest")
        self.assertIn("/tables/commodities-report/data", url)
        self.assertNotIn("measure__type__id", url)
        self.assertTrue(url.endswith("?format=csv"))
        self.assertNotIn("query-simple", url)
        with self.assertRaises(ValueError):
            source_url("../../unsafe")

    def test_missing_columns_is_hard_error(self):
        self.csv.write_text("commodity__code\n0101210000\n")
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            build(str(self.csv), str(self.db), as_of=DAY,
                  source_version="latest", min_active=1)


if __name__ == "__main__":
    unittest.main()
