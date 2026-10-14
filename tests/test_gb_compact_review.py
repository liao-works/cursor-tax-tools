"""Fail-closed GB compact candidate review from a real-shaped pinned full source."""
import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.catalog import FIELDS as CATALOGUE_FIELDS
from src.target_duty.catalog import build as catalog_build
from src.target_duty.gb_full_measures import build as full_build
from src.target_duty.gap_measures import FIELDS as MEASURE_FIELDS
from src.target_duty.gb_compact_review import build as compact_build

DAY=date(2026,10,14)
VERSION="v4.0.1626"


def entry(code,sid):
    return {
        "commodity__sid":sid,"commodity__code":code,"commodity__suffix":"80",
        "commodity__description":"Test commodity",
        "commodity__validity_start":"2020-01-01",
        "commodity__validity_end":"#NA",
        "parent__sid":"#NA","parent__code":"#NA","parent__suffix":"#NA",
    }


def measure(code,sid,geo,expr,*,start="2020-01-01",extra="#NA"):
    return {
        "commodity__code":code,"measure__sid":sid,"measure__type__id":"103",
        "measure__geographical_area__id":geo,
        "measure__duty_expression":expr,
        "measure__additional_code__code":extra,
        "measure__conditions":"condition-one",
        "measure__excluded_geographical_areas__ids":"#NA",
        "measure__effective_start_date":start,
        "measure__effective_end_date":"#NA",
        "measure__type__description":"Third country duty",
        "measure__quota__order_number":"#NA",
        "measure__footnotes":"#NA","measure__regulation__id":"#NA"
    }


class CompactGBTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.catalog=self.root/"gb-catalogue.db"
        self.full=self.root/"gb-full.db"
        self.compact=self.root/"gb-compact.db"
        csource=self.root/"commodities.csv"
        with csource.open("w",newline="",encoding="utf-8") as out:
            w=csv.DictWriter(out,fieldnames=CATALOGUE_FIELDS)
            w.writeheader()
            for code,sid in (
                ("0101210000","11"),("0207412010","12"),
                ("9930240000","13")):
                w.writerow(entry(code,sid))
        catalog_build(str(csource),str(self.catalog),as_of=DAY,
                      source_version=VERSION,
                      resolved_url=f"https://example.com/versions/{VERSION}/tables/commodities-report/data",
                      min_active=1)
        msource=self.root/"all-expanded.csv"
        with msource.open("w",newline="",encoding="utf-8") as out:
            w=csv.DictWriter(out,fieldnames=MEASURE_FIELDS)
            w.writeheader()
            w.writerow(measure("0101210000","1001","1011",
                               "12% + 25 GBP / 100 kg",extra="A01"))
            w.writerow(measure("0207412010","1002","1080","0%"))
            w.writerow(measure("0207412010","1003","1011",
                               "7%",start="2027-01-01"))
        self.focused=self.root/"focused.db"
        d=sqlite3.connect(self.focused)
        d.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY)")
        d.execute("INSERT INTO tariffs VALUES ('0101210000')")
        d.commit();d.close()
        full_build(str(msource),str(self.catalog),str(self.full),
                   version=VERSION,as_of=DAY,focused=str(self.focused),
                   min_rows=1)

    def test_only_103_1011_candidates_and_channel_islands_as_context(self):
        summary=compact_build(str(self.full),str(self.catalog),str(self.compact),
                              max_mb=20)
        self.assertEqual(summary["official_active_leaf_codes"],3)
        self.assertEqual(summary["active_target_codes"],1)
        self.assertEqual(summary["candidate_103_1011_rows_all_dates"],2)
        self.assertEqual(summary["non_gb_channel_islands_1080_context_rows"],1)
        self.assertEqual(summary["statuses"]["no_expanded_measure_rows_unverified"],1)
        self.assertFalse(summary["production_eligible"])
        self.assertFalse(summary["rate_applicability_verified"])
        self.assertFalse(summary["ni_other_verified"])
        d=sqlite3.connect(self.compact)
        try:
            self.assertEqual(d.execute(
                "SELECT status FROM commodities WHERE code='9930240000'"
            ).fetchone()[0],"no_expanded_measure_rows_unverified")
            self.assertEqual(d.execute(
                "SELECT status FROM commodities WHERE code='0207412010'"
            ).fetchone()[0],"non_gb_1080_only_or_other_measures_unresolved")
            self.assertEqual(d.execute(
                "SELECT expression,conditions,additional_code FROM evidence "
                "WHERE code='0101210000'"
            ).fetchone(),("12% + 25 GBP / 100 kg","condition-one","A01"))
            self.assertEqual(d.execute(
                "SELECT role FROM evidence WHERE geography='1080'"
            ).fetchone()[0],"non_gb_103_1080_context")
            self.assertEqual(d.execute(
                "SELECT active_target_candidates FROM commodities "
                "WHERE code='0207412010'"
            ).fetchone()[0],0)
            self.assertEqual(d.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally:d.close()

    def test_source_byte_tampering_rejected_before_promotion(self):
        compact_build(str(self.full),str(self.catalog),str(self.compact))
        before=self.compact.read_bytes()
        d=sqlite3.connect(self.full)
        d.execute("UPDATE measures SET expression='0%' WHERE code='0101210000'")
        d.commit();d.close()
        with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            compact_build(str(self.full),str(self.catalog),str(self.compact))
        self.assertEqual(before,self.compact.read_bytes())

    def test_source_report_and_catalogue_parity_rejected(self):
        path=self.full.with_suffix(".json")
        doc=json.loads(path.read_text())
        doc["focused_parity"]=False
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,"parity"):
            compact_build(str(self.full),str(self.catalog),str(self.compact))
        self.assertFalse(self.compact.exists())

    def test_empty_missing_leaf_never_inferred_zero_rate(self):
        compact_build(str(self.full),str(self.catalog),str(self.compact))
        d=sqlite3.connect(self.compact)
        try:
            columns=[r[1] for r in d.execute("PRAGMA table_info(commodities)")]
            self.assertNotIn("rate",columns)
            self.assertNotIn("north_ireland_rate",columns)
            self.assertEqual(d.execute(
                "SELECT COUNT(*) FROM evidence WHERE code='9930240000'"
            ).fetchone()[0],0)
        finally:d.close()


if __name__=="__main__":
    unittest.main()
