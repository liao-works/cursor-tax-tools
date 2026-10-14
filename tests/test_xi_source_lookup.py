"""Read-only NI commodity lookup never converts source gaps into zero duty."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as catalogue_build
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_source_lookup import lookup_many
from test_xi_origin_compare import payload

DAY=date(2026,10,14)
CODE1="0101210000"
CODE2="0101290000"
CODE3="0101300000"

def item(code,n):
    return {"id":str(n),"type":"goods_nomenclature","attributes":{
        "goods_nomenclature_item_id":code,"goods_nomenclature_sid":n,
        "producline_suffix":"80","description":"fixture","declarable":True,
        "validity_start_date":"2020-01-01","validity_end_date":None}}

class XIReadOnlyLookupTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.cat=self.root/"catalogue.db"
        self.raw=self.root/"raw.db"
        catalogue_build({"data":[{"id":"1"}]},
                        {"01":{"data":[item(x,i+1) for i,x in
                                       enumerate((CODE1,CODE2,CODE3))]}},
                        str(self.cat),as_of=DAY,public=True)

    def test_all_source_states_no_unjustified_zero_and_exact_linked_base(self):
        def source(code,when):
            if code==CODE1:
                doc=json.loads(payload(code,[
                    ("103measure","103","1011","",True),
                    ("105measure","105","1011","5% + 15 EUR / 100 kg",False),
                ]))
                node=doc["included"][0]
                node["relationships"]["duty_expression"]={
                    "data":{"type":"duty_expression","id":"base0"}}
                node["relationships"]["measure_components"]={
                    "data":[{"type":"measure_component","id":"component0"}]}
                doc["included"].append({
                    "type":"duty_expression","id":"base0",
                    "attributes":{"base":"0.00 %"}})
                return json.dumps(doc).encode()
            if code==CODE2:
                return payload(code,[])
            raise AssertionError("no third source expected")
        collect(str(self.cat),str(self.raw),as_of=DAY,
                limit=2,fetcher=source)
        result=lookup_many(str(self.cat),str(self.raw),
                           [CODE1,CODE2,CODE3,"0101999999"],min_chapters=1)
        self.assertEqual(len(result["results"]),4)
        evidence=result["results"][0]
        self.assertEqual(evidence["status"],"unfiltered_raw_source_candidates")
        self.assertEqual(evidence["third_country_103_105_candidate_count"],2)
        self.assertEqual(evidence["candidate_evidence"][0]["linked_duty_base"],"0.00 %")
        self.assertTrue(evidence["candidate_evidence"][0]["conditions_present"])
        self.assertEqual(evidence["candidate_evidence"][1]["resolved_duty_expression"],
                         "5% + 15 EUR / 100 kg")
        self.assertIsNone(evidence["payable_rate"])
        self.assertEqual(result["results"][1]["status"],"source_response_no_import_measures")
        self.assertEqual(result["results"][2]["status"],"source_not_collected")
        self.assertEqual(result["results"][3]["status"],
                         "not_in_enumerated_active_declarable_xi_catalogue")
        self.assertTrue(all(x["payable_rate"] is None for x in result["results"]))
        self.assertFalse(result["managed_full_source_coverage_proven"])
        self.assertFalse(result["production_eligible"])

    def test_failed_response_retained_as_error_not_zero(self):
        collect(str(self.cat),str(self.raw),as_of=DAY,limit=1,
                fetcher=lambda code,when:(_ for _ in ()).throw(
                    RuntimeError("official 503")))
        result=lookup_many(str(self.cat),str(self.raw),[CODE1],min_chapters=1)
        self.assertEqual(result["results"][0]["status"],"source_error")
        self.assertIn("official 503",result["results"][0]["source_error"])
        self.assertIsNone(result["results"][0]["payable_rate"])

    def test_bad_codes_and_tampered_checkpoint_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"distinct ten-digit"):
            lookup_many(str(self.cat),str(self.raw),["bad"],min_chapters=1)
        with self.assertRaisesRegex(ValueError,"distinct ten-digit"):
            lookup_many(str(self.cat),str(self.raw),[CODE1,CODE1],min_chapters=1)
        with self.assertRaisesRegex(ValueError,"1..1000"):
            lookup_many(str(self.cat),str(self.raw),[],min_chapters=1)
        collect(str(self.cat),str(self.raw),as_of=DAY,limit=1,
                fetcher=lambda code,day:payload(code,[]))
        with sqlite3.connect(self.raw) as db:
            db.execute("UPDATE requests SET response_sha256='corrupted'")
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            lookup_many(str(self.cat),str(self.raw),[CODE1],min_chapters=1)

if __name__=="__main__":
    unittest.main()
