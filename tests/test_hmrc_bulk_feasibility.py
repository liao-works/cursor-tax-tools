"""Offline validation of HMRC Bulk inventory continuity; no real API schema assumed."""
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.hmrc_bulk_feasibility import load_inventory, plan, verified_local_file


class HMRCBulkFeasibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / "inventory.json"
        self.data = self.root / "downloaded"
        self.data.mkdir()

    def entry(self, kind, period, *, raw=b"fixture", ref=None):
        ref = ref or (kind.lower() + "-" + period + ".xml")
        (self.data / ref).write_bytes(raw)
        return {"information_type": kind, "period": period,
                "reference": ref, "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw)}

    def load(self, rows, **overrides):
        payload = {
            "kind": "hmrc-tariff-files-operator-normalized-v1",
            "source_semantics_verified": False,
            "files": rows,
        }
        payload.update(overrides)
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")
        return load_inventory(str(self.inventory))

    def test_annual_monthly_daily_complete_metadata_and_local_bytes(self):
        rows = [
            self.entry("TARIFF-ANNUAL", "2026-01-01"),
            self.entry("TARIFF-MONTHLY", "2026-01-01"),
            self.entry("TARIFF-MONTHLY", "2026-02-01"),
            self.entry("TARIFF-DAILY", "2026-03-01"),
            self.entry("TARIFF-DAILY", "2026-03-02"),
            self.entry("TARIFF-DAILY", "2026-03-03"),
        ]
        inventory = self.load(rows)
        result = plan(inventory, as_of=date(2026, 3, 3), local_dir=str(self.data))
        self.assertEqual(result["required_files"], 6)
        self.assertTrue(result["verified_bytes_complete"])
        self.assertTrue(result["metadata_chain_complete"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["source_semantics_verified"])
        self.assertFalse(result["region_scope_verified"])
        self.assertFalse(result["delta_application_implemented"])
        self.assertEqual([r["period"] for r in result["ordered_files"]],
                         ["2026-01-01","2026-01-01","2026-02-01",
                          "2026-03-01","2026-03-02","2026-03-03"])

    def test_missing_month_or_day_blocks_continuity(self):
        inv = self.load([
            self.entry("TARIFF-ANNUAL", "2026-01-01"),
            self.entry("TARIFF-MONTHLY", "2026-01-01"),
            self.entry("TARIFF-DAILY", "2026-03-01"),
            self.entry("TARIFF-DAILY", "2026-03-03")])
        result = plan(inv, as_of=date(2026,3,3), local_dir=str(self.data))
        self.assertFalse(result["metadata_chain_complete"])
        self.assertFalse(result["verified_bytes_complete"])
        self.assertEqual([(r["information_type"], r["period"]) for r in result["missing"]],
                         [("TARIFF-MONTHLY","2026-02-01"),
                          ("TARIFF-DAILY","2026-03-02")])

    def test_lying_inventory_hash_or_missing_file_blocks_verified_bytes(self):
        rows = [self.entry("TARIFF-ANNUAL","2026-01-01"),
                self.entry("TARIFF-DAILY","2026-01-01")]
        inv = self.load(rows)
        self.assertFalse(plan(inv, as_of=date(2026,1,1))["verified_bytes_complete"])
        rows[0]["sha256"] = "b" * 64
        broken = self.load(rows)
        result = plan(broken, as_of=date(2026,1,1), local_dir=str(self.data))
        self.assertTrue(result["metadata_chain_complete"])
        self.assertFalse(result["verified_bytes_complete"])
        self.assertEqual(len(result["corrupt_or_missing_bytes"]), 1)

    def test_same_period_duplicate_rejected(self):
        row = self.entry("TARIFF-DAILY","2026-01-01")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.load([row, {**row, "reference": "other.xml"}])

    def test_illegal_reference_and_annual_month_rejected(self):
        row = self.entry("TARIFF-ANNUAL", "2026-01-01")
        with self.assertRaisesRegex(ValueError, "Invalid information"):
            self.load([{**row, "reference": "../etc/passwd"}])
        with self.assertRaisesRegex(ValueError, "January 1"):
            self.load([{**row, "period": "2026-02-01"}])
        with self.assertRaisesRegex(ValueError, "size/hash"):
            self.load([{**row, "size_bytes": 0}])
        with self.assertRaisesRegex(ValueError, "size/hash"):
            self.load([{**row, "sha256": "xyz"}])

    def test_never_accept_source_semantics_claim(self):
        with self.assertRaisesRegex(ValueError, "cannot certify"):
            self.load([], source_semantics_verified=True)
        with self.assertRaises(ValueError):
            self.load([{"information_type":"NI-UNKNOWN", "period":"2026-01-01",
                        "reference":"data", "sha256":"a"*64, "size_bytes":3}])

    def test_malformed_prior_year_and_file_escape(self):
        inv = self.load([])
        with self.assertRaises(ValueError):
            plan(inv, as_of=date(2020,12,31))
        record = self.entry("TARIFF-ANNUAL","2026-01-01")
        record["reference"] = ".."
        with self.assertRaises(ValueError):
            verified_local_file(record, str(self.data))

    def test_october_has_exact_nine_monthlies_and_fourteen_dailies(self):
        inv = self.load([])
        result = plan(inv, as_of=date(2026,10,14))
        # Annual + Jan..Sep rollups + Oct 1..14
        self.assertEqual(result["required_files"], 1+9+14)
        self.assertEqual(result["missing"][1]["period"], "2026-01-01")
        self.assertEqual(result["missing"][9]["period"], "2026-09-01")
        self.assertEqual(result["missing"][10]["period"], "2026-10-01")


if __name__ == "__main__":
    unittest.main()
