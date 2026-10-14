"""Every XI source gap must survive independent response-replay review."""
import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build
from src.target_duty.xi_public_full_capture import freeze_public,collect_partition
from src.target_duty.xi_public_gap_ledger import gaps

DAY = date(2026, 10, 14)


def commodity(chapter):
    code=chapter+"01000000"
    return {"type":"goods_nomenclature","id":str(int(chapter)*100),
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":int(chapter)*100,
                          "declarable":True,"producline_suffix":"80",
                          "validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


def source(code,day):
    return json.dumps({
        "data":{"type":"commodity","id":code,
                "attributes":{"goods_nomenclature_item_id":code},
                "relationships":{"import_measures":{"data":[]}}},
        "included":[]}).encode()


class PublicGapLedgerTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
        self.cat=str(self.root/"cat.db")
        self.manifest=str(self.root/"frozen.json")
        chapters={f"{i:02d}":{"data":[commodity(f"{i:02d}")]}
                  for i in range(1,98)}
        build({"data":[{"id":str(i)} for i in range(1,98)]},chapters,self.cat,
              as_of=DAY,public=True,public_full_audit=True,official_listed_total=97)
        freeze_public(self.cat,self.manifest)

    def run_gap(self,shards):
        return gaps(self.cat,self.manifest,shards,
                    output_csv=str(self.root/"all-codes.csv"),
                    output_report=str(self.root/"gaps.json"))

    def crawl(self,index,fetcher=source):
        path=str(self.root/f"shard-{index}.db")
        collect_partition(self.cat,self.manifest,path,worker=index,workers=4,
                          batch_size=11,interval=0,fetcher=fetcher)
        return path

    def test_no_shards_reports_every_missing_code_never_zero_duty(self):
        report=self.run_gap([])
        self.assertEqual(report["expected_commodity_codes"],97)
        self.assertEqual(report["replayed_successful_responses"],0)
        self.assertEqual(report["missing_responses"],97)
        self.assertEqual(report["source_errors"],0)
        self.assertEqual(report["incomplete_chapters"],97)
        self.assertFalse(report["production_eligible"])
        self.assertFalse(report["managed_full_source_coverage_proven"])
        rows=list(csv.DictReader(open(self.root/"all-codes.csv")))
        self.assertEqual(len(rows),97)
        self.assertEqual(rows[0]["source_response_status"],"missing")

    def test_one_worker_partial_gap_ledger_and_all_four_complete(self):
        first=self.crawl(0)
        partial=self.run_gap([first])
        self.assertEqual(partial["replayed_successful_responses"],25)
        self.assertEqual(partial["missing_responses"],72)
        self.assertEqual(partial["incomplete_chapters"],72)
        self.assertEqual(partial["source_errors"],0)
        others=[self.crawl(i) for i in (1,2,3)]
        complete=self.run_gap([first,*others])
        self.assertTrue(complete["public_source_complete"])
        self.assertEqual(complete["replayed_successful_responses"],97)
        self.assertEqual(complete["missing_responses"],0)
        self.assertEqual(complete["source_errors"],0)
        self.assertFalse(complete["production_eligible"])
        self.assertEqual(len(complete["original_shards"]),4)

    def test_http_failure_remains_explicit_and_recoverable(self):
        def fail_one(code,day):
            if code=="0101000000":
                raise RuntimeError("HTTP 503")
            return source(code,day)
        first=self.crawl(0,fail_one)
        receipt=self.run_gap([first])
        self.assertEqual(receipt["source_errors"],1)
        self.assertEqual(receipt["replayed_successful_responses"],10)
        self.assertEqual(receipt["missing_responses"],86)
        self.assertIn("0101000000",receipt["samples"]["source_error"])
        collect_partition(self.cat,self.manifest,first,worker=0,workers=4,
                          batch_size=11,interval=0,fetcher=source)
        recovered=self.run_gap([first])
        self.assertEqual(recovered["source_errors"],0)
        self.assertEqual(recovered["replayed_successful_responses"],25)

    def test_duplicate_codes_and_tampered_source_fail_closed(self):
        first=self.crawl(0)
        copied=str(self.root/"copied.db")
        shutil.copyfile(first,copied)
        with self.assertRaisesRegex(ValueError,"multiple public shards"):
            self.run_gap([first,copied])
        with sqlite3.connect(first) as db:
            db.execute("UPDATE requests SET response_sha256=? WHERE code=?",("f"*64,"0101000000"))
        with self.assertRaises(ValueError):
            self.run_gap([first])


if __name__=="__main__":
    unittest.main()
