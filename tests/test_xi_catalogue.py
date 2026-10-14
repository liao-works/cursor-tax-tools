"""HMRC XI chapter-based nomenclature: independent and fail-closed fixtures."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_catalogue import active, build, chapters, records, fetch

TODAY = date(2026, 10, 14)


def listing():
    return {"data":[{"id":"1"},{"id":"02"}]}


def item(chapter, code, rid, declarable=True, suffix="80", start="2020-01-01"):
    return {
        "id":rid, "type":"goods_nomenclature",
        "attributes":{
            "goods_nomenclature_item_id":code,
            "goods_nomenclature_sid":int(rid),
            "producline_suffix":suffix,
            "description":"Sample",
            "declarable":declarable,
            "validity_start_date":start,
            "validity_end_date":None,
        },
    }


def payloads():
    return {
        "01":{"data":[
            item("01","0101210000","1",True),
            item("01","0101000000","2",False),
        ]},
        "02":{"data":[item("02","0207412010","3",True)]},
    }


class XICatalogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/"xi-catalogue.db"

    def test_official_chapter_list_and_independent_codes(self):
        self.assertEqual(chapters(listing()),["01","02"])
        result=build(listing(),payloads(),str(self.output),as_of=TODAY)
        self.assertEqual(result["chapters_complete"],2)
        self.assertEqual(result["nomenclature_rows"],3)
        self.assertEqual(result["active_declarable_entries"],2)
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["other_semantics_verified"])
        self.assertFalse(result["atomic_version_proven"])
        self.assertEqual(result["measures_imported"],0)
        db=sqlite3.connect(self.output)
        try:
            self.assertEqual(db.execute(
                "SELECT code FROM nomenclature WHERE declarable=1 ORDER BY code"
            ).fetchall(),[("0101210000",),("0207412010",)])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally:db.close()

    def test_missing_chapter_blocks_partial_promotion(self):
        with self.assertRaisesRegex(ValueError,"coverage mismatch"):
            build(listing(),{"01":payloads()["01"]},str(self.output),as_of=TODAY)
        self.assertFalse(self.output.exists())

    def test_invalid_nomenclature_and_duplicate_nodes_rejected(self):
        with self.assertRaisesRegex(ValueError,"Off-chapter"):
            records("01",{"data":[item("01","0207412010","9")]},TODAY)
        with self.assertRaisesRegex(ValueError,"Invalid/duplicate"):
            records("01",{"data":[item("01","0101210000","9"),
                                  item("01","0101220000","9")]},TODAY)
        with self.assertRaisesRegex(ValueError,"declarability"):
            records("01",{"data":[item("01","0101210000","9","Yes")]},TODAY)

    def test_as_of_validity_and_unknown_flags(self):
        self.assertTrue(active({"validity_start_date":"2026-10-14T00:00:00Z",
                                "validity_end_date":"2026-10-14T22:00:00Z"},TODAY))
        self.assertFalse(active({"validity_start_date":"2026-10-15"},TODAY))
        self.assertFalse(active({"validity_start_date":None},TODAY))
        doc={"data":[item("01","0101210000","11",None,suffix="80")]}
        self.assertIsNone(records("01",doc,TODAY)[0]["declarable"])

    def test_reject_missing_chapter_list(self):
        with self.assertRaisesRegex(ValueError,"missing or empty"):
            chapters({"data":[]})
        with self.assertRaisesRegex(ValueError,"duplicate"):
            chapters({"data":[{"id":"1"},{"id":"01"}]})
        with self.assertRaisesRegex(ValueError,"Unsupported"):
            fetch("/commodities/0101210000",token="anything")


if __name__=="__main__":
    unittest.main()
