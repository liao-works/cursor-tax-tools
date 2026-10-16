"""XI source drift before resuming an old frozen catalogue: source-only."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_drift_guard import audit,semantic_fingerprint

DAY=date(2026,10,14)
CODES=("0101210000","0101290000","0101300000")


def node(code,n):
    return {"id":str(n),"type":"goods_nomenclature","attributes":{
        "goods_nomenclature_item_id":code,"goods_nomenclature_sid":n,
        "producline_suffix":"80","description":"fixture",
        "declarable":True,"validity_start_date":"2020-01-01",
        "validity_end_date":None}}


def body(code,rate="5%",*,condition="certificate",reverse=False):
    mid=str(int(code))
    measure={"type":"measure","id":mid,
             "attributes":{"resolved_duty_expression":rate,
                           "effective_start_date":"2020-01-01"},
             "relationships":{
                "measure_type":{"data":{"type":"measure_type","id":"103"}},
                "geographical_area":{"data":{"type":"geographical_area","id":"1011"}},
                "measure_conditions":{"data":[{"type":"measure_condition","id":condition}]}
             }}
    inc=[measure,{"type":"measure_condition","id":condition,
                  "attributes":{"requirement":"required"}}]
    return json.dumps({
        "data":{"id":code,"type":"commodity",
                "attributes":{"goods_nomenclature_item_id":code},
                "relationships":{"import_measures":{"data":[
                    {"type":"measure","id":mid}]}}},
        "included":list(reversed(inc)) if reverse else inc,
    },sort_keys=reverse,indent=2 if reverse else None).encode()


class XICrossBatchDriftTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root=Path(t.name)
        self.catalog=root/"partial-catalogue.db"
        self.previous=root/"previous.db"
        listing={"data":[{"id":"1"}]}
        chapters={"01":{"data":[node(code,i+1) for i,code in enumerate(CODES)]}}
        build_catalogue(listing,chapters,str(self.catalog),as_of=DAY,public=True)
        collect(str(self.catalog),str(self.previous),as_of=DAY,limit=3,
                fetcher=lambda code,day:body(code))

    def test_stable_source_ignores_json_key_order_and_included_order(self):
        calls=[]
        def reordered(code,day):
            calls.append(code)
            return body(code,reverse=True)
        result=audit(str(self.catalog),str(self.previous),as_of=DAY,
                     fetcher=reordered,public_canary=True,count=3)
        self.assertEqual(calls,list(CODES))
        self.assertEqual(result["anchor_count"],3)
        self.assertTrue(result["resume_source_stability_gate_pass"])
        self.assertEqual(result["changed_anchor_codes"],[])
        self.assertFalse(result["atomic_official_snapshot_verified"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])
        self.assertNotEqual(result["anchors"][0]["old_raw_sha256"],
                            result["anchors"][0]["new_raw_sha256"])
        self.assertEqual(result["anchors"][0]["old_semantic_sha256"],
                         result["anchors"][0]["new_semantic_sha256"])

    def test_duty_change_causes_drift_and_blocks_resume(self):
        result=audit(str(self.catalog),str(self.previous),as_of=DAY,count=3,
                     public_canary=True,
                     fetcher=lambda code,day:body(
                         code,rate="8%" if code==CODES[1] else "5%"))
        self.assertFalse(result["resume_source_stability_gate_pass"])
        self.assertEqual(result["changed_anchor_codes"],[CODES[1]])
        self.assertEqual(result["anchors"][1]["status"],"source_drift_detected")

    def test_raw_source_drift_evidence_is_atomically_preserved(self):
        import hashlib
        import zlib
        evidence=self.catalog.parent/"anchors-evidence.db"
        result=audit(str(self.catalog),str(self.previous),as_of=DAY,
                     public_canary=True,count=2,evidence_output=str(evidence),
                     fetcher=lambda code,day:body(
                         code,rate="12% + 10 EUR / 100 kg" if code==CODES[2] else "5%"))
        self.assertFalse(result["resume_source_stability_gate_pass"])
        self.assertEqual(result["changed_anchor_codes"],[CODES[2]])
        self.assertTrue(evidence.is_file())
        self.assertEqual(result["raw_anchor_evidence_sqlite_sha256"],
                         hashlib.sha256(evidence.read_bytes()).hexdigest())
        with sqlite3.connect(evidence) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            rows=db.execute(
                "SELECT code,outcome,old_compressed_body,new_compressed_body "
                "FROM anchors ORDER BY code").fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(zlib.decompress(rows[0][2]),body(CODES[0]))
            self.assertEqual(zlib.decompress(rows[1][2]),body(CODES[2]))
            self.assertEqual(zlib.decompress(rows[1][3]),
                             body(CODES[2],rate="12% + 10 EUR / 100 kg"))
            self.assertEqual(rows[1][1],"source_drift_detected")
        with self.assertRaisesRegex(ValueError,"must not overwrite"):
            audit(str(self.catalog),str(self.previous),as_of=DAY,
                  public_canary=True,count=1,evidence_output=str(self.previous),
                  fetcher=lambda code,day:body(code))

    def test_changed_linked_condition_also_causes_drift(self):
        result=audit(str(self.catalog),str(self.previous),as_of=DAY,
                     public_canary=True,count=2,
                     fetcher=lambda code,day:body(
                         code,condition="new certificate" if code==CODES[2]
                         else "certificate"))
        self.assertEqual(result["anchor_count"],2)
        self.assertEqual([x["code"] for x in result["anchors"]],
                         [CODES[0],CODES[2]])
        self.assertEqual(result["changed_anchor_codes"],[CODES[2]])

    def test_failed_new_anchor_fetch_is_not_stability_success(self):
        def unavailable(code,day):
            raise RuntimeError("429 blocked")
        result=audit(str(self.catalog),str(self.previous),as_of=DAY,
                     public_canary=True,count=1,fetcher=unavailable)
        self.assertFalse(result["resume_source_stability_gate_pass"])
        self.assertEqual(result["failed_anchor_codes"],[CODES[0]])
        self.assertIn("429",result["anchors"][0]["error"])

    def test_corrupted_prior_checkpoint_aborts_before_new_fetch(self):
        with sqlite3.connect(self.previous) as db:
            db.execute("UPDATE requests SET response_sha256=? WHERE code=?",
                       ("a"*64,CODES[0]))
        calls=[]
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            audit(str(self.catalog),str(self.previous),as_of=DAY,
                  public_canary=True,
                  fetcher=lambda code,day:calls.append(code))
        self.assertEqual(calls,[])

    def test_managed_mode_requires_a_frozen_manifest(self):
        with self.assertRaisesRegex(ValueError,"frozen source manifest"):
            audit(str(self.catalog),str(self.previous),as_of=DAY,
                  fetcher=lambda code,day:body(code))
        with self.assertRaisesRegex(ValueError,"Public XI canary cannot"):
            audit(str(self.catalog),str(self.previous),as_of=DAY,
                  manifest="fake",public_canary=True,
                  fetcher=lambda code,day:body(code))
        with self.assertRaisesRegex(ValueError,"anchor limits"):
            audit(str(self.catalog),str(self.previous),as_of=DAY,
                  public_canary=True,count=4,
                  fetcher=lambda code,day:body(code))

    def test_managed_97_chapter_manifest_allows_only_source_stability_not_atomic_cert(self):
        root=self.catalog.parent
        cat=root/"managed-full.db"
        manifest=root/"managed-manifest.json"
        checkpoint=root/"managed-measures.db"
        listing={"data":[{"id":str(x)} for x in range(1,98)]}
        chapters={f"{x:02d}":{"data":[node(f"{x:02d}01000000",x)]}
                  for x in range(1,98)}
        build_catalogue(listing,chapters,str(cat),as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)
        freeze(str(cat),str(manifest))
        collect(str(cat),str(checkpoint),as_of=DAY,limit=1,
                fetcher=lambda code,day:body(code))
        report=audit(str(cat),str(checkpoint),as_of=DAY,manifest=str(manifest),
                     count=2,fetcher=lambda code,day:body(code))
        self.assertEqual(report["source_scope"],"managed_full")
        self.assertEqual(report["previous_successful_codes"],1)
        self.assertEqual(report["anchor_count"],1)
        self.assertTrue(report["resume_source_stability_gate_pass"])
        self.assertFalse(report["atomic_official_snapshot_verified"])
        self.assertFalse(report["ni_other_verified"])

    def test_wrong_date_rejects_even_for_public_canary(self):
        with self.assertRaisesRegex(ValueError,"source as_of differs"):
            audit(str(self.catalog),str(self.previous),
                  as_of=date(2026,10,13),public_canary=True,
                  fetcher=lambda code,day:body(code))


if __name__=="__main__":
    unittest.main()
