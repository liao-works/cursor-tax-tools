"""Weekly accepted XI source produces only a pinned daily review baseline."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_duty.xi_daily_baseline import CANARIES,build

class XiDailyBaselineTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder=Path(temp.name)
        self.db=folder/"merged.db"
        self.manifest=folder/"manifest.json"
        self.acceptance=folder/"acceptance.json"
        self.out=folder/"daily-baseline.json"
        self.m={
           "kind":"xi-public-full-catalogue-frozen-review-v1",
           "as_of":"2026-10-14",
           "catalogue_sha256":"c"*64,
           "active_declarable_codes":6,
        }
        self.a={
           "kind":"xi-public-full-source-acceptance-review-v1",
           "as_of":"2026-10-14","expected":6,"responses_replayed":6,
           "missing":0,"failed":0,"public_full_raw_source_coverage":True,
           "ni_other_verified":False,"production_eligible":False
        }
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
             CREATE TABLE config(key TEXT PRIMARY KEY,value TEXT);
             CREATE TABLE requests(code TEXT PRIMARY KEY,response_sha256 TEXT,status TEXT);
             CREATE TABLE measures(measure_type TEXT,effective_start TEXT,effective_end TEXT);
            """)
            conn.executemany("INSERT INTO config VALUES(?,?)",[
               ("region","XI"),("as_of","2026-10-14"),("catalogue_sha256","c"*64),
               ("production_eligible","false")
            ])
            conn.executemany("INSERT INTO requests VALUES(?,?,?)",[
                (code,format(i,"064x"),"ok") for i,code in enumerate(CANARIES,1)
            ])
            conn.executemany("INSERT INTO measures VALUES(?,?,?)",[
                ("103","2018-01-01","2026-10-15"),
                ("105","2024-01-01","2026-10-31"),
                ("142","2022-01-01","2026-11-01"),
                ("103","2026-10-15",""),
            ])
        self.write_proofs()

    def write_proofs(self):
        self.manifest.write_text(json.dumps(self.m))
        self.acceptance.write_text(json.dumps(self.a))

    def audit(self):
        return build(str(self.db),str(self.manifest),str(self.acceptance),
                     run_id=12345,output=str(self.out),min_codes=6)

    def test_accepted_all_original_codes_generate_six_sha_and_known_expiries(self):
        result=self.audit()
        self.assertEqual(len(result["canary_response_sha256"]),6)
        self.assertEqual(result["known_103_105_end_dates"],{
            "2026-10-15":1,"2026-10-31":1
        })
        self.assertEqual(result["third_country_source_candidates"],2)
        self.assertEqual(result["original_codes_verified"],6)
        self.assertEqual(len(result["source_merged_db_sha256"]),64)
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])
        self.assertEqual(json.loads(self.out.read_text()),result)

    def test_missing_source_code_fails(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM requests WHERE code=?",(CANARIES[0],))
        with self.assertRaisesRegex(ValueError,"request count differs"):
            self.audit()

    def test_unaccepted_or_partial_shard_fails(self):
        self.a["missing"]=1
        self.write_proofs()
        with self.assertRaisesRegex(ValueError,"unaccepted"):
            self.audit()

    def test_catalogue_provenance_mismatch_fails(self):
        self.m["catalogue_sha256"]="f"*64
        self.write_proofs()
        with self.assertRaisesRegex(ValueError,"provenance"):
            self.audit()

    def test_source_evidence_not_overwritten(self):
        with self.assertRaisesRegex(ValueError,"overwrite"):
            build(str(self.db),str(self.manifest),str(self.acceptance),
                  run_id=12345,output=str(self.db),min_codes=6)


if __name__=="__main__":
    unittest.main()
