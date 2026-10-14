"""NI checkpoint chain must replay source and reject erased/altered old successes."""
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_merge import combine
from src.target_duty.xi_checkpoint_chain import seal, verify
from src.target_duty.xi_frozen_catalogue import digest_file

DAY=date(2026,10,14)
CODES=["0101210000","0101290000","0101300000"]


def item(code,n):
    return {"id":str(n),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":n,
                          "description":"fixture","producline_suffix":"80",
                          "declarable":True,"validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


def payload(code):
    return json.dumps({
        "data":{"id":code,"type":"commodity",
                "attributes":{"goods_nomenclature_item_id":code},
                "relationships":{"import_measures":{"data":[]}}},
        "included":[]}).encode()


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.cat=root/"cat.db"
        self.shard1=root/"first.db"
        self.shard2=root/"second.db"
        self.parent=root/"parent.db"
        self.current=root/"current.db"
        self.chain0=root/"parent-chain.json"
        self.chain1=root/"current-chain.json"
        build_catalogue({"data":[{"id":"1"}]},
                        {"01":{"data":[item(c,i+1) for i,c in enumerate(CODES)]}},
                        str(self.cat),as_of=DAY,public=True)
        def first(c,d):
            if c==CODES[1]:
                raise RuntimeError("429 official source")
            return payload(c)
        collect(str(self.cat),str(self.shard1),as_of=DAY,limit=2,
                fetcher=first)
        combine(str(self.cat),[str(self.shard1)],str(self.parent),as_of=DAY)
        time.sleep(0.02)
        collect(str(self.cat),str(self.shard2),as_of=DAY,
                start_index=1,limit=2,fetcher=lambda c,d:payload(c))
        combine(str(self.cat),[str(self.parent),str(self.shard2)],
                str(self.current),as_of=DAY,resolve_failures=True)

    def _seal_first(self):
        return seal(str(self.cat),str(self.parent),
                    str(self.parent.with_suffix(".json")),
                    output=str(self.chain0),public_canary=True)

    def _continue(self):
        return seal(
            str(self.cat),str(self.current),
            str(self.current.with_suffix(".json")),
            output=str(self.chain1),public_canary=True,
            previous_db=str(self.parent),
            previous_report=str(self.parent.with_suffix(".json")),
            previous_chain=str(self.chain0))

    def test_first_and_next_receipts_link_verified_immutable_successes(self):
        initial=self._seal_first()
        after=self._continue()
        self.assertIsNone(initial["parent_chain_sha256"])
        self.assertEqual(initial["successful_codes"],1)
        self.assertEqual(initial["failed_codes"],1)
        self.assertEqual(after["parent_chain_sha256"],digest_file(self.chain0))
        self.assertEqual(after["parent_checkpoint_sha256"],digest_file(self.parent))
        self.assertEqual(after["continuity"],{
            "old_requests_retained":2,"old_successes_immutable":1,
            "old_failures_recovered":1,"new_requests":1})
        self.assertEqual(after["successful_codes"],3)
        self.assertEqual(after["failed_codes"],0)
        self.assertEqual(after["missing_codes"],0)
        self.assertFalse(after["production_eligible"])
        self.assertFalse(after["chain_authenticated_or_signed"])
        self.assertFalse(after["ni_other_verified"])
        self.assertEqual(
            verify(str(self.cat),str(self.current),
                   str(self.current.with_suffix(".json")),str(self.chain1),
                   public_canary=True,previous_db=str(self.parent),
                   previous_report=str(self.parent.with_suffix(".json")),
                   previous_chain=str(self.chain0)),
            after
        )
        with self.assertRaisesRegex(ValueError,"does not match"):
            verify(str(self.cat),str(self.current),
                   str(self.current.with_suffix(".json")),str(self.chain1),
                   public_canary=True)

    def test_old_success_removed_is_a_fatal_continuity_violation(self):
        self._seal_first()
        alternative=self.cat.parent/"lost-old-success.db"
        combine(str(self.cat),[str(self.shard2)],str(alternative),as_of=DAY)
        with self.assertRaisesRegex(ValueError,"lost previously captured requests"):
            seal(str(self.cat),str(alternative),
                 str(alternative.with_suffix(".json")),public_canary=True,
                 output=str(self.chain1),
                 previous_db=str(self.parent),
                 previous_report=str(self.parent.with_suffix(".json")),
                 previous_chain=str(self.chain0))
        self.assertFalse(self.chain1.exists())

    def test_prior_success_timestamp_altered_even_if_resigned_locally_is_rejected(self):
        self._seal_first()
        with sqlite3.connect(self.current) as db:
            db.execute("UPDATE requests SET checked_at='2026-10-14T23:00:00+00:00' "
                       "WHERE code=?",(CODES[0],))
        report_path=self.current.with_suffix(".json")
        report=json.loads(report_path.read_text())
        report["sqlite_sha256"]=digest_file(self.current)
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,"modified successful evidence"):
            self._continue()

    def test_old_parent_chain_tamper_or_missing_is_rejected(self):
        self._seal_first()
        proof=json.loads(self.chain0.read_text())
        proof["successful_codes"]=888
        self.chain0.write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError,"parent continuity receipt"):
            self._continue()
        with self.assertRaisesRegex(ValueError,"AND continuity receipt"):
            seal(str(self.cat),str(self.current),
                 str(self.current.with_suffix(".json")),
                 output=str(self.chain1),public_canary=True,
                 previous_db=str(self.parent))

    def test_changed_merged_report_byte_fingerprint_rejected(self):
        self._seal_first()
        report=self.current.with_suffix(".json")
        original=json.loads(report.read_text())
        original["sqlite_sha256"]="0"*64
        report.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError,"merge report source hash"):
            self._continue()

    def test_partial_source_never_passes_managed_mode(self):
        with self.assertRaisesRegex(ValueError,"frozen source manifest"):
            seal(str(self.cat),str(self.parent),
                 str(self.parent.with_suffix(".json")),
                 output=str(self.chain0))
        with self.assertRaisesRegex(ValueError,"cannot overwrite"):
            seal(str(self.cat),str(self.parent),
                 str(self.parent.with_suffix(".json")),
                 output=str(self.parent),public_canary=True)


if __name__=="__main__":
    unittest.main()
