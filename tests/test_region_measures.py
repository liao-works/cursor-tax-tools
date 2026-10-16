"""Contract tests for independent GB and XI evidence, without network or production writes."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_duty.region_measures import build, lookup

DAY = "2026-10-14"
DIGEST = "a" * 64


def gb_fixture(path, *, bad_count=False, source_error=False):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE audit_meta(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE source_requests(
        code TEXT PRIMARY KEY,response_sha256 TEXT,row_count INTEGER,status TEXT,error TEXT);
      CREATE TABLE measures (
        code TEXT,ordinal INTEGER,measure_sid TEXT,measure_type TEXT,
        geographical_area TEXT,expression TEXT,additional_code TEXT,conditions TEXT,
        excluded_areas TEXT,starts TEXT,ends TEXT,effective_state TEXT,raw_json TEXT);
    """)
    db.executemany("INSERT INTO audit_meta VALUES (?,?)", [
        ("version", "v4.0.1626"), ("as_of", DAY)])
    db.execute("INSERT INTO source_requests VALUES (?,?,?,?,?)", (
        "0207412010", DIGEST, 2 if bad_count else 1,
        "source_error" if source_error else "active_103_other_geography",
        "HTTP 503" if source_error else None))
    db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "0207412010", 0, "123", "103", "1080", "0%", "#NA",
        "certificate A", "#NA", "2026-01-01", "#NA", "active",
        json.dumps({
            "commodity__code": "0207412010",
            "measure__sid": "123",
            "measure__geographical_area__id": "1080",
            "measure__duty_expression": "0%",
        })))
    db.execute("INSERT INTO source_requests VALUES (?,?,?,?,?)", (
        "9930240000", DIGEST, 0, "no_measures_in_official_export", None))
    db.commit()
    db.close()


def xi_fixture(path, *, with_hash=True):
    doc = {
        "kind": "xi-third-country-duty-review-only",
        "production_eligible": False,
        "as_of": DAY, "coverage": 1,
        "entries": [{
            "code": "0207412010",
            "source": "HMRC-XI-v2",
            "response_sha256": DIGEST if with_hash else None,
            "effective_date_checked": DAY,
            "verdict": "candidate_review",
            "candidate_measures": [{
                "measure_id": "567", "measure_type": "103",
                "geographical_area_id": "1011",
                "duty_expression": "10% + 77 GBP / 100 kg",
                "has_conditions": True, "additional_code_present": True,
                "excluded_countries_present": False,
                "effective_start_date": "2026-01-01",
                "effective_end_date": None,
                "resolved_measure_components": [{"expression": "10%"}],
            }],
        }]
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


class RegionMeasureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        d = Path(self.temp.name)
        self.gb = d / "gb-evidence.db"
        self.xi = d / "xi-audit.json"
        self.target = d / "canonical.db"
        gb_fixture(self.gb)
        xi_fixture(self.xi)

    def test_keep_regions_and_original_duty_separate(self):
        report = build(str(self.target), gb=str(self.gb), xi=str(self.xi))
        self.assertFalse(report["production_eligible"])
        self.assertFalse(report["gb_complete"])
        self.assertFalse(report["xi_other_verified"])
        self.assertEqual([region["region"] for region in report["regions"]], ["GB", "XI"])
        a = lookup(str(self.target), "GB", "0207412010")
        b = lookup(str(self.target), "XI", "0207412010")
        self.assertEqual(a["measures"][0]["geography"], "1080")
        self.assertEqual(a["measures"][0]["duty_expression"], "0%")
        self.assertEqual(b["measures"][0]["geography"], "1011")
        self.assertEqual(b["measures"][0]["duty_expression"], "10% + 77 GBP / 100 kg")
        self.assertEqual(b["measures"][0]["raw"]["resolved_measure_components"],
                         [{"expression": "10%"}])
        self.assertNotEqual(a["measures"][0]["sid"], b["measures"][0]["sid"])
        self.assertEqual(a["scope"], "gap-only-366-candidates")
        self.assertEqual(b["scope"], "sample-not-other-certified")
        conn = sqlite3.connect(self.target)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM snapshots WHERE production_eligible=1"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            conn.close()

    def test_empty_source_request_is_not_zero_duty(self):
        build(str(self.target), gb=str(self.gb))
        result = lookup(str(self.target), "GB", "9930240000")
        self.assertTrue(result["found"])
        self.assertEqual(result["status"], "no_measures_in_official_export")
        self.assertEqual(result["measures"], [])
        self.assertEqual(result["row_count"], 0)
        self.assertFalse(result["production_eligible"])

    def test_uningested_region_and_code_remain_out_of_scope(self):
        build(str(self.target), gb=str(self.gb))
        self.assertEqual(lookup(str(self.target), "XI", "0207412010")["reason"],
                         "region_not_ingested")
        self.assertEqual(lookup(str(self.target), "GB", "0101210000")["reason"],
                         "outside_evidence_scope")

    def test_invalid_xi_evidence_never_poison_existing_output(self):
        build(str(self.target), gb=str(self.gb))
        before = self.target.read_bytes()
        xi_fixture(self.xi, with_hash=False)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            build(str(self.target), gb=str(self.gb), xi=str(self.xi))
        self.assertEqual(self.target.read_bytes(), before)

    def test_bad_gb_count_or_error_rejected(self):
        for flag in ("bad_count", "source_error"):
            with self.subTest(flag=flag):
                if self.gb.exists():
                    self.gb.unlink()
                gb_fixture(self.gb, **{flag: True})
                with self.assertRaises(ValueError):
                    build(str(self.target), gb=str(self.gb))
                self.assertFalse(self.target.exists())

    def test_region_is_required_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            build(str(self.target))
        build(str(self.target), xi=str(self.xi))
        with self.assertRaisesRegex(ValueError, "Explicit"):
            lookup(str(self.target), "NI", "0207412010")
        with self.assertRaisesRegex(ValueError, "Explicit"):
            lookup(str(self.target), "XI", "a123")

    def test_xi_must_be_raw_sample_not_review_gate(self):
        self.xi.write_text(json.dumps({
            "kind": "xi-other-evidence-gate-v1",
            "as_of": DAY,
            "production_eligible": False,
        }))
        with self.assertRaisesRegex(ValueError, "original XI"):
            build(str(self.target), xi=str(self.xi))


if __name__ == "__main__":
    unittest.main()
