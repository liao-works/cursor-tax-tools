"""Exact code impact is derived ONLY from full, SHA-accepted original GB/XI."""
import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_duty.full_source_delta_scope import create,file_sha

A="0101210000"
B="0101299000"
C="0207412010"


class FullSourceImpactTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.p=Path(folder.name)
        self.before=self.p/"before.db"
        self.after=self.p/"after.db"
        self.out=self.p/"impact.csv"
        self.report=self.p/"impact.json"

    def gb(self, path, rows, *, version, day="2026-10-14"):
        with sqlite3.connect(path) as db:
            db.executescript("""
            CREATE TABLE snapshots(region TEXT,source_kind TEXT,source_version TEXT,
              as_of TEXT,scope TEXT,production_eligible INTEGER);
            CREATE TABLE meta(key TEXT,value TEXT);
            CREATE TABLE requests(region TEXT,code TEXT,response_sha256 TEXT,
              status TEXT,row_count INTEGER,error TEXT);
            CREATE TABLE measures(region TEXT,code TEXT,raw_json TEXT);
            """)
            db.execute("INSERT INTO snapshots VALUES ('GB','DBT-expanded-measures',?,?,?,0)",
                       (version,day,"all-DBT-expanded-measures-not-all-import-taxes"))
            db.executemany("INSERT INTO requests VALUES ('GB',?,NULL,?,?,NULL)",[
                (code,"has_expanded_measures" if payloads else
                 "no_expanded_measure_rows_unverified",len(payloads))
                for code,payloads in rows.items()
            ])
            n=0
            for code,payloads in rows.items():
                for raw in payloads:
                    db.execute("INSERT INTO measures VALUES('GB',?,?)",(code,json.dumps(raw)))
                    n+=1
            db.execute("INSERT INTO meta VALUES('imported_measure_rows',?)",(str(n),))
        proof={
            "kind":"gb-full-expanded-measures-shadow","production_eligible":False,
            "all_import_duties_verified":False,"ni_other_verified":False,
            "complete_within_dbt_export_scope":True,"version":version,"as_of":day,
            "source_rows":n,
            "source_commodity_codes":sum(bool(x) for x in rows.values()),
            "active_leaves_without_any_export_rows":sum(not x for x in rows.values()),
            "official_active_leaves":len(rows),
            "database_sha256":file_sha(str(path))
        }
        path.with_suffix(".json").write_text(json.dumps(proof))

    def xi(self,path,items,*,day):
        with sqlite3.connect(path) as db:
            db.executescript("""
            CREATE TABLE config(key TEXT,value TEXT);
            CREATE TABLE requests(code TEXT,status TEXT,response_sha256 TEXT);
            """)
            db.executemany("INSERT INTO config VALUES(?,?)",[
               ("region","XI"),("as_of",day),
               ("catalogue_sha256","a"*64)])
            db.executemany("INSERT INTO requests VALUES(?,'ok',?)",
                           [(code,sha) for code,sha in items.items()])
        path.with_suffix(".json").write_text(json.dumps({
            "kind":"xi-merged-source-checkpoint-v1",
            "production_eligible":False,"source_complete":True,
            "as_of":day,"catalogue_candidate_codes":len(items),
            "successful_codes":len(items),
            "failed_codes":0,"remaining_codes":0,
            "sqlite_sha256":file_sha(str(path)),
        }))
        # Each XI artifact is in an isolated directory to avoid colliding
        # independent same-name acceptance receipts.
        (path.parent/"xi-public-full-acceptance.json").write_text(json.dumps({
            "kind":"xi-public-full-source-acceptance-review-v1",
            "as_of":day,"expected":len(items),
            "responses_replayed":len(items),"missing":0,"failed":0,
            "public_full_raw_source_coverage":True,
            "ni_other_verified":False,"production_eligible":False
        }))

    def test_full_gb_catches_unchanged_removed_added_and_non_103_rows(self):
        self.gb(self.before,{A:[{"sid":"1","type":"103"}],B:[{"sid":"2","type":"142"}]},
                version="v4.0.1627")
        self.gb(self.after,{A:[{"sid":"1","type":"103"}],B:[{"sid":"2","type":"142","conditions":"Y155"}],
                            C:[]},version="v4.0.1628",day="2026-10-15")
        r=create(str(self.before),str(self.after),"GB",str(self.out),str(self.report))
        self.assertEqual(r["affected_codes"],2)
        self.assertEqual(r["unchanged_codes"],1)
        self.assertEqual(r["change_kinds"]["new_official_commodity"],1)
        with self.out.open(newline="") as f:
            lines=list(csv.DictReader(f))
        self.assertEqual({x["code"] for x in lines},{B,C})
        self.assertTrue(all(not x["payable_rate"] and
                            x["production_eligible"]=="false" for x in lines))
        self.assertEqual(r["changed_code_csv_sha256"],file_sha(str(self.out)))
        self.assertFalse(r["ni_other_verified"])

    def test_gb_full_source_sha_tamper_detected(self):
        self.gb(self.before,{A:[{"sid":"1"}]},version="v4.0.1627")
        self.gb(self.after,{A:[{"sid":"1"}]},version="v4.0.1628")
        self.after.write_bytes(self.after.read_bytes()+b"garbage")
        with self.assertRaisesRegex(ValueError,"SHA mismatch"):
            create(str(self.before),str(self.after),"GB",str(self.out),str(self.report))

    def test_xi_exact_raw_shas_reveal_change_and_missing_codes(self):
        original_dir=self.p/"old"
        latest_dir=self.p/"new"
        original_dir.mkdir()
        latest_dir.mkdir()
        self.before=original_dir/"xi-public-full-merged.db"
        self.after=latest_dir/"xi-public-full-merged.db"
        self.xi(self.before,{A:"a"*64,B:"b"*64},day="2026-10-14")
        self.xi(self.after,{A:"a"*64,B:"c"*64,C:"d"*64},day="2026-10-15")
        r=create(str(self.before),str(self.after),"XI",str(self.out),str(self.report))
        self.assertEqual(r["affected_codes"],2)
        self.assertEqual(r["change_kinds"]["official_source_rows_changed"],1)
        self.assertEqual(r["change_kinds"]["new_official_commodity"],1)
        self.assertEqual(r["unchanged_codes"],1)

    def test_xi_partial_independent_approval_fails_closed(self):
        self.before=self.p/"old"/"xi-public-full-merged.db"
        self.after=self.p/"new"/"xi-public-full-merged.db"
        self.before.parent.mkdir()
        self.after.parent.mkdir()
        self.xi(self.before,{A:"a"*64},day="2026-10-14")
        self.xi(self.after,{A:"a"*64},day="2026-10-15")
        receipt=self.after.parent/"xi-public-full-acceptance.json"
        data=json.loads(receipt.read_text())
        data["missing"]=1
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"acceptance mismatch"):
            create(str(self.before),str(self.after),"XI",str(self.out),str(self.report))

    def test_older_asof_source_is_not_applied(self):
        self.gb(self.before,{A:[{"sid":"1"}]},version="v4.0.1627",day="2026-10-15")
        self.gb(self.after,{A:[{"sid":"1"}]},version="v4.0.1628",day="2026-10-14")
        with self.assertRaisesRegex(ValueError,"Backward"):
            create(str(self.before),str(self.after),"GB",str(self.out),str(self.report))

    def test_output_cannot_overwrite_source(self):
        self.gb(self.before,{A:[{"sid":"1"}]},version="v4.0.1627")
        self.gb(self.after,{A:[{"sid":"1"}]},version="v4.0.1628")
        with self.assertRaisesRegex(ValueError,"overwritten"):
            create(str(self.before),str(self.after),"GB",str(self.before),str(self.report))


if __name__=="__main__":
    unittest.main()
