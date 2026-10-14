"""GB compact version transition audit: changes, tamper resistance, status safety."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.gb_compact_review import SCHEMA,file_sha
from src.target_duty.gb_review_diff import compare

ACTIVE="candidate_present_requires_applicability_review"
MISSING="no_expanded_measure_rows_unverified"
CONTEXT="non_gb_1080_only_or_other_measures_unresolved"
OLD="v4.0.1626"
NEW="v4.0.1627"
ASOF="2026-10-14"


def row(rate="5%", *, condition="certificate", status="active",
        geo="1011", sid="11"):
    return {
        "sid":sid,"rate":rate,"condition":condition,
        "effective_status":status,"geo":geo,
    }


def make_snapshot(path:Path, version:str, items:dict,
                  *, day:str=ASOF) -> None:
    db=sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        all_target=0
        for key,value in {
            "region":"GB",
            "source_kind":"pinned-DBT-compact-103-review",
            "source_version":version,
            "as_of":day,
            "full_source_sha256":"1234",
            "complete_import_source_rows":"100",
            "official_active_leaves":str(len(items)),
            "production_eligible":"false",
            "rate_applicability_verified":"false",
            "ni_other_verified":"false",
        }.items():
            db.execute("INSERT INTO provenance VALUES (?,?)",(key,value))
        for code,(status,measures) in sorted(items.items()):
            active=sum(m["geo"]=="1011" and m["effective_status"]=="active" for m in measures)
            other=sum(m["geo"]=="1080" for m in measures)
            all_target+=int(active>0)
            db.execute("INSERT INTO commodities VALUES (?,?,?,?,?)",
                       (code,status,len(measures),active,other))
            for ordinal,m in enumerate(measures):
                geo=m["geo"]
                role=("gb_103_1011_candidate" if geo=="1011"
                      else "non_gb_103_1080_context")
                db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (code,ordinal,role,m["sid"],"103",geo,
                            m["effective_status"],m["rate"],None,
                            m["condition"],None,"2020-01-01",None,
                            json.dumps({"commodity__code":code})))
        db.commit()
    finally:
        db.close()
    path.with_suffix(".json").write_text(json.dumps({
        "kind":"gb-compact-103-1011-shadow-v1",
        "production_eligible":False,
        "rate_applicability_verified":False,
        "ni_other_verified":False,
        "version":version,
        "as_of":day,
        "full_source_sha256":"1234",
        "full_source_rows":100,
        "official_active_leaf_codes":len(items),
        "active_target_codes":all_target,
        "review_database_sha256":file_sha(str(path)),
    }))


class ReviewDiffTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.old=self.root/"previous.db"
        self.new=self.root/"current.db"
        make_snapshot(self.old,OLD,{
            "0101210000":(ACTIVE,[row()]),
            "0207412010":(ACTIVE,[row(rate="9%",sid="22")]),
            "9930240000":(MISSING,[]),
            "9930270000":(CONTEXT,[row(rate="0%",geo="1080",sid="24")]),
        })
        make_snapshot(self.new,NEW,{
            "0101210000":(ACTIVE,[row(rate="8% + 15 GBP / 100 kg",
                                     condition="new certificate")]),
            "0207412010":(CONTEXT,[row(rate="0%",geo="1080",sid="25")]),
            "0401100000":(ACTIVE,[row(rate="0%",sid="26")]),
            "9930240000":(MISSING,[]),
            "9930270000":(CONTEXT,[row(rate="0%",geo="1080",sid="24")]),
        })

    def test_detect_rate_and_condition_change_loss_and_addition(self):
        result=compare(str(self.old),str(self.new))
        self.assertTrue(result["high_risk_change_present"])
        self.assertTrue(result["manual_business_review_required"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])
        self.assertEqual(result["counts"]["added_commodities"],1)
        self.assertEqual(result["counts"]["removed_commodities"],0)
        self.assertEqual(result["counts"]["lost_active_103_1011"],1)
        self.assertEqual(result["counts"]["changed_active_103_1011"],1)
        self.assertEqual(result["counts"]["changed_commodity_status"],1)
        self.assertEqual(result["samples"]["lost_active_103_1011"],["0207412010"])
        self.assertEqual(result["samples"]["changed_active_103_1011"],["0101210000"])
        self.assertEqual(result["new_active_target_codes"],2)
        self.assertEqual(result["readiness"],"high_risk_change_requires_review")

    def test_same_source_is_self_test_only_not_new_published_version(self):
        with self.assertRaisesRegex(ValueError,"distinct versions"):
            compare(str(self.old),str(self.old))
        result=compare(str(self.old),str(self.old),allow_self_check=True)
        self.assertTrue(result["self_check_only"])
        self.assertEqual(sum(result["counts"].values()),0)
        self.assertFalse(result["high_risk_change_present"])
        self.assertTrue(result["manual_business_review_required"])
        self.assertFalse(result["production_eligible"])

    def test_old_database_tamper_rejected_before_comparison(self):
        db=sqlite3.connect(self.old)
        db.execute("UPDATE evidence SET expression='0%'")
        db.commit();db.close()
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            compare(str(self.old),str(self.new))

    def test_broken_1080_geography_rejected_not_gb(self):
        db=sqlite3.connect(self.new)
        db.execute("UPDATE evidence SET geography='1011' WHERE geography='1080'")
        db.commit();db.close()
        manifest=self.new.with_suffix(".json")
        report=json.loads(manifest.read_text())
        report["review_database_sha256"]=file_sha(str(self.new))
        manifest.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,"geography/type"):
            compare(str(self.old),str(self.new))

    def test_snapshot_production_flag_tampering_rejected(self):
        report=json.loads(self.new.with_suffix(".json").read_text())
        report["production_eligible"]=True
        self.new.with_suffix(".json").write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,"production-enabled"):
            compare(str(self.old),str(self.new))

    def test_rollback_and_invalid_sample_bounds_rejected(self):
        with self.assertRaisesRegex(ValueError,"go backwards"):
            compare(str(self.new),str(self.old))
        with self.assertRaisesRegex(ValueError,"sample limit"):
            compare(str(self.old),str(self.new),sample_limit=0)

    def test_explicit_unverified_gaps_are_not_0_percent(self):
        report=compare(str(self.old),str(self.new))
        self.assertNotIn("9930240000",report["samples"]["lost_active_103_1011"])
        self.assertFalse(report["payable_rate_verified"])
        self.assertTrue(report["any_source_change"])


if __name__=="__main__":
    unittest.main()
