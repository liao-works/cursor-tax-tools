import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.target_duty.build import COLUMNS, build, build_url, choose

class TargetDutyTest(unittest.TestCase):
    def row(self, code="0101210000", rate="0%", **kw):
        item = {x: "" for x in COLUMNS}
        item.update({"commodity__code": code,
                     "commodity__description": "Breeding animals",
                     "measure__type__id": "103",
                     "measure__geographical_area__id": "1011",
                     "measure__duty_expression": rate,
                     "measure__effective_start_date": "2025-01-01"})
        item.update(kw)
        return item

    def test_url_only_filtered_target(self):
        query = parse_qs(urlsplit(build_url("v4.0.1595")).query)
        self.assertEqual(query["measure__type__id"], ["103"])
        self.assertEqual(query["measure__geographical_area__id"], ["1011"])
        self.assertEqual(len(query["_columns"]), len(COLUMNS))
        with self.assertRaises(ValueError):
            build_url("../evil")

    def test_ambiguity_is_not_zero(self):
        self.assertEqual(choose([self.row(rate="0%")]), ("0%", "verified"))
        self.assertEqual(choose([self.row(rate="0%"), self.row(rate="6%")]), ("", "conditional"))
        self.assertEqual(choose([self.row(measure__additional_code__code="1234")]),
                         ("", "conditional"))
        self.assertEqual(choose([self.row(measure__conditions="A1")]), ("", "conditional"))

    def test_shadow_build_and_stale_ni(self):
        with tempfile.TemporaryDirectory() as temp:
            source, prior, output = [Path(temp) / x for x in ("source.csv","legacy.db","target.db")]
            with open(source, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerow(self.row())
                writer.writerow(self.row(code="0101290000", rate="6%"))
                writer.writerow(self.row(code="0101290000", rate="12%"))
                writer.writerow(self.row(code="0101300000", rate="2%",
                                         measure__effective_end_date="2025-01-01"))
            db = sqlite3.connect(prior)
            db.execute("CREATE TABLE tariffs(code TEXT,north_ireland_rate TEXT,north_ireland_url TEXT)")
            db.execute("INSERT INTO tariffs VALUES ('0101210000', '4%', 'https://example.test/ni')")
            db.commit()
            db.close()
            meta = build(str(source), str(output), version="v4.0.1595",
                         as_of=date(2026,10,13), legacy_db=str(prior),
                         min_records=2, min_verified_ratio=0.4)
            self.assertEqual(meta["record_count"], 2)
            con = sqlite3.connect(output)
            self.assertEqual(con.execute("SELECT rate,uk_rate_status,north_ireland_rate,ni_rate_status FROM tariffs WHERE code='0101210000'").fetchone(), ("0%","verified","4%","stale"))
            self.assertEqual(con.execute("SELECT rate,uk_rate_status FROM tariffs WHERE code='0101290000'").fetchone(), ("","conditional"))
            con.close()

    def test_rejects_low_coverage_without_publishing(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp)/"s.csv", Path(temp)/"o.db"
            with open(source,"w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=COLUMNS)
                w.writeheader()
                w.writerow(self.row())
            with self.assertRaisesRegex(ValueError,"Coverage gate"):
                build(str(source),str(output),version="latest",as_of=date(2026,10,13),
                      min_records=2)
            self.assertFalse(output.exists())

if __name__ == "__main__":
    unittest.main()
