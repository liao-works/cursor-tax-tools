"""XI evidence acceptance gate: source replay, scope, and Other sentinel safety."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as catalogue_build
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_acceptance import evaluate, interpret_other_origin, decompress_checked

DAY=date(2026,10,14)

def commodity(code,sid):
    return {"id":str(sid),"type":"goods_nomenclature","attributes":{
        "goods_nomenclature_item_id":code,"goods_nomenclature_sid":sid,
        "producline_suffix":"80","description":"Sample",
        "declarable":True,"validity_start_date":"2020-01-01",
        "validity_end_date":None}}

def source_body(code, *, use="103"):
    measure={"id":"123","type":"measure",
       "attributes":{"resolved_duty_expression":"5% + 10 EUR / 100 kg",
                     "effective_start_date":"2020-01-01"},
       "relationships":{"measure_type":{"data":{"type":"measure_type","id":use}},
                        "geographical_area":{"data":{"type":"geographical_area","id":"1011"}},
                        "measure_conditions":{"data":[{"type":"measure_condition","id":"q"}]}}}
    return json.dumps({"data":{"type":"commodity","id":code,
      "attributes":{"goods_nomenclature_item_id":code},
      "relationships":{"import_measures":{"data":[{"type":"measure","id":"123"}]}}},
      "included":[measure]}).encode()

class XIAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.catalogue=self.base/"catalog.db"
        self.measures=self.base/"measures.db"
        listing={"data":[{"id":"1"}]}
        chapter={"01":{"data":[commodity("0101210000",1),commodity("0101290000",2)]}}
        catalogue_build(listing,chapter,str(self.catalogue),as_of=DAY)
        collect(str(self.catalogue),str(self.measures),as_of=DAY,limit=1,
                fetcher=lambda code,day:source_body(code))

    def test_partial_canary_never_claims_complete(self):
        x=evaluate(str(self.catalogue),str(self.measures),min_chapters=1)
        self.assertEqual(x["active_declarable_codes"],2)
        self.assertEqual(x["missing_codes"],1)
        self.assertEqual(x["chapters_incomplete"],1)
        self.assertEqual(x["chapter_coverage"][0]["chapter"],"01")
        self.assertEqual(x["chapter_coverage"][0]["expected"],2)
        self.assertEqual(x["chapter_coverage"][0]["verified_responses"],1)
        self.assertEqual(x["chapter_coverage"][0]["missing"],1)
        self.assertFalse(x["chapter_coverage"][0]["complete"])
        self.assertEqual(x["replayed_source_measures"],1)
        self.assertEqual(x["third_country_measure_types"],{"103":1})
        self.assertEqual(x["readiness"],"source_coverage_incomplete")
        self.assertFalse(x["other_semantics_verified"])
        with self.assertRaisesRegex(ValueError,"strict audit"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=1,strict=True)

    def test_complete_sample_still_not_a_verified_ni_rate(self):
        collect(str(self.catalogue),str(self.measures),as_of=DAY,limit=2,
                fetcher=lambda code,day:source_body(code,use="105"))
        x=evaluate(str(self.catalogue),str(self.measures),min_chapters=1)
        self.assertEqual(x["readiness"],"source_coverage_incomplete")
        self.assertEqual(x["listing_scope"],"partial_or_fixture")
        self.assertTrue(x["all_collected_measure_responses_verified"])
        self.assertEqual(x["chapters_incomplete"],0)
        self.assertTrue(x["chapter_coverage"][0]["complete"])
        self.assertEqual(x["third_country_measure_types"],{"103":1,"105":1})
        self.assertFalse(x["atomic_official_snapshot_verified"])
        self.assertFalse(x["production_eligible"])

    def test_checksum_tampering_is_fatal(self):
        db=sqlite3.connect(self.measures)
        db.execute("UPDATE requests SET response_sha256=? WHERE code='0101210000'",
                   ("0"*64,))
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=1)

    def test_persisted_measure_tampering_is_fatal(self):
        db=sqlite3.connect(self.measures)
        db.execute("UPDATE measures SET measure_type='106' WHERE code='0101210000'")
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError,"discrepancy"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=1)

    def test_failed_source_request_cannot_hide_measure_rows(self):
        db=sqlite3.connect(self.measures)
        db.execute("UPDATE requests SET status='source_error',error='bad source' "
                   "WHERE code='0101210000'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"failed request incorrectly contains measures"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=1)

    def test_unknown_request_status_is_not_an_ordinary_retry(self):
        db=sqlite3.connect(self.measures)
        db.execute("UPDATE requests SET status='claimed_complete' "
                   "WHERE code='0101210000'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"Unsupported XI source status"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=1)

    def test_catalogue_changed_or_wrong_minimum(self):
        with self.assertRaisesRegex(ValueError,"thresholds"):
            evaluate(str(self.catalogue),str(self.measures),min_chapters=0)
        # Exactly one XI chapter is not complete official XI coverage.
        x=evaluate(str(self.catalogue),str(self.measures))
        self.assertFalse(x["independent_catalogue_scope_ok"])

    def test_strict_cli_archives_gap_evidence_before_nonzero_exit(self):
        import subprocess,sys
        output=self.base/"strict-missing-evidence.json"
        p=subprocess.run([
            sys.executable,"-m","src.target_duty.xi_acceptance",
            "--catalogue",str(self.catalogue),
            "--measures",str(self.measures),
            "--min-chapters","1","--strict","--output",str(output)
        ],capture_output=True,text=True,timeout=30)
        self.assertNotEqual(p.returncode,0)
        self.assertIn("report retained",p.stderr)
        self.assertTrue(output.is_file())
        recorded=json.loads(output.read_text())
        self.assertEqual(recorded["missing_codes"],1)
        self.assertEqual(recorded["chapters_incomplete"],1)
        self.assertEqual(recorded["active_declarable_codes"],2)
        self.assertFalse(recorded["production_eligible"])

    def test_ambiguous_origin_other_is_not_rate(self):
        missing=interpret_other_origin("OTHER")
        self.assertEqual(missing["status"],"origin_required")
        self.assertIsNone(missing["duty_rate"])
        valid=interpret_other_origin("OTHER",selected_country="AR")
        self.assertEqual(valid["origin"],"AR")
        self.assertEqual(valid["candidate_measure_types"],["103","105"])
        self.assertEqual(valid["status"],"origin_specific_official_filter_required")
        self.assertFalse(valid["production_eligible"])
        self.assertEqual(interpret_other_origin("CN")["origin"],"CN")
        self.assertEqual(
            interpret_other_origin("OTHER",selected_country="UNCONFIRMED")["status"],
            "origin_required")
        with self.assertRaises(ValueError):
            interpret_other_origin("1080")

    def test_decompression_limits_and_trailer(self):
        import zlib
        raw=source_body("0101210000")
        self.assertEqual(decompress_checked(zlib.compress(raw)),raw)
        with self.assertRaisesRegex(ValueError,"Oversized"):
            decompress_checked(zlib.compress(raw),max_bytes=4)
        with self.assertRaisesRegex(ValueError,"trailing"):
            decompress_checked(zlib.compress(raw)+b"junk")


if __name__ == "__main__":
    unittest.main()
