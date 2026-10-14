"""Source-only XI chapter coverage gate with explicit unverified NI duty."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as catalogue_build
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_coverage_receipt import receipt

DAY=date(2026,10,14)


def chapter_item(chap: str):
    code=chap+"01000000"
    return {"id":str(int(chap)*100),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":int(chap)*100,
                          "description":"fixture","producline_suffix":"80",
                          "declarable":True,
                          "validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


def payload(code):
    # The API's correct empty import_measures list is *zero raw measures*,
    # not a licence to produce a 0% duty rate.
    return json.dumps({"data":{"type":"commodity","id":code,
        "attributes":{"goods_nomenclature_item_id":code},
        "relationships":{"import_measures":{"data":[]}}},"included":[]}).encode()


class XICoverageReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.catalogue=root/"full.db"
        self.manifest=root/"full.json"
        self.checkpoint=root/"measures.db"
        chap_list={"data":[{"id":str(x)} for x in range(1,98)]}
        chapters={f"{x:02d}":{"data":[chapter_item(f"{x:02d}")]}
                  for x in range(1,98)}
        catalogue_build(chap_list,chapters,str(self.catalogue),as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)
        freeze(str(self.catalogue),str(self.manifest))

    def test_true_97_chapter_source_completion_is_not_ni_other_rate(self):
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=100,fetcher=lambda code,day:payload(code))
        r=receipt(str(self.catalogue),str(self.checkpoint),
                  manifest=str(self.manifest))
        self.assertTrue(r["managed_full_source_coverage_pass"])
        self.assertEqual(r["complete_chapters"],97)
        self.assertEqual(r["pending_chapters"],0)
        self.assertEqual(r["active_declarable_codes"],97)
        self.assertEqual(r["successful_responses"],97)
        self.assertEqual(r["missing_responses"],0)
        self.assertEqual(r["failed_responses"],0)
        self.assertEqual(r["raw_import_measures_replayed"],0)
        self.assertEqual(len(r["chapters"]),97)
        self.assertFalse(r["production_eligible"])
        self.assertFalse(r["ni_other_verified"])
        self.assertFalse(r["payable_duty_verified"])
        self.assertFalse(r["atomic_official_snapshot_verified"])

    def test_partial_gaps_and_failure_are_accounted_per_chapter(self):
        def flaky(code,day):
            if code.startswith("02"):
                raise RuntimeError("official 503")
            return payload(code)
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=3,fetcher=flaky)
        r=receipt(str(self.catalogue),str(self.checkpoint),
                  manifest=str(self.manifest))
        self.assertEqual(r["successful_responses"],2)
        self.assertEqual(r["failed_responses"],1)
        self.assertEqual(r["missing_responses"],94)
        self.assertEqual(r["complete_chapters"],2)
        self.assertEqual(r["pending_chapters"],95)
        self.assertFalse(r["managed_full_source_coverage_pass"])
        ch={x["chapter"]:x for x in r["chapters"]}
        self.assertEqual(ch["02"]["source_failures"],1)
        self.assertEqual(ch["02"]["failure_samples"],["0201000000"])
        self.assertEqual(ch["04"]["missing_samples"],["0401000000"])
        self.assertEqual(ch["01"]["replayed_successes"],1)

    def test_strict_cli_fails_partial_but_saves_explainable_chapter_report(self):
        import subprocess
        import sys
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=3,fetcher=lambda code,day:payload(code))
        out=self.catalogue.parent/"incomplete-report.json"
        cmd=[sys.executable,"-m","src.target_duty.xi_coverage_receipt",
             "--catalogue",str(self.catalogue),
             "--manifest",str(self.manifest),
             "--checkpoint",str(self.checkpoint),
             "--output",str(out),"--strict"]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("full official source coverage gate",result.stderr)
        self.assertTrue(out.is_file())
        audit=json.loads(out.read_text())
        self.assertEqual(audit["missing_responses"],94)
        self.assertEqual(audit["complete_chapters"],3)
        self.assertFalse(audit["managed_full_source_coverage_pass"])

    def test_strict_cli_accepts_all_raw_responses_without_ni_rate_certification(self):
        import subprocess
        import sys
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=97,fetcher=lambda code,day:payload(code))
        out=self.catalogue.parent/"completed-report.json"
        result=subprocess.run(
            [sys.executable,"-m","src.target_duty.xi_coverage_receipt",
             "--catalogue",str(self.catalogue),
             "--manifest",str(self.manifest),
             "--checkpoint",str(self.checkpoint),
             "--output",str(out),"--strict"],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        audit=json.loads(out.read_text())
        self.assertTrue(audit["managed_full_source_coverage_pass"])
        self.assertFalse(audit["ni_other_verified"])
        self.assertFalse(audit["production_eligible"])

    def test_corrupted_persisted_response_blocks_receipt(self):
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=1,fetcher=lambda code,day:payload(code))
        with sqlite3.connect(self.checkpoint) as db:
            db.execute("UPDATE requests SET response_sha256=?",
                       ("b"*64,))
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            receipt(str(self.catalogue),str(self.checkpoint),
                    manifest=str(self.manifest))

    def test_missing_or_tampered_managed_manifest_is_rejected(self):
        collect(str(self.catalogue),str(self.checkpoint),as_of=DAY,
                limit=1,fetcher=lambda code,day:payload(code))
        with self.assertRaisesRegex(ValueError,"frozen manifest"):
            receipt(str(self.catalogue),str(self.checkpoint))
        with sqlite3.connect(self.catalogue) as db:
            db.execute("UPDATE chapters SET loaded_at='modified' WHERE chapter='01'")
        with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            receipt(str(self.catalogue),str(self.checkpoint),
                    manifest=str(self.manifest))

    def test_full_public_canary_never_becomes_managed_source_completion(self):
        cat=self.catalogue.parent/"public.db"
        out=self.catalogue.parent/"public-measures.db"
        catalogue_build({"data":[{"id":"1"}]},
                        {"01":{"data":[chapter_item("01")]}},
                        str(cat),as_of=DAY,public=True)
        collect(str(cat),str(out),as_of=DAY,limit=1,
                fetcher=lambda code,day:payload(code))
        r=receipt(str(cat),str(out),public_canary=True)
        self.assertEqual(r["source_scope"],"partial_or_fixture")
        self.assertEqual(r["complete_chapters"],1)
        self.assertFalse(r["managed_full_source_coverage_pass"])
        self.assertEqual(r["readiness"],"incomplete_source_coverage")
        with self.assertRaisesRegex(ValueError,"frozen manifest"):
            receipt(str(cat),str(out))

if __name__=="__main__":
    unittest.main()
