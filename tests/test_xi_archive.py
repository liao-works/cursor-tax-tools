"""Portable signed XI checkpoint archives: restore, wrong key, tamper, zip-slip."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from src.target_duty.xi_archive import pack,restore
from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_measure_crawl import collect
from src.target_duty.xi_merge import combine
from src.target_duty.xi_checkpoint_chain import seal

DAY=date(2026,10,14)


def item(chapter):
    code=chapter+"01000000"
    return {"id":str(int(chapter)*100),"type":"goods_nomenclature",
            "attributes":{"goods_nomenclature_item_id":code,
                          "goods_nomenclature_sid":int(chapter)*100,
                          "description":"fixture","producline_suffix":"80",
                          "declarable":True,"validity_start_date":"2020-01-01",
                          "validity_end_date":None}}


def body(code):
    return json.dumps({"data":{"id":code,"type":"commodity",
                               "attributes":{"goods_nomenclature_item_id":code},
                               "relationships":{"import_measures":{"data":[]}}},
                       "included":[]}).encode()


@unittest.skipUnless(shutil.which("openssl"),"OpenSSL Ed25519 required for archive tests")
class SignedXIArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.cat=self.root/"catalogue.db"
        self.frozen=self.root/"frozen.json"
        self.first=self.root/"first-shard.db"
        self.parent=self.root/"parent.db"
        self.chain=self.root/"chain.json"
        self.archive=self.root/"signed-source.zip"
        self.priv=self.root/"operator-private.pem"
        self.pub=self.root/"operator-public.pem"
        self.wrong_priv=self.root/"other-private.pem"
        self.wrong_pub=self.root/"other-public.pem"
        self._openssl(["genpkey","-algorithm","Ed25519","-out",str(self.priv)])
        self._openssl(["pkey","-in",str(self.priv),
                       "-pubout","-out",str(self.pub)])
        self._openssl(["genpkey","-algorithm","Ed25519","-out",str(self.wrong_priv)])
        self._openssl(["pkey","-in",str(self.wrong_priv),
                       "-pubout","-out",str(self.wrong_pub)])
        chapters={f"{i:02d}":{"data":[item(f"{i:02d}")]}
                  for i in range(1,98)}
        build_catalogue({"data":[{"id":str(i)} for i in range(1,98)]},
                        chapters,str(self.cat),as_of=DAY,
                        full_managed_listing=True,official_listed_total=97)
        freeze(str(self.cat),str(self.frozen))
        collect(str(self.cat),str(self.first),as_of=DAY,
                limit=2,fetcher=lambda code,date:body(code))
        combine(str(self.cat),[str(self.first)],str(self.parent),as_of=DAY)
        seal(str(self.cat),str(self.parent),str(self.parent.with_suffix(".json")),
             manifest=str(self.frozen),output=str(self.chain))

    def _openssl(self,args):
        subprocess.run(["openssl",*args],check=True,capture_output=True)

    def _pack(self,archive=None,**kwargs):
        return pack(str(archive or self.archive),
                    catalogue=str(self.cat),checkpoint=str(self.parent),
                    merged_report=str(self.parent.with_suffix(".json")),
                    chain=str(self.chain),frozen_manifest=str(self.frozen),
                    signing_private_key=str(self.priv),**kwargs)

    def test_managed_signed_root_restores_full_verified_source_but_not_duty(self):
        p=self._pack()
        self.assertEqual(p["source_scope"],"managed_full")
        self.assertTrue(p["operator_signature_present"])
        restored=self.root/"recovered"
        audit=restore(str(self.archive),str(restored),
                      trusted_public_key=str(self.pub))
        self.assertTrue(audit["operator_signature_verified"])
        self.assertTrue(audit["source_replay_verified"])
        self.assertFalse(audit["production_eligible"])
        self.assertFalse(audit["ni_other_verified"])
        with sqlite3.connect(restored/"checkpoint.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],2)
        with self.assertRaisesRegex(ValueError,"overwrite existing"):
            restore(str(self.archive),str(restored),
                    trusted_public_key=str(self.pub))
        with self.assertRaisesRegex(ValueError,"replace existing"):
            self._pack()

    def test_missing_wrong_key_or_unsigned_managed_archive_rejected(self):
        self._pack()
        with self.assertRaisesRegex(ValueError,"trusted Ed25519"):
            restore(str(self.archive),str(self.root/"no-trust"))
        with self.assertRaisesRegex(ValueError,"signature operation failed"):
            restore(str(self.archive),str(self.root/"wrong"),
                    trusted_public_key=str(self.wrong_pub))
        self.assertFalse((self.root/"wrong").exists())
        with self.assertRaisesRegex(ValueError,"cannot impersonate"):
            restore(str(self.archive),str(self.root/"downgrade"),
                    public_canary=True)

    def test_changed_member_rejected_even_when_zip_crc_is_updated(self):
        self._pack()
        target=self.root/"edited.zip"
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(target,"w") as out:
            for name in original.namelist():
                value=original.read(name)
                if name=="checkpoint.json":
                    value+=b"\\n"
                out.writestr(name,value)
        with self.assertRaisesRegex(ValueError,"size mismatch|SHA-256 mismatch"):
            restore(str(target),str(self.root/"edited-result"),
                    trusted_public_key=str(self.pub))
        self.assertFalse((self.root/"edited-result").exists())

    def test_zip_slip_and_extra_member_rejected_even_if_signature_valid(self):
        self._pack()
        target=self.root/"injected.zip"
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(target,"w") as out:
            for name in original.namelist():
                out.writestr(name,original.read(name))
            out.writestr("../escape",b"no")
        with self.assertRaisesRegex(ValueError,"unsafe path"):
            restore(str(target),str(self.root/"injected-result"),
                    trusted_public_key=str(self.pub))
        self.assertFalse((self.root/"escape").exists())

    def test_signed_child_carries_exact_previous_checkpoint_provenance(self):
        second=self.root/"second.db"
        current=self.root/"current.db"
        child_chain=self.root/"child-chain.json"
        collect(str(self.cat),str(second),as_of=DAY,
                start_index=2,limit=1,fetcher=lambda code,day:body(code))
        combine(str(self.cat),[str(self.parent),str(second)],
                str(current),as_of=DAY)
        seal(str(self.cat),str(current),str(current.with_suffix(".json")),
             manifest=str(self.frozen),output=str(child_chain),
             previous_db=str(self.parent),
             previous_report=str(self.parent.with_suffix(".json")),
             previous_chain=str(self.chain))
        child=self.root/"child.zip"
        pack(str(child),catalogue=str(self.cat),checkpoint=str(current),
             merged_report=str(current.with_suffix(".json")),
             chain=str(child_chain),frozen_manifest=str(self.frozen),
             previous_db=str(self.parent),
             previous_report=str(self.parent.with_suffix(".json")),
             previous_chain=str(self.chain),signing_private_key=str(self.priv))
        restored=self.root/"child-restored"
        result=restore(str(child),str(restored),
                       trusted_public_key=str(self.pub))
        self.assertTrue(result["source_replay_verified"])
        self.assertTrue((restored/"parent-chain.json").is_file())
        with sqlite3.connect(restored/"checkpoint.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],3)

    def test_public_partial_archive_stays_unsigned_and_cannot_be_managed(self):
        cat=self.root/"public-catalogue.db"
        shard=self.root/"public-shard.db"
        merged=self.root/"public-merged.db"
        chain=self.root/"public-chain.json"
        build_catalogue({"data":[{"id":"1"}]},
                        {"01":{"data":[item("01")]}},
                        str(cat),as_of=DAY,public=True)
        collect(str(cat),str(shard),as_of=DAY,limit=1,
                fetcher=lambda code,day:body(code))
        combine(str(cat),[str(shard)],str(merged),as_of=DAY)
        seal(str(cat),str(merged),str(merged.with_suffix(".json")),
             output=str(chain),public_canary=True)
        target=self.root/"public-source.zip"
        report=pack(str(target),catalogue=str(cat),checkpoint=str(merged),
                    merged_report=str(merged.with_suffix(".json")),
                    chain=str(chain),public_canary=True)
        self.assertFalse(report["operator_signature_present"])
        audit=restore(str(target),str(self.root/"public-result"),public_canary=True)
        self.assertFalse(audit["operator_signature_verified"])
        self.assertEqual(audit["source_scope"],"partial_or_fixture")
        with self.assertRaisesRegex(ValueError,"must be signed"):
            restore(str(target),str(self.root/"incorrect-managed"),
                    trusted_public_key=str(self.pub))


if __name__=="__main__":
    unittest.main()
