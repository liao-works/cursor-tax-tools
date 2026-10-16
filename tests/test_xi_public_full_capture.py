"""Strict public XI whole-inventory evidence without managed-source promotion."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build
from src.target_duty.xi_merge import combine
from src.target_duty.xi_public_full_capture import (
    freeze_public, verify_public, collect_partition, public_acceptance)

DAY=date(2026,10,14)

def commodity(chapter):
    code=chapter+"01000000"
    return {"id":str(int(chapter)*100), "type":"goods_nomenclature",
        "attributes":{"goods_nomenclature_item_id":code,
            "goods_nomenclature_sid":int(chapter)*100,
            "description":"Fixture", "producline_suffix":"80",
            "declarable":True, "validity_start_date":"2020-01-01",
            "validity_end_date":None}}

def payload(code,day):
    return json.dumps({
        "data":{"type":"commodity","id":code,
            "attributes":{"goods_nomenclature_item_id":code},
            "relationships":{"import_measures":{"data":[]}}},"included":[]}).encode()

class PublicCompleteTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.catalogue=str(root/"public.db")
        self.manifest=str(root/"public-manifest.json")
        self.merged=str(root/"merged.db")
        listing={"data":[{"id":str(i)} for i in range(1,98)]}
        chapters={f"{i:02d}":{"data":[commodity(f"{i:02d}")]}
                  for i in range(1,98)}
        build(listing,chapters,self.catalogue,as_of=DAY,
              public=True,public_full_audit=True,official_listed_total=97)
        freeze_public(self.catalogue,self.manifest)
        self.root=root

    def test_untrusted_97_chapter_source_complete_but_never_managed_or_payable(self):
        shards=[]
        for i in range(4):
            target=str(self.root/f"shard-{i}.db")
            r=collect_partition(self.catalogue,self.manifest,target,
                                worker=i,workers=4,batch_size=11,interval=0,
                                fetcher=payload)
            self.assertTrue(r["complete"])
            self.assertFalse(r["managed_source_verified"])
            shards.append(target)
        combine(self.catalogue,shards,self.merged,as_of=DAY)
        result=public_acceptance(self.catalogue,self.manifest,self.merged)
        self.assertEqual(result["expected"],97)
        self.assertEqual(result["responses_replayed"],97)
        self.assertTrue(result["public_full_raw_source_coverage"])
        self.assertFalse(result["managed_full_source_coverage_proven"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])

    def test_eight_partition_merging_replays_every_code_without_overlap(self):
        shards=[]
        assigned=0
        for i in range(8):
            target=str(self.root/f"eight-{i}.db")
            r=collect_partition(self.catalogue,self.manifest,target,
                                worker=i,workers=8,batch_size=7,interval=0,
                                fetcher=payload)
            self.assertTrue(r["complete"])
            self.assertEqual(r["failed"],0)
            self.assertEqual(r["missing"],0)
            assigned+=r["expected"]
            shards.append(target)
        self.assertEqual(assigned,97)
        combined=combine(self.catalogue,shards,self.merged,as_of=DAY)
        accepted=public_acceptance(self.catalogue,self.manifest,self.merged)
        self.assertTrue(accepted["public_full_raw_source_coverage"])
        self.assertEqual(accepted["responses_replayed"],97)
        self.assertFalse(accepted["production_eligible"])

    def test_sixteen_partition_zero_overlap_complete_replay(self):
        shards=[]
        expected_sum=0
        for worker in range(16):
            path=str(self.root/f"sixteen-{worker}.db")
            result=collect_partition(self.catalogue,self.manifest,path,
                                     worker=worker,workers=16,batch_size=3,interval=0,
                                     fetcher=payload)
            self.assertTrue(result["complete"])
            self.assertEqual(result["failed"],0)
            self.assertEqual(result["missing"],0)
            expected_sum+=result["expected"]
            shards.append(path)
        self.assertEqual(expected_sum,97)
        combine(self.catalogue,shards,self.merged,as_of=DAY)
        accepted=public_acceptance(self.catalogue,self.manifest,self.merged)
        self.assertEqual(accepted["responses_replayed"],97)
        self.assertEqual(accepted["failed"],0)
        self.assertEqual(accepted["missing"],0)

    def test_second_pass_retries_failed_and_missing_not_successful_codes(self):
        calls={}
        first_code="0101000000"
        def once_unavailable(code,day):
            calls[code]=calls.get(code,0)+1
            if code==first_code and calls[code]==1:
                raise OSError("HTTP 503 temporary upstream failure")
            return payload(code,day)
        path=str(self.root/"retry-0.db")
        first=collect_partition(self.catalogue,self.manifest,path,
                                worker=0,workers=16,batch_size=3,interval=0,
                                fetcher=once_unavailable)
        self.assertFalse(first["complete"])
        self.assertEqual(first["failed"],1)
        self.assertGreater(first["missing"],0)
        successful_before={c:n for c,n in calls.items() if c!=first_code}
        second=collect_partition(self.catalogue,self.manifest,path,
                                 worker=0,workers=16,batch_size=3,interval=0,
                                 fetcher=once_unavailable)
        self.assertTrue(second["complete"])
        self.assertEqual(second["successful"],second["expected"])
        self.assertEqual(second["failed"],0)
        self.assertEqual(second["missing"],0)
        self.assertEqual(calls[first_code],2)
        for code,n in successful_before.items():
            self.assertEqual(calls[code],n,"successful first-pass responses must not be fetched again")

    def test_second_pass_persistent_failure_remains_incomplete(self):
        def unavailable(code,day):
            if code=="0101000000":
                raise OSError("HTTP 429 persistent throttling")
            return payload(code,day)
        path=str(self.root/"retry-still-missing.db")
        for attempt in range(2):
            report=collect_partition(self.catalogue,self.manifest,path,
                                     worker=0,workers=16,batch_size=3,interval=0,
                                     fetcher=unavailable)
        self.assertFalse(report["complete"])
        self.assertEqual(report["failed"],1)
        self.assertFalse(report["production_eligible"])

    def test_failures_not_mistaken_for_zero_duty(self):
        def broken(code, day):
            if code=="0101000000":
                raise RuntimeError("public upstream unavailable")
            return payload(code,day)
        target=str(self.root/"failed.db")
        report=collect_partition(self.catalogue,self.manifest,target,worker=0,
                                 workers=4,batch_size=10,interval=0,fetcher=broken)
        self.assertEqual(report["failed"],1)
        self.assertFalse(report["complete"])
        self.assertGreater(report["missing"],0)
        fixed=collect_partition(self.catalogue,self.manifest,target,worker=0,
                                workers=4,batch_size=10,interval=0,fetcher=payload)
        self.assertEqual(fixed["failed"],0)
        self.assertTrue(fixed["complete"])

    def test_frozen_source_cannot_be_replaced(self):
        self.assertEqual(verify_public(self.catalogue,self.manifest)["official_chapters"],97)
        m=Path(self.manifest)
        d=json.loads(m.read_text());d["catalogue_sha256"]="f"*64
        m.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError,"mismatch"):
            verify_public(self.catalogue,self.manifest)

    def test_partial_catalogue_cannot_claim_public_full(self):
        target=str(self.root/"partial.db")
        build({"data":[{"id":"1"}]},
              {"01":{"data":[commodity("01")]}},
              target,as_of=DAY,public=True)
        with self.assertRaisesRegex(ValueError,"incomplete or unsafe"):
            freeze_public(target,str(self.root/"nope.json"))

if __name__=="__main__":
    unittest.main()
