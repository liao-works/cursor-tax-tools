"""Resumable XI independent-source measure evidence without tax-rate guessing."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_measure_crawl import collect, parse, candidates

DAY=date(2026,10,14)


def fixture_code(code,rid):
    return {
        "id":str(rid),"type":"goods_nomenclature",
        "attributes":{"goods_nomenclature_item_id":code,
                      "goods_nomenclature_sid":rid,
                      "producline_suffix":"80","description":"sample",
                      "declarable":True,"validity_start_date":"2020-01-01",
                      "validity_end_date":None}
    }


def measure_payload(code, *, expressed="5% + 10 EUR / 100 kg",
                    geo="1011", with_measure=True):
    measures=[{"type":"measure","id":"123"}] if with_measure else []
    inc=[{"type":"measure","id":"123",
          "attributes":{"resolved_duty_expression":expressed,
                        "effective_start_date":"2020-01-01"},
          "relationships":{
              "measure_type":{"data":{"type":"measure_type","id":"103"}},
              "geographical_area":{"data":{"type":"geographical_area","id":geo}},
              "measure_conditions":{"data":[{"type":"measure_condition","id":"c1"}]}
          }}] if with_measure else []
    return json.dumps({
        "data":{"type":"commodity","id":code,
                "attributes":{"goods_nomenclature_item_id":code},
                "relationships":{"import_measures":{"data":measures}}},
        "included":inc
    }).encode()


class XICollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.cat=self.base/"xi-catalogue.db"
        self.out=self.base/"xi-measures.db"
        listing={"data":[{"id":"1"}]}
        chapter={"01":{"data":[fixture_code("0101210000",1),
                                 fixture_code("0101290000",2)]}}
        build_catalogue(listing,chapter,str(self.cat),as_of=DAY)

    def test_resume_preserves_compound_and_original_conditions(self):
        count=[]
        def source(code,date_checked):
            count.append(code)
            return measure_payload(code,with_measure=code=="0101210000")
        a=collect(str(self.cat),str(self.out),as_of=DAY,limit=1,fetcher=source)
        self.assertEqual(a["successful_codes"],1)
        self.assertEqual(a["remaining_codes"],1)
        self.assertFalse(a["all_codes_fetched"])
        b=collect(str(self.cat),str(self.out),as_of=DAY,limit=2,fetcher=source)
        self.assertEqual(b["successful_codes"],2)
        self.assertEqual(b["remaining_codes"],0)
        self.assertTrue(b["all_codes_fetched"])
        self.assertEqual(b["measure_rows"],1)
        self.assertEqual(count,["0101210000","0101290000"])
        db=sqlite3.connect(self.out)
        try:
            self.assertEqual(db.execute(
                "SELECT duty_expression FROM measures"
            ).fetchone()[0],"5% + 10 EUR / 100 kg")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM requests").fetchone()[0],2)
            self.assertEqual(db.execute(
                "SELECT status FROM requests WHERE code='0101290000'"
            ).fetchone()[0],"ok")
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally:db.close()
        self.assertFalse(json.loads(Path(str(self.out)+".metadata.json").read_text())
                         ["other_semantics_verified"])

    def test_errors_explicit_and_retryable(self):
        def broken(code,day):
            if code=="0101210000":
                raise RuntimeError("HTTP 429")
            return measure_payload(code)
        a=collect(str(self.cat),str(self.out),as_of=DAY,limit=2,fetcher=broken)
        self.assertEqual(a["failed_codes"],1)
        self.assertEqual(a["successful_codes"],1)
        b=collect(str(self.cat),str(self.out),as_of=DAY,limit=2,
                  fetcher=lambda c,d:measure_payload(c))
        self.assertEqual(b["failed_codes"],0)
        self.assertEqual(b["successful_codes"],2)

    def test_source_catalogue_change_rejected_on_existing_checkpoint(self):
        collect(str(self.cat),str(self.out),as_of=DAY,limit=1,
                fetcher=lambda c,d:measure_payload(c))
        with self.assertRaisesRegex(ValueError,"checkpoint incompatible"):
            collect(str(self.cat),str(self.out),as_of=date(2026,10,13),limit=1,
                    fetcher=lambda c,d:measure_payload(c))

    def test_unresolved_measures_rejected_and_not_zero_rate(self):
        broken=measure_payload("0101210000")
        doc=json.loads(broken)
        doc["included"]=[]
        with self.assertRaisesRegex(ValueError,"Unresolved"):
            parse(json.dumps(doc).encode(),"0101210000")
        blank=measure_payload("0101290000",with_measure=False)
        self.assertEqual(parse(blank,"0101290000"),[])
        with self.assertRaisesRegex(ValueError,"another code"):
            parse(blank,"0101210000")

    def test_fixed_shard_skips_prior_successes_without_refetch(self):
        prior=self.base/"prior.db"
        collect(str(self.cat),str(prior),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d:measure_payload(c))
        seen=[]
        def next_source(code,day):
            seen.append(code)
            return measure_payload(code)
        report=collect(str(self.cat),str(self.out),as_of=DAY,
                       start_index=0,limit=2,skip_successes_from=str(prior),
                       fetcher=next_source)
        self.assertEqual(seen,["0101290000"])
        self.assertEqual(report["attempted_this_run"],1)
        self.assertEqual(report["previous_checkpoint_successes_available"],1)
        self.assertEqual(report["successful_codes"],1)
        self.assertEqual(report["cumulative_known_successes"],2)
        self.assertEqual(report["remaining_codes"],0)
        with self.assertRaisesRegex(ValueError,"provenance mismatch"):
            collect(str(self.cat),str(self.base/"wrong-day.db"),
                    as_of=date(2026,10,13),limit=2,
                    skip_successes_from=str(prior),fetcher=next_source)
        with self.assertRaisesRegex(ValueError,"cannot be overwritten"):
            collect(str(self.cat),str(prior),as_of=DAY,limit=1,
                    skip_successes_from=str(prior),fetcher=next_source)

    def test_local_checkpoint_cannot_skip_corrupted_success_on_rerun(self):
        collect(str(self.cat),str(self.out),as_of=DAY,limit=1,
                fetcher=lambda c,d:measure_payload(c))
        with sqlite3.connect(self.out) as db:
            db.execute("UPDATE measures SET duty_expression='tampered 0%' "
                       "WHERE code='0101210000'")
        fetched=[]
        with self.assertRaisesRegex(ValueError,"discrepancy"):
            collect(str(self.cat),str(self.out),as_of=DAY,limit=2,
                    fetcher=lambda c,d:fetched.append(c))
        self.assertEqual(fetched,[])
        with sqlite3.connect(self.out) as db:
            self.assertEqual(db.execute(
                "SELECT duty_expression FROM measures WHERE code='0101210000'"
            ).fetchone()[0],"tampered 0%")

    def test_independent_collector_rejects_tampered_prior_ok_without_fetching(self):
        prior=self.base/"tampered-prior.db"
        collect(str(self.cat),str(prior),as_of=DAY,limit=1,start_index=0,
                fetcher=lambda c,d:measure_payload(c))
        with sqlite3.connect(prior) as db:
            db.execute("UPDATE requests SET response_sha256=? WHERE status='ok'",
                       ("0"*64,))
        calls=[]
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            collect(str(self.cat),str(self.out),as_of=DAY,
                    start_index=0,limit=2,skip_successes_from=str(prior),
                    fetcher=lambda c,d:calls.append(c))
        self.assertEqual(calls,[])
        self.assertFalse(self.out.exists())

    def test_incomplete_official_measures_pagination_is_rejected(self):
        raw=json.loads(measure_payload("0101210000"))
        raw["links"]={"next":"https://example.invalid/commodity?page=2"}
        with self.assertRaisesRegex(ValueError,"paginated"):
            parse(json.dumps(raw).encode(),"0101210000")
        del raw["links"]
        raw["data"]["relationships"]["import_measures"]["meta"]={"total_count":2}
        with self.assertRaisesRegex(ValueError,"advertised count"):
            parse(json.dumps(raw).encode(),"0101210000")
        raw["data"]["relationships"]["import_measures"]["meta"]={"total_count":1}
        self.assertEqual(len(parse(json.dumps(raw).encode(),"0101210000")),1)

    def test_bounded_batch_and_independent_catalogue(self):
        self.assertEqual(len(candidates(str(self.cat))),2)
        with self.assertRaisesRegex(ValueError,"bounds"):
            collect(str(self.cat),str(self.out),as_of=DAY,limit=1001,
                    fetcher=lambda c,d:measure_payload(c))


if __name__=="__main__":
    unittest.main()
