"""GB full expanded measures: lossless, pinned, fail-closed regression suite."""
import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.catalog import FIELDS as CFIELDS, build as catalog_build
from src.target_duty.gb_full_measures import build, source_url
from src.target_duty.gap_measures import FIELDS as MFIELDS
from src.target_duty.region_measures import lookup

DAY = date(2026,10,14)
VERSION = "v4.0.1626"


def commodity(code, sid):
    return {
        "commodity__sid": sid, "commodity__code": code, "commodity__suffix": "80",
        "commodity__description": "Sample", "commodity__validity_start": "2022-01-01",
        "commodity__validity_end": "#NA", "parent__sid": "#NA",
        "parent__code": "#NA", "parent__suffix": "#NA",
    }


def measure(code, sid, kind, geography, expression, *,
            start="2022-01-01", end="#NA"):
    return {
        "commodity__code": code, "measure__sid": sid,
        "measure__type__id": kind, "measure__geographical_area__id": geography,
        "measure__duty_expression": expression,
        "measure__additional_code__code": "#NA", "measure__conditions": "#NA",
        "measure__excluded_geographical_areas__ids": "#NA",
        "measure__effective_start_date": start, "measure__effective_end_date": end,
        "measure__type__description": "Import duty",
        "measure__quota__order_number": "#NA", "measure__footnotes": "#NA",
        "measure__regulation__id": "#NA", "some_future_official_field": "keep",
    }


class FullGBTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cat = self.base / "gb-catalogue.db"
        self.csv = self.base / "all-measures.csv"
        self.focused = self.base / "focused.db"
        self.output = self.base / "gb-full.db"
        source = self.base / "commodity.csv"
        with source.open("w",newline="",encoding="utf-8") as out:
            w = csv.DictWriter(out, fieldnames=CFIELDS)
            w.writeheader()
            w.writerow(commodity("0101210000","100"))
            w.writerow(commodity("0207412010","200"))
            w.writerow(commodity("9930240000","300"))
        catalog_build(str(source),str(self.cat),as_of=DAY,
                      source_version=VERSION,
                      resolved_url=f"https://example.com/versions/{VERSION}/tables/commodities-report/data",
                      min_active=1)
        db = sqlite3.connect(self.focused)
        db.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY)")
        db.execute("INSERT INTO tariffs VALUES ('0101210000')")
        db.commit(); db.close()

    def csv_rows(self, rows):
        with self.csv.open("w",newline="",encoding="utf-8") as out:
            w = csv.DictWriter(out,fieldnames=MFIELDS + ("some_future_official_field",))
            w.writeheader()
            w.writerows(rows)

    def test_full_scope_preserves_raw_type_geography_and_no_measure_leaf(self):
        self.csv_rows([
            measure("0101210000","1000","103","1011","12% + 25 GBP / 100 kg"),
            measure("0101210000","1001","142","1002","0%"),
            measure("0207412010","1002","103","1080","0%"),
            measure("0207412010","1003","551","1011","19 GBP / 100 kg",
                    start="2027-01-01"),
        ])
        result = build(str(self.csv),str(self.cat),str(self.output),
                       version=VERSION,as_of=DAY,focused=str(self.focused),
                       min_rows=4)
        self.assertEqual(result["source_rows"],4)
        self.assertEqual(result["official_active_leaves"],3)
        self.assertEqual(result["active_leaves_without_any_export_rows"],1)
        self.assertEqual(result["missing_leaf_samples"],["9930240000"])
        self.assertEqual(result["focused_codes"],1)
        self.assertEqual(result["active_103_1011_codes"],1)
        self.assertTrue(result["focused_parity"])
        self.assertFalse(result["production_eligible"])
        gb = lookup(str(self.output),"GB","0101210000")
        self.assertEqual(len(gb["measures"]),2)
        self.assertEqual(gb["measures"][0]["duty_expression"],
                         "12% + 25 GBP / 100 kg")
        self.assertEqual(gb["measures"][0]["raw"]["some_future_official_field"],"keep")
        channel = lookup(str(self.output),"GB","0207412010")
        self.assertEqual(channel["measures"][0]["geography"],"1080")
        self.assertEqual(channel["measures"][1]["effective_status"],"inactive")
        empty = lookup(str(self.output),"GB","9930240000")
        self.assertEqual(empty["status"],"no_expanded_measure_rows_unverified")
        self.assertEqual(empty["measures"],[])
        self.assertEqual(lookup(str(self.output),"XI","0101210000")["reason"],
                         "region_not_ingested")

    def test_fail_on_focused_measure_absent_even_if_present_for_channel_islands(self):
        self.csv_rows([measure("0101210000","1000","103","1080","0%")])
        with self.assertRaisesRegex(ValueError,"parity failed"):
            build(str(self.csv),str(self.cat),str(self.output),
                  version=VERSION,as_of=DAY,focused=str(self.focused),min_rows=1)
        self.assertFalse(self.output.exists())

    def test_fail_on_truncated_export_and_keep_previous_database(self):
        self.csv_rows([measure("0101210000","1000","103","1011","0%")])
        before = build(str(self.csv),str(self.cat),str(self.output),
                       version=VERSION,as_of=DAY,focused=str(self.focused),
                       min_rows=1)
        previous = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "too few"):
            build(str(self.csv),str(self.cat),str(self.output),
                  version=VERSION,as_of=DAY,focused=str(self.focused),min_rows=10)
        self.assertEqual(self.output.read_bytes(),previous)
        self.assertEqual(before["source_rows"],1)

    def test_reject_missing_required_fields_and_foreign_commodity(self):
        self.csv.write_text("commodity__code,measure__sid\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"missing official fields"):
            build(str(self.csv),str(self.cat),str(self.output),
                  version=VERSION,as_of=DAY,min_rows=1)
        self.csv_rows([measure("not-an-id","1000","103","1011","0%")])
        with self.assertRaisesRegex(ValueError,"Invalid code"):
            build(str(self.csv),str(self.cat),str(self.output),
                  version=VERSION,as_of=DAY,min_rows=1)

    def test_invalid_pin_or_catalogue_date(self):
        with self.assertRaisesRegex(ValueError,"pinned"):
            source_url("latest")
        self.csv_rows([measure("0101210000","1000","103","1011","0%")])
        with self.assertRaisesRegex(ValueError,"source/as_of mismatch"):
            build(str(self.csv),str(self.cat),str(self.output),
                  version=VERSION,as_of=date(2026,10,13),min_rows=1)


if __name__ == "__main__":
    unittest.main()
