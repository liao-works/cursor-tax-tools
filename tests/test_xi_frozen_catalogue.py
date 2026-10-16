"""Pin the exact managed XI SQLite catalogue across independently resumed runs."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_frozen_catalogue import freeze, verify, shard_plan

DAY=date(2026,10,14)


def fixture_record(chapter):
    code=chapter+"01000000"
    return {"id":str(int(chapter)*100),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":int(chapter)*100,
                          "producline_suffix":"80","description":"fixture",
                          "declarable":True,"validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


class FrozenXICatalogueTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.path=self.root/"managed.db"
        self.manifest=self.root/"catalogue-pin.json"
        list_source={"data":[{"id":str(i)} for i in range(1,98)]}
        sections={f"{i:02d}":{"data":[fixture_record(f"{i:02d}")]}
                  for i in range(1,98)}
        build_catalogue(list_source,sections,str(self.path),as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)

    def test_freeze_verify_and_last_shard_clamped(self):
        pinned=freeze(str(self.path),str(self.manifest))
        self.assertEqual(pinned["official_chapters"],97)
        self.assertEqual(pinned["active_declarable_codes"],97)
        self.assertFalse(pinned["production_eligible"])
        self.assertEqual(verify(str(self.path),str(self.manifest),
                                day=DAY.isoformat())["catalogue_sha256"],
                         pinned["catalogue_sha256"])
        first=shard_plan(str(self.path),str(self.manifest),size=50,start=0)
        last=shard_plan(str(self.path),str(self.manifest),size=50,start=50)
        self.assertEqual((first["start_index"],first["end_index_exclusive"]),(0,50))
        self.assertEqual((last["start_index"],last["end_index_exclusive"]),(50,97))
        self.assertEqual(last["first_code"],"5101000000")
        self.assertEqual(last["last_code"],"9701000000")
        self.assertEqual(last["remaining_after_shard"],0)

    def test_a_freshly_generated_db_cannot_silently_replace_pin(self):
        freeze(str(self.path),str(self.manifest))
        # Ordinary catalogue metadata includes fetch timestamps. Any re-export
        # or local DB write changes bytes even if all commodity codes agree.
        db=sqlite3.connect(self.path)
        db.execute("UPDATE chapters SET loaded_at='different' WHERE chapter='01'")
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            verify(str(self.path),str(self.manifest))

    def test_manifest_tamper_or_day_mismatch_rejected(self):
        freeze(str(self.path),str(self.manifest))
        with self.assertRaisesRegex(ValueError,"as-of"):
            verify(str(self.path),str(self.manifest),day="2026-10-13")
        bad=json.loads(self.manifest.read_text())
        bad["active_declarable_codes"]=96
        self.manifest.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,"differs"):
            verify(str(self.path),str(self.manifest))

    def test_public_or_partial_catalogue_not_acceptable_as_managed_full(self):
        freeze(str(self.path),str(self.manifest))
        db=sqlite3.connect(self.path)
        db.execute("UPDATE metadata SET value='partial_or_fixture' WHERE key='listing_scope'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"not a managed full"):
            freeze(str(self.path),str(self.root/"should-not-write.json"))
        self.assertFalse((self.root/"should-not-write.json").exists())

    def test_invalid_shard_bounds_or_malformed_catalogue_rejected(self):
        freeze(str(self.path),str(self.manifest))
        for start,size in [(-1,20),(97,1),(0,0),(0,1001)]:
            with self.subTest(start=start,size=size):
                with self.assertRaisesRegex(ValueError,"bounds"):
                    shard_plan(str(self.path),str(self.manifest),start=start,size=size)
        db=sqlite3.connect(self.path)
        db.execute("UPDATE nomenclature SET code='01ABC00000' WHERE chapter='01'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"code"):
            freeze(str(self.path),str(self.root/"corrupt-manifest.json"))


if __name__=="__main__":
    unittest.main()
