"""97-chapter shard completeness integration: no network or production writes."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_parallel_capture import bounds, crawl_shard, make_plan
from src.target_duty.xi_merge import combine
from src.target_duty.xi_acceptance import evaluate

DAY = date(2026, 10, 14)


def commodity(chapter):
    code = chapter + "01000000"
    return {"type": "goods_nomenclature", "id": str(int(chapter)*100),
            "attributes": {"goods_nomenclature_item_id": code,
                           "goods_nomenclature_sid": int(chapter)*100,
                           "description": "Synthetic offline test",
                           "producline_suffix": "80", "declarable": True,
                           "validity_start_date": "2020-01-01",
                           "validity_end_date": None}}


def response(code, day):
    return json.dumps({
        "data": {"type": "commodity", "id": code,
                 "attributes": {"goods_nomenclature_item_id": code},
                 "relationships": {"import_measures": {"data": []}}},
        "included": []}).encode("utf-8")


class ManagedFourWayXIShards(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.cat=str(self.root/"catalogue.db")
        self.manifest=str(self.root/"catalogue.json")
        chapters={f"{i:02d}":{"data":[commodity(f"{i:02d}")]}
                  for i in range(1,98)}
        build_catalogue({"data":[{"id":str(i)} for i in range(1,98)]},
                        chapters, self.cat,as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)
        freeze(self.cat,self.manifest)

    def test_disjoint_partition_has_exact_inventory_coverage(self):
        plan=make_plan(self.cat,self.manifest,workers=4)
        self.assertEqual([x["limit"] for x in plan["shards"]],[25,24,24,24])
        self.assertEqual([x["start_index"] for x in plan["shards"]],[0,25,49,73])
        self.assertEqual(plan["expected_codes"],97)
        self.assertFalse(plan["production_eligible"])
        with self.assertRaises(ValueError):
            bounds(97,4,4)
        with self.assertRaises(ValueError):
            make_plan(self.cat,self.manifest,workers=98)

    def test_four_real_sqlite_shards_merge_to_independent_complete_source(self):
        shards=[]
        for index in range(4):
            output=str(self.root/f"xi-shard-{index}.db")
            result=crawl_shard(self.cat,self.manifest,output,
                               index=index,workers=4,as_of=DAY,
                               fetcher=response,batch_size=10,interval=0)
            self.assertTrue(result["complete"])
            self.assertEqual(result["missing"],0)
            self.assertEqual(result["source_errors"],0)
            self.assertFalse(result["production_eligible"])
            shards.append(output)
        merged=str(self.root/"merged.db")
        merge=combine(self.cat,shards,merged,as_of=DAY)
        self.assertEqual(merge["successful_codes"],97)
        audit=evaluate(self.cat,merged,min_chapters=90,strict=True)
        self.assertEqual(audit["requests_recorded"],97)
        self.assertEqual(audit["missing_codes"],0)
        self.assertTrue(audit["all_collected_measure_responses_verified"])
        self.assertTrue(audit["independent_catalogue_scope_ok"])
        self.assertFalse(audit["production_eligible"])
        self.assertFalse(audit["other_semantics_verified"])

    def test_missing_response_is_retained_and_full_acceptance_blocks(self):
        def fetch(code,day):
            if code=="0101000000":
                raise RuntimeError("Simulated HMRC upstream 503")
            return response(code,day)
        path=str(self.root/"failed-shard.db")
        partial=crawl_shard(self.cat,self.manifest,path,index=0,workers=4,
                            as_of=DAY,fetcher=fetch,batch_size=10,interval=0)
        self.assertFalse(partial["complete"])
        self.assertEqual(partial["source_errors"],1)
        self.assertEqual(partial["verified"],24)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT status FROM requests WHERE code='0101000000'").fetchone()[0],
                             "source_error")
        recovered=crawl_shard(self.cat,self.manifest,path,index=0,workers=4,
                               as_of=DAY,fetcher=response,batch_size=10,interval=0)
        self.assertTrue(recovered["complete"])
        self.assertEqual(recovered["source_errors"],0)
        self.assertFalse(recovered["ni_other_verified"])

    def test_cannot_resume_from_another_workers_checkpoint(self):
        path=str(self.root/"wrong-shard.db")
        crawl_shard(self.cat,self.manifest,path,index=0,workers=4,
                    as_of=DAY,fetcher=response,batch_size=10,interval=0)
        with self.assertRaisesRegex(ValueError,"other worker"):
            crawl_shard(self.cat,self.manifest,path,index=1,workers=4,
                        as_of=DAY,fetcher=response,batch_size=10,interval=0)


if __name__=="__main__":
    unittest.main()
