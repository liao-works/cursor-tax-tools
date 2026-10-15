"""Actual historical NI 103/105 candidate end impact, full-source SHA guarded."""
import copy
import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_date_impact_queue import collect,sha


class XIExpiryImpactTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.p=Path(folder.name)
        self.source=self.p/"conditions.csv"
        self.summary=self.p/"conditions.json"
        self.output=self.p/"review.csv"
        self.report=self.p/"review.json"
        self.names=["code","measure_id","measure_type","geography_id",
                    "effective_end","production_eligible","ni_other_payable_rate"]
        with self.source.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=self.names)
            w.writeheader()
            for i in range(10002):
                w.writerow({
                    "code":"0101210000" if i<8 else "0101299000",
                    "measure_id":str(i),"measure_type":"103",
                    "geography_id":"1011",
                    "effective_end":"2026-10-15" if i<8 else
                                    "2026-10-31" if i<55 else "",
                    "production_eligible":"false","ni_other_payable_rate":""
                })
        self.proof={"kind":"xi-third-country-raw-conditions-evidence-v1",
            "as_of":"2026-10-14","source_full_complete":True,
            "expected_codes":16728,"verified_original_responses":16728,
            "candidate_measures":10002,"candidate_codes":2,
            "ni_other_verified":False,"production_eligible":False,
            "output_csv_sha256":sha(str(self.source))}
        self.save()

    def save(self):
        self.summary.write_text(json.dumps(self.proof))

    def audit(self,on=date(2026,10,15)):
        return collect(str(self.source),str(self.summary),
                       str(self.output),str(self.report),through=on)

    def test_actual_source_shape_eight_oct_15_expirations_and_exact_codes(self):
        r=self.audit()
        self.assertEqual(r["candidate_effective_end_measures"],8)
        self.assertEqual(r["affected_distinct_codes"],1)
        self.assertEqual(r["ending_on_dates"],{"2026-10-15":8})
        self.assertFalse(r["production_eligible"])
        with self.output.open(newline="") as f:
            rows=list(csv.DictReader(f))
        self.assertEqual({x["code"] for x in rows},{"0101210000"})
        self.assertTrue(all(x["payable_rate"]=="" for x in rows))
        self.assertTrue(all(x["current_source_refetched"]=="false" for x in rows))

    def test_oct_31_impact_retains_oct_15_missed_watch(self):
        r=self.audit(date(2026,10,31))
        self.assertEqual(r["candidate_effective_end_measures"],55)
        self.assertEqual(r["affected_distinct_codes"],2)
        self.assertEqual(r["ending_on_dates"]["2026-10-31"],47)

    def test_source_tamper_is_blocking(self):
        self.source.write_bytes(self.source.read_bytes()+b"x")
        with self.assertRaisesRegex(ValueError,"SHA tampered"):
            self.audit()

    def test_partial_or_published_source_rejected(self):
        self.proof["source_full_complete"]=False
        self.save()
        with self.assertRaisesRegex(ValueError,"incomplete"):
            self.audit()

    def test_source_and_output_must_not_alias(self):
        with self.assertRaisesRegex(ValueError,"overwritten"):
            collect(str(self.source),str(self.summary),
                    str(self.source),str(self.report),through=date(2026,10,15))


if __name__=="__main__":
    unittest.main()
