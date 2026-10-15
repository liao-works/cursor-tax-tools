"""The eight verified expired XI source codes are fetched anew or all fail."""
import csv
import json
import sqlite3
import tempfile
import unittest
import zlib
from datetime import date
from pathlib import Path

from src.target_duty.xi_targeted_delta_capture import review,sha

DAY=date(2026,10,15)


def payload(code):
    return json.dumps({
      "data":{"id":code,"type":"commodity",
              "attributes":{"goods_nomenclature_item_id":code},
              "relationships":{"import_measures":{"data":[
                  {"type":"measure","id":"m1"}]}}},
      "included":[
        {"type":"measure","id":"m1",
         "attributes":{"effective_start_date":"2022-01-01","effective_end_date":None},
         "relationships":{"measure_type":{"data":{"type":"measure_type","id":"103"}},
                          "geographical_area":{"data":{"type":"geographical_area","id":"1011"}},
                          "duty_expression":{"data":{"type":"duty_expression","id":"d1"}},
                          "measure_conditions":{"data":[]},"excluded_countries":{"data":[]},
                          "measure_condition_permutation_groups":{"data":[]},
                          "measure_components":{"data":[]},"resolved_measure_components":{"data":[]}}},
        {"type":"measure_type","id":"103","attributes":{}},
        {"type":"geographical_area","id":"1011","attributes":{}},
        {"type":"duty_expression","id":"d1","attributes":{"base":"10.00 %"}}
      ]
    }).encode()


class XITargetedCaptureTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.p=Path(folder.name)
        self.queue=self.p/"q.csv"
        self.qreport=self.p/"q.json"
        self.db=self.p/"live.db"
        self.report=self.p/"live.json"
        with self.queue.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=[
                "code","measure_id","review_reason","production_eligible",
                "payable_rate","effective_end"])
            w.writeheader()
            for i in (10,20):
                w.writerow({"code":f"080510{i:04d}","measure_id":"old-m",
                    "review_reason":"KNOWN_103_105_SOURCE_END",
                    "production_eligible":"false","payable_rate":"",
                    "effective_end":"2026-10-15"})
        self.proof={
            "kind":"xi-known-candidate-end-affected-code-queue-v1",
            "production_eligible":False,"ni_other_payable_verified":False,
            "full_original_source_responses":16728,
            "impact_queue_csv_sha256":sha(str(self.queue)),
            "review_through":"2026-10-15",
            "candidate_effective_end_measures":2,
            "affected_distinct_codes":2,
            "original_condition_source_csv_sha256":"a"*64
        }
        self.write()

    def write(self):
        self.qreport.write_text(json.dumps(self.proof))

    def collect(self,fetcher):
        return review(str(self.queue),str(self.qreport),str(self.db),
                      str(self.report),as_of=DAY,fetcher=fetcher,sleep_seconds=0)

    def test_complete_selected_fetch_preserves_raw_original_replies(self):
        r=self.collect(lambda code,day: payload(code))
        self.assertEqual(r["verified_public_responses"],2)
        self.assertEqual(r["failed_responses"],0)
        self.assertTrue(r["source_success_complete"])
        self.assertFalse(r["NI_Other_payable_certified"])
        with sqlite3.connect(self.db) as db:
            for code,fingerprint,blob in db.execute(
                    "SELECT code,response_sha256,compressed_raw_json FROM source_responses"):
                self.assertEqual(sha_bytes(zlib.decompress(blob)),fingerprint)
                self.assertEqual(json.loads(zlib.decompress(blob))["data"]["id"],code)

    def test_one_http_failure_must_not_write_apparent_complete_database(self):
        def fetcher(code,day):
            if code.endswith("0020"):
                raise ConnectionError("HTTP 503")
            return payload(code)
        r=self.collect(fetcher)
        self.assertEqual(r["failed_responses"],1)
        self.assertFalse(r["source_success_complete"])
        self.assertFalse(self.db.exists())
        self.assertEqual(len(r["source_measure_review"]),2)

    def test_source_queue_sha_mutation_blocks_fetch(self):
        self.queue.write_bytes(self.queue.read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"untrusted"):
            self.collect(lambda c,d: payload(c))

    def test_wrong_official_code_must_never_pass(self):
        r=self.collect(lambda code,day: payload("9999999999"))
        self.assertEqual(r["failed_responses"],2)
        self.assertFalse(self.db.exists())


def sha_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()

if __name__=="__main__":
    unittest.main()
