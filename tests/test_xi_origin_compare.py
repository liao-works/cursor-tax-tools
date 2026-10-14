"""Offline XI origin comparison contract tests. No tax rate inferred."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.target_duty.xi_origin_compare import build, extract, url

DAY=date(2026,10,14)
CODE="0101210000"


def payload(code, data):
    related=[{"id":str(mid),"type":"measure"} for mid,*_ in data]
    included=[]
    for mid,kind,geography,rate,condition in data:
        included.append({
            "id":str(mid),"type":"measure",
            "attributes":{"resolved_duty_expression":rate,
                          "effective_start_date":"2020-01-01",
                          "effective_end_date":None},
            "relationships":{
                "measure_type":{"data":{"type":"measure_type","id":kind}},
                "geographical_area":{"data":{"type":"geographical_area","id":geography}},
                "measure_conditions":{"data":([{"id":"X1","type":"measure_condition"}]
                                               if condition else [])},
                "additional_code":{"data":None},
                "excluded_countries":{"data":[]},
            }})
    return json.dumps({
        "data":{"id":code,"type":"commodity",
                "attributes":{"goods_nomenclature_item_id":code},
                "relationships":{"import_measures":{"data":related}}},
        "included":included
    }).encode()


class XIOriginComparison(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"review.db"

    def test_explicit_source_query_and_provenance(self):
        params=parse_qs(urlparse(url(CODE,DAY,"CN",public=True)).query)
        self.assertEqual(params["filter[geographical_area_id]"],["CN"])
        self.assertEqual(params["as_of"],["2026-10-14"])
        self.assertNotIn("filter[geographical_area_id]",
                         parse_qs(urlparse(url(CODE,DAY,None,public=False)).query))
        with self.assertRaises(ValueError):
            url(CODE,DAY,"OTHER",public=True)
        with self.assertRaises(ValueError):
            url("123",DAY,"CN",public=True)

    def test_two_origins_preserve_103_105_conditional_and_differences(self):
        all_records=[
            ("1","103","1011","5% + 13 EUR / 100 kg",True),
            ("2","105","1011","0%",False),
            ("3","142","US","0%",False)
        ]
        options={
            None:payload(CODE,all_records),
            "US":payload(CODE,[all_records[0],all_records[2]]),
            "CN":payload(CODE,[all_records[0],all_records[1]]),
        }
        calls=[]
        def source(code,when,origin):
            calls.append(origin)
            return options[origin]
        result=build(CODE,DAY,["US","CN"],str(self.db),
                     public=True,fetcher=source)
        self.assertEqual(calls,[None,"US","CN"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])
        self.assertEqual(result["unfiltered_measure_count"],3)
        self.assertEqual(result["comparisons"][0]["third_country_measure_types"],["103"])
        self.assertEqual(result["comparisons"][1]["third_country_measure_types"],["103","105"])
        self.assertTrue(result["comparisons"][0]["third_country_measure_evidence"][0]["has_conditions"])
        self.assertEqual(result["comparisons"][0]["third_country_measure_evidence"][0]["expression"],
                         "5% + 13 EUR / 100 kg")
        self.assertEqual(result["comparisons"][0]["missing_vs_unfiltered"],["2"])
        self.assertEqual(result["comparisons"][1]["missing_vs_unfiltered"],["3"])
        self.assertIsNone(result["comparisons"][0]["applicable_rate"])
        db=sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM context").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measures").fetchone()[0],7)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertTrue(db.execute(
                "SELECT compressed_body FROM context WHERE origin='CN'"
            ).fetchone()[0])
        finally:db.close()

    def test_bad_source_does_not_overwrite_existing_valid_evidence(self):
        before=payload(CODE,[("1","103","1011","7%",False)])
        build(CODE,DAY,["CN"],str(self.db),public=True,
              fetcher=lambda c,d,o:before)
        original=self.db.read_bytes()
        with self.assertRaisesRegex(ValueError,"another code"):
            build(CODE,DAY,["CN"],str(self.db),public=True,
                  fetcher=lambda c,d,o:payload("2222222222",[]))
        self.assertEqual(self.db.read_bytes(),original)

    def test_partial_or_ambiguous_source_is_not_0_percent(self):
        def source(c,d,o):
            if o is None:
                return payload(c,[("1","103","1011","8%",False)])
            return payload(c,[])
        result=build(CODE,DAY,["US"],str(self.db),
                     public=True,fetcher=source)
        self.assertEqual(result["comparisons"][0]["filtered_measure_count"],0)
        self.assertEqual(result["comparisons"][0]["third_country_103_105_count"],0)
        self.assertEqual(result["comparisons"][0]["status"],
                         "manual_applicability_review_required")
        self.assertIsNone(result["comparisons"][0]["applicable_rate"])
        with self.assertRaisesRegex(ValueError,"1-3"):
            build(CODE,DAY,["CN","CN"],str(self.db),public=True,fetcher=source)
        with self.assertRaisesRegex(ValueError,"1-3"):
            build(CODE,DAY,["US","CN","FR","DE"],str(self.db),public=True,fetcher=source)

    def test_missing_import_relationship_is_failure_not_empty_rate(self):
        doc=json.loads(payload(CODE,[]))
        del doc["data"]["relationships"]["import_measures"]
        with self.assertRaisesRegex(ValueError,"import_measures"):
            extract(CODE,json.dumps(doc).encode())


if __name__=="__main__":
    unittest.main()
