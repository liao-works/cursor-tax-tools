"""Offline GB/XI/legacy three-way completeness matrix."""
import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.target_duty.xi_catalogue import build
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.gb_xi_coverage_matrix import matrix

DAY = date(2026, 10, 14)


def goods(code, sid):
    return {"id":str(sid),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":sid,
                          "producline_suffix":"80", "declarable":True,
                          "validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


def raw(code, as_of):
    return json.dumps({"data":{"type":"commodity","id":code,
        "attributes":{"goods_nomenclature_item_id":code},
        "relationships":{"import_measures":{"data":[]}}},"included":[]}).encode()


class CrossDatabaseMatrixTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.cat=str(root/"catalogue.db")
        self.check=str(root/"checkpoint.db")
        self.gb=str(root/"gb.db")
        self.old=str(root/"legacy.db")
        self.csv=str(root/"matrix.csv")
        self.json=str(root/"matrix.json")
        build({"data":[{"id":"1"}]},
              {"01":{"data":[goods("0101210000",1),goods("0101290000",2)]}},
              self.cat,as_of=DAY)
        collect(self.cat,self.check,as_of=DAY,limit=1,fetcher=raw)
        with sqlite3.connect(self.old) as db:
            db.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY,rate TEXT)")
            db.executemany("INSERT INTO tariffs VALUES(?,?)",[
                ("0101210000","4%"),("9900000000","0%")])
        # Fake GB snapshot is intentional: its real independent SHA validation
        # is exercised by gb_review_diff tests, not replaced in production.
        self.fixture={
            "version":"v4.0.1627","as_of":DAY.isoformat(),"sha256":"a"*64,
            "codes":{
                "0101210000":{"status":"candidate_present_requires_applicability_review"},
                "0201100000":{"status":"no_active_103_1011_unresolved"},
            }}

    def analyze(self):
        with patch("src.target_duty.gb_xi_coverage_matrix.gb_snapshot",
                   return_value=self.fixture):
            return matrix(self.old,self.gb,self.cat,self.check,
                          output_csv=self.csv,output_report=self.json,
                          min_chapters=1)

    def test_union_includes_missing_regions_and_never_fakes_payable_rate(self):
        report=self.analyze()
        self.assertEqual(report["matrix_rows"],4)
        self.assertEqual(report["legacy_desktop_codes"],2)
        self.assertEqual(report["gb_official_active_leaf_codes"],2)
        self.assertEqual(report["xi_official_active_declarable_codes"],2)
        self.assertFalse(report["xi_source_response_complete"])
        self.assertFalse(report["production_eligible"])
        self.assertFalse(report["ni_other_verified"])
        self.assertEqual(report["source_status_counts"]["xi_source_not_collected"],1)
        self.assertEqual(report["source_status_counts"]["legacy_not_active_in_either_catalogue"],1)
        self.assertEqual(hashlib.sha256(Path(self.csv).read_bytes()).hexdigest(),
                         report["matrix_csv_sha256"])
        rows={r["code"]:r for r in csv.DictReader(open(self.csv,encoding="utf-8"))}
        self.assertEqual(rows["0101290000"]["xi_original_response_status"],
                         "source_not_collected")
        self.assertEqual(rows["0201100000"]["gb_103_1011_source_status"],
                         "no_active_103_1011_unresolved")
        self.assertEqual(rows["9900000000"]["legacy_desktop"],"1")
        self.assertEqual(rows["9900000000"]["xi_official_declarable"],"0")
        self.assertFalse(json.loads(Path(self.json).read_text())["production_eligible"])

    def test_repaired_xi_code_is_reflected_without_claiming_managed_full(self):
        collect(self.cat,self.check,as_of=DAY,limit=2,fetcher=raw)
        report=self.analyze()
        self.assertEqual(report["source_status_counts"].get("xi_source_not_collected",0),0)
        self.assertFalse(report["xi_source_response_complete"])
        # Partial fixture catalogue can never pass managed full completeness.
        self.assertFalse(report["both_regional_sources_complete"])

    def test_mismatched_region_effective_dates_block_joint_source_completion(self):
        self.fixture["as_of"] = "2026-10-13"
        report = self.analyze()
        self.assertFalse(report["as_of_dates_equal"])
        self.assertFalse(report["both_regional_sources_complete"])
        self.assertEqual(report["as_of_gb"], "2026-10-13")
        self.assertEqual(report["as_of_xi"], "2026-10-14")

    def test_sources_and_outputs_cannot_alias(self):
        with patch("src.target_duty.gb_xi_coverage_matrix.gb_snapshot",
                   return_value=self.fixture):
            with self.assertRaisesRegex(ValueError,"overwrite or alias"):
                matrix(self.old,self.gb,self.cat,self.check,
                       output_csv=self.old,output_report=self.json,min_chapters=1)


if __name__ == "__main__":
    unittest.main()
