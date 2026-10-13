import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_duty.legacy import validate_metadata
from src.target_duty.reconcile import compare, classify_rate


class ReconcileTest(unittest.TestCase):
    def test_rate_semantics_not_merely_strings(self):
        self.assertEqual(classify_rate("0.00%", "0%"), "gb_format_equivalent")
        self.assertEqual(classify_rate("1.60%", "1.6%"), "gb_format_equivalent")
        self.assertEqual(classify_rate("£34.00 / 100 kg", "34 GBP / 100 kg"),
                         "gb_format_equivalent")
        self.assertEqual(classify_rate("8.00%", "14%"), "gb_percentage_changed")
        self.assertEqual(classify_rate("10.00%", "10% + 77 GBP / 100 kg"),
                         "gb_new_complex_duty")

    def test_metadata_has_to_pin_existing_version(self):
        self.assertEqual(validate_metadata({
            "version": "data-36851279616", "file_size": 500,
            "file_hash": "sha256:" + "a" * 64,
        }), ("data-36851279616", 500, "a" * 64))
        with self.assertRaises(ValueError):
            validate_metadata({"version": "../latest-data", "file_size": 42,
                               "file_hash": "sha256:" + "b" * 64})

    def test_reports_missing_codes_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, output = [Path(tmp)/x for x in ("old.db","new.db","diff.json")]
            one=sqlite3.connect(old)
            one.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY,rate TEXT,north_ireland_rate TEXT)")
            one.executemany("INSERT INTO tariffs VALUES(?,?,?)",[
                ("0101210000","4%","6%"),("0201100000","0%","2%")])
            one.commit();one.close()
            two=sqlite3.connect(new)
            two.execute("""CREATE TABLE tariffs(code TEXT PRIMARY KEY,rate TEXT,
                            north_ireland_rate TEXT,uk_rate_status TEXT,ni_rate_status TEXT)""")
            two.executemany("INSERT INTO tariffs VALUES(?,?,?,?,?)",[
                ("0101210000","5%","6%","verified","stale"),
                ("0301110000","0%",None,"verified","pending")])
            two.commit();two.close()
            report=compare(str(old),str(new),str(output))
            self.assertEqual(report["summary"]["gb_percentage_changed"], 1)
            self.assertEqual(report["summary"]["legacy_only_code"], 1)
            self.assertEqual(report["summary"]["new_code"], 1)
            self.assertTrue(output.exists())
            self.assertEqual(len(list(csv.DictReader(open(output.with_suffix(".csv"))))), 3)

if __name__ == "__main__":
    unittest.main()
