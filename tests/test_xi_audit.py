import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_audit import audit, select_codes


def official(code, as_of):
    expression = "10.20 % + 93.10 EUR / 100 kg" if code.startswith("02") else "0%"
    return {
        "data": {"relationships": {"import_measures": {
            "data": [{"id": "1", "type": "measure"}]}}},
        "included": [
            {"id": "1", "type": "measure",
             "attributes": {"effective_start_date": "2021-01-01T00:00:00.000Z"},
             "relationships": {
                 "measure_type": {"data": {"id": "103", "type": "measure_type"}},
                 "geographical_area": {"data": {"id": "1011", "type": "geographical_area"}},
                 "duty_expression": {"data": {"id": "d", "type": "duty_expression"}},
                 "measure_conditions": {"data": []}}},
            {"id": "d", "type": "duty_expression",
             "attributes": {"base": expression}}
        ]
    }


class XIAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.db = self.dir / "legacy.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE tariffs(code TEXT,north_ireland_rate TEXT)")
            conn.executemany("INSERT INTO tariffs VALUES (?,?)", [
                ("0101210000", "0.00%"), ("0101299000", "4%"),
                ("0201100000", "10.20%"), ("0301110000", "0%")])

    def tearDown(self):
        self.temp.cleanup()

    def test_chapters_are_sampled_across_product_families(self):
        chosen = select_codes(str(self.db), 3)
        self.assertEqual([code[:2] for code, _ in chosen], ["01", "02", "03"])
        with self.assertRaises(ValueError):
            select_codes(str(self.db), 101)

    def test_report_only_and_preserve_mixed_duties(self):
        report = audit(str(self.db), str(self.dir/"audit.json"),
                       limit=3, as_of=date(2026,10,13),
                       fetcher=official, delay=0)
        self.assertFalse(report["production_eligible"])
        self.assertEqual(report["coverage"], 3)
        self.assertEqual(report["stats"]["candidate_review"], 3)
        feb = next(r for r in report["entries"] if r["code"].startswith("02"))
        self.assertEqual(feb["xi_candidate"], "10.20 % + 93.10 EUR / 100 kg")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tariffs").fetchone()[0], 4)

if __name__ == "__main__":
    unittest.main()
