"""Read-only XI shard assembly: cross-run recovery without silent overwrite."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as catalogue_build
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_merge import combine
from src.target_duty.xi_acceptance import evaluate

DAY=date(2026,10,14)
CODES=["0101210000","0101290000","0101300000"]

def source_item(code,index):
    return {"id":str(index),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":index,
                          "description":"Fixture","producline_suffix":"80",
                          "declarable":True,"validity_start_date":"2020-01-01",
                          "validity_end_date":None}}

def payload(code, rate="4%"):
    mid=str(int(code))
    return json.dumps({
      "data":{"id":code,"type":"commodity",
              "attributes":{"goods_nomenclature_item_id":code},
              "relationships":{"import_measures":{"data":[
                  {"type":"measure","id":mid}]}}},
      "included":[{"id":mid,"type":"measure",
          "attributes":{"resolved_duty_expression":rate,
                        "effective_start_date":"2020-01-01"},
          "relationships":{"measure_type":{"data":{"type":"measure_type","id":"105"}},
                           "geographical_area":{"data":{"type":"geographical_area","id":"1011"}}}}],
    }).encode()

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cat=self.root/"xi-catalogue.db"
        self.shard1=self.root/"shard-a.db"
        self.shard2=self.root/"shard-b.db"
        self.out=self.root/"joined.db"
        chapter={"01":{"data":[source_item(code,i+1) for i,code in enumerate(CODES)]}}
        catalogue_build({"data":[{"id":"1"}]},chapter,str(self.cat),as_of=DAY)
        collect(str(self.cat),str(self.shard1),as_of=DAY,
                limit=1,start_index=0,fetcher=lambda c,d:payload(c))
        collect(str(self.cat),str(self.shard2),as_of=DAY,
                limit=2,start_index=1,fetcher=lambda c,d:payload(c))

    def test_three_codes_from_separate_shards_merge_and_replay(self):
        report=combine(str(self.cat),[str(self.shard1),str(self.shard2)],
                       str(self.out),as_of=DAY)
        self.assertTrue(report["source_complete"])
        self.assertEqual(report["successful_codes"],3)
        self.assertEqual(report["remaining_codes"],0)
        self.assertFalse(report["other_semantics_verified"])
        audit=evaluate(str(self.cat),str(self.out),min_chapters=1)
        self.assertEqual(audit["replayed_source_measures"],3)
        self.assertTrue(audit["all_collected_measure_responses_verified"])
        self.assertFalse(audit["independent_catalogue_scope_ok"])

    def test_overlap_same_raw_bytes_deduplicated(self):
        extra=self.root/"overlap.db"
        collect(str(self.cat),str(extra),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d:payload(c))
        report=combine(str(self.cat),[str(self.shard1),str(self.shard2),
                                      str(extra)],str(self.out),as_of=DAY)
        self.assertEqual(report["duplicate_identical_codes"],1)
        self.assertEqual(report["merged_requests"],3)

    def test_overlap_conflict_fails_and_preserves_previous_good_db(self):
        combine(str(self.cat),[str(self.shard1),str(self.shard2)],
                str(self.out),as_of=DAY)
        good=self.out.read_bytes()
        conflicting=self.root/"conflicting.db"
        collect(str(self.cat),str(conflicting),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d:payload(c,"12%"))
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            combine(str(self.cat),[str(self.shard1),str(conflicting)],
                    str(self.out),as_of=DAY)
        self.assertEqual(self.out.read_bytes(),good)

    def test_duplicate_shard_measure_tampering_cannot_bypass_replay(self):
        copy=self.root/"tampered-duplicate.db"
        import shutil
        shutil.copyfile(self.shard1,copy)
        db=sqlite3.connect(copy)
        db.execute("UPDATE measures SET measure_type='999'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"measure rows differ"):
            combine(str(self.cat),[str(self.shard1),str(self.shard2),
                                   str(copy)],str(self.out),as_of=DAY)
        self.assertFalse(self.out.exists())

    def test_failed_then_successful_overlap_needs_explicit_reconciliation(self):
        failed=self.root/"failed.db"
        def reject(c,d):
            raise RuntimeError("429")
        collect(str(self.cat),str(failed),as_of=DAY,limit=1,start_index=0,
                fetcher=reject)
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            combine(str(self.cat),[str(failed),str(self.shard1)],
                    str(self.out),as_of=DAY)
        self.assertFalse(self.out.exists())

    def test_explicit_later_verified_retry_heals_only_failed_request(self):
        import time
        failed=self.root/"earlier-error.db"
        recovered=self.root/"later-ok.db"
        collect(str(self.cat),str(failed),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d: (_ for _ in ()).throw(RuntimeError("429")))
        time.sleep(0.01)
        collect(str(self.cat),str(recovered),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d:payload(c))
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            combine(str(self.cat),[str(failed),str(recovered)],
                    str(self.out),as_of=DAY)
        result=combine(str(self.cat),[str(failed),str(recovered)],
                       str(self.out),as_of=DAY,resolve_failures=True)
        self.assertEqual(result["resolved_source_failures"],1)
        self.assertEqual(result["failed_codes"],0)
        self.assertEqual(result["successful_codes"],1)
        self.assertEqual(result["remaining_codes"],2)
        self.assertFalse(result["source_complete"])
        db=sqlite3.connect(self.out)
        try:
            self.assertEqual(db.execute("SELECT status FROM requests").fetchone()[0],"ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measures").fetchone()[0],1)
        finally:db.close()

    def test_source_tampering_rejected(self):
        db=sqlite3.connect(self.shard1)
        db.execute("UPDATE requests SET response_sha256=?",
                   ("a"*64,))
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError,"fingerprint mismatch"):
            combine(str(self.cat),[str(self.shard1)],str(self.out),as_of=DAY)
        self.assertFalse(self.out.exists())

    def test_partial_shards_do_not_claim_full_and_bad_date_rejected(self):
        report=combine(str(self.cat),[str(self.shard1)],
                       str(self.out),as_of=DAY)
        self.assertFalse(report["source_complete"])
        self.assertEqual(report["remaining_codes"],2)
        other=self.root/"not-date.db"
        with self.assertRaisesRegex(ValueError,"metadata mismatch"):
            combine(str(self.cat),[str(self.shard1)],str(other),
                    as_of=date(2026,10,13))
        with self.assertRaisesRegex(ValueError,"cannot overwrite"):
            combine(str(self.cat),[str(self.shard1)],str(self.shard1),as_of=DAY)

if __name__=="__main__":
    unittest.main()
