"""NI full-chapter batch planner: independently replay official source checkpoints."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as catalogue_build
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_merge import combine
from src.target_duty.xi_run_plan import build_plan

DAY = date(2026, 10, 14)


def item(chapter):
    code=f"{chapter}01000000"
    return {"id":str(int(chapter)*100),"type":"goods_nomenclature",
            "attributes":{
                "goods_nomenclature_item_id":code,
                "goods_nomenclature_sid":int(chapter)*100,
                "description":"source fixture","producline_suffix":"80",
                "declarable":True,"validity_start_date":"2020-01-01",
                "validity_end_date":None}}


def measures(code):
    return json.dumps({"data":{
        "id":code,"type":"commodity",
        "attributes":{"goods_nomenclature_item_id":code},
        "relationships":{"import_measures":{"data":[]}}},
        "included":[]}).encode()


class FullManagedXIPlanTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.catalogue=root/"frozen.db"
        self.manifest=root/"frozen.json"
        self.first=root/"first.db"
        self.second=root/"second.db"
        self.merged=root/"merged.db"
        listing={"data":[{"id":str(i)} for i in range(1,98)]}
        payloads={f"{i:02d}":{"data":[item(f"{i:02d}")]}
                  for i in range(1,98)}
        catalogue_build(listing,payloads,str(self.catalogue),as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)
        freeze(str(self.catalogue),str(self.manifest))

    def test_next_batch_walks_all_97_official_chapters_without_skip(self):
        state=build_plan(str(self.catalogue),manifest=str(self.manifest),
                         batch_size=2)
        self.assertEqual(state["official_chapters_collected"],97)
        self.assertEqual(state["remaining_codes"],97)
        self.assertEqual(state["next_shard"]["start_index"],0)
        self.assertEqual(state["next_shard"]["first_code"],"0101000000")
        self.assertFalse(state["all_managed_source_responses_verified"])
        self.assertFalse(state["ni_other_verified"])
        collect(str(self.catalogue),str(self.first),as_of=DAY,
                start_index=0,limit=2,fetcher=lambda code,day:measures(code))
        state=build_plan(str(self.catalogue),manifest=str(self.manifest),
                         checkpoint=str(self.first),batch_size=3)
        self.assertTrue(state["previous_checkpoint_replayed"])
        self.assertEqual(state["verified_successful_codes"],2)
        self.assertEqual(state["remaining_codes"],95)
        self.assertEqual(state["next_shard"]["start_index"],2)
        self.assertEqual(state["next_shard"]["end_index_exclusive"],5)
        self.assertFalse(state["production_eligible"])

    def test_retries_explicit_failed_request_before_advancing(self):
        collect(str(self.catalogue),str(self.first),as_of=DAY,
                start_index=0,limit=2,
                fetcher=lambda code,day: (
                    measures(code) if code=="0101000000"
                    else (_ for _ in ()).throw(RuntimeError("HTTP 503"))))
        plan=build_plan(str(self.catalogue),manifest=str(self.manifest),
                        checkpoint=str(self.first),batch_size=3)
        self.assertEqual(plan["failed_requests"],1)
        self.assertEqual(plan["missing_requests"],95)
        self.assertEqual(plan["next_shard"]["start_index"],1)
        self.assertEqual(plan["next_shard"]["failed_codes_in_window"],1)
        collect(str(self.catalogue),str(self.second),as_of=DAY,
                start_index=1,limit=3,
                skip_successes_from=str(self.first),
                fetcher=lambda code,day:measures(code))
        combine(str(self.catalogue),[str(self.first),str(self.second)],
                str(self.merged),as_of=DAY,resolve_failures=True)
        next_step=build_plan(str(self.catalogue),manifest=str(self.manifest),
                             checkpoint=str(self.merged),batch_size=2)
        self.assertEqual(next_step["failed_requests"],0)
        self.assertEqual(next_step["verified_successful_codes"],4)
        self.assertEqual(next_step["next_shard"]["start_index"],4)

    def test_retry_failed_first_can_jump_past_earlier_missing_codes(self):
        collect(str(self.catalogue),str(self.first),as_of=DAY,
                start_index=4,limit=1,
                fetcher=lambda c,d: (_ for _ in ()).throw(RuntimeError("HTTP 503")))
        ordinary=build_plan(str(self.catalogue),manifest=str(self.manifest),
                            checkpoint=str(self.first),batch_size=2)
        retry=build_plan(str(self.catalogue),manifest=str(self.manifest),
                         checkpoint=str(self.first),batch_size=2,
                         retry_failed_first=True)
        self.assertEqual(ordinary["next_shard"]["start_index"],0)
        self.assertEqual(retry["next_shard"]["start_index"],4)
        self.assertEqual(retry["next_shard"]["failed_codes_in_window"],1)
        self.assertEqual(retry["failed_requests"],1)
        self.assertEqual(retry["missing_requests"],96)
        self.assertEqual(retry["incomplete_chapters"],97)
        self.assertEqual(retry["chapter_gaps"][4]["failed_samples"],["0501000000"])
        self.assertFalse(retry["production_eligible"])

    def test_corrupted_prior_success_never_marked_complete(self):
        collect(str(self.catalogue),str(self.first),as_of=DAY,
                start_index=0,limit=2,fetcher=lambda c,d:measures(c))
        db=sqlite3.connect(self.first)
        db.execute("UPDATE requests SET response_sha256=? WHERE code='0101000000'",
                   ("f"*64,))
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            build_plan(str(self.catalogue),manifest=str(self.manifest),
                       checkpoint=str(self.first))

    def test_explicit_window_must_contain_uncollected_requests(self):
        collect(str(self.catalogue),str(self.first),as_of=DAY,
                start_index=0,limit=2,fetcher=lambda c,d:measures(c))
        with self.assertRaisesRegex(ValueError,"no pending"):
            build_plan(str(self.catalogue),manifest=str(self.manifest),
                       checkpoint=str(self.first),start_index=0,batch_size=2)
        with self.assertRaisesRegex(ValueError,"index outside"):
            build_plan(str(self.catalogue),manifest=str(self.manifest),
                       start_index=97)
        with self.assertRaisesRegex(ValueError,"batch size"):
            build_plan(str(self.catalogue),manifest=str(self.manifest),batch_size=0)

    def test_unpinned_or_replaced_catalogue_cannot_plan(self):
        with self.assertRaisesRegex(ValueError,"frozen manifest"):
            build_plan(str(self.catalogue),batch_size=10)
        db=sqlite3.connect(self.catalogue)
        db.execute("UPDATE chapters SET loaded_at='tampered' WHERE chapter='01'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            build_plan(str(self.catalogue),manifest=str(self.manifest))

    def test_public_canary_never_claims_full_ni_data(self):
        canary=self.catalogue.parent/"canary.db"
        catalogue_build({"data":[{"id":"1"}]},
                        {"01":{"data":[item("01")]}},
                        str(canary),as_of=DAY,public=True)
        with self.assertRaisesRegex(ValueError,"frozen manifest"):
            build_plan(str(canary))
        plan=build_plan(str(canary),batch_size=1,canary=True)
        self.assertEqual(plan["source_scope"],"partial_or_fixture")
        self.assertFalse(plan["all_managed_source_responses_verified"])
        self.assertFalse(plan["ni_other_verified"])
        db=sqlite3.connect(canary)
        db.execute("UPDATE metadata SET value='managed_full' WHERE key='listing_scope'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"partial catalogue"):
            build_plan(str(canary),batch_size=1,canary=True)


if __name__ == "__main__":
    unittest.main()
