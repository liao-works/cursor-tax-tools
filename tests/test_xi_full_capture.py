"""Offline end-to-end managed XI source capture: never claims a payable duty."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.target_duty.xi_catalogue import build as build_catalogue
from src.target_duty.xi_frozen_catalogue import freeze
from src.target_duty.xi_full_capture import run

DAY = date(2026, 10, 14)


def record(chapter):
    code = chapter + "01000000"
    return {"type": "goods_nomenclature", "id": str(int(chapter) * 100),
            "attributes": {
                "goods_nomenclature_item_id": code,
                "goods_nomenclature_sid": int(chapter) * 100,
                "description": "synthetic, never a real duty",
                "producline_suffix": "80", "declarable": True,
                "validity_start_date": "2020-01-01",
                "validity_end_date": None}}


def response(code, day):
    return json.dumps({
        "data": {"type": "commodity", "id": code,
                 "attributes": {"goods_nomenclature_item_id": code},
                 "relationships": {"import_measures": {"data": []}}},
        "included": []}).encode("utf-8")


class FullCaptureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cat = str(self.root / "catalogue.db")
        self.manifest = str(self.root / "frozen-manifest.json")
        self.checkpoint = str(self.root / "checkpoint.db")
        self.receipt = str(self.root / "capture-progress.json")
        listing = {"data": [{"id": str(i)} for i in range(1, 98)]}
        payloads = {f"{i:02d}": {"data": [record(f"{i:02d}")]}
                    for i in range(1, 98)}
        build_catalogue(listing, payloads, self.cat, as_of=DAY,
                        full_managed_listing=True, official_listed_total=97)
        freeze(self.cat, self.manifest)

    def capture(self, **kwargs):
        return run(self.cat, self.manifest, self.checkpoint, as_of=DAY,
                   fetcher=kwargs.pop("fetcher", response),
                   batch_size=kwargs.pop("batch_size", 50),
                   max_batches=kwargs.pop("max_batches", 1),
                   delay=0, drift_delay=0,
                   report_path=self.receipt, **kwargs)

    def test_budget_stop_and_resume_complete_exactly_97_without_business_verification(self):
        partial = self.capture()
        self.assertEqual(partial["status"], "batch_budget_exhausted")
        self.assertEqual(partial["verified"], 50)
        self.assertEqual(partial["missing"], 47)
        self.assertFalse(partial["source_coverage_verified"])
        complete = self.capture()
        self.assertEqual(complete["status"], "source_coverage_verified_business_pending")
        self.assertEqual(complete["verified"], 97)
        self.assertEqual(complete["missing"], 0)
        self.assertEqual(complete["source_errors"], 0)
        self.assertEqual(len(complete["chapter_coverage"]), 97)
        self.assertTrue(complete["source_coverage_verified"])
        self.assertFalse(complete["production_eligible"])
        self.assertFalse(complete["ni_other_verified"])
        self.assertFalse(complete["atomic_official_snapshot_verified"])
        self.assertEqual(json.loads(Path(self.receipt).read_text())["verified"], 97)
        # Resume on complete checkpoint must not fetch or rewrite source bodies.
        self.capture(fetcher=lambda c, d: self.fail("complete checkpoint refetched"))
        with sqlite3.connect(self.checkpoint) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 97)

    def test_source_failure_is_durable_and_retried_on_next_invocation(self):
        calls = []
        def fail(code, day):
            calls.append(code)
            raise RuntimeError("official upstream temporarily failed")
        first = self.capture(batch_size=1, fetcher=fail)
        self.assertEqual(first["status"], "halted_without_progress")
        self.assertEqual(first["source_errors"], 1)
        self.assertTrue(Path(self.checkpoint).exists())
        with sqlite3.connect(self.checkpoint) as db:
            self.assertEqual(db.execute("SELECT status FROM requests").fetchone()[0],
                             "source_error")
        second = self.capture(batch_size=1)
        self.assertEqual(second["verified"], 1)
        self.assertEqual(second["source_errors"], 0)
        self.assertEqual(second["missing"], 96)

    def test_modified_checkpoint_is_rejected_before_any_new_request(self):
        self.capture(batch_size=1)
        with sqlite3.connect(self.checkpoint) as db:
            db.execute("UPDATE requests SET response_sha256=?", ("f" * 64,))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.capture(fetcher=lambda c, d: self.fail("tampered source was trusted"))

    def test_drift_halts_before_continuing_and_retains_receipt(self):
        self.capture(batch_size=1)
        with patch("src.target_duty.xi_full_capture.drift_audit",
                   return_value={"resume_source_stability_gate_pass": False,
                                 "changed_anchor_codes": ["0101000000"]}):
            with self.assertRaisesRegex(ValueError, "NEW frozen snapshot"):
                self.capture(fetcher=lambda c, d: self.fail("continued after drift"))
        report = json.loads(Path(self.receipt).read_text())
        self.assertEqual(report["status"], "halted_source_drift")
        self.assertFalse(report["production_eligible"])

    def test_wrong_day_and_unsafe_output_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "day mismatch"):
            run(self.cat, self.manifest, self.checkpoint,
                as_of=date(2026, 10, 13), fetcher=response)
        with self.assertRaisesRegex(ValueError, "production tariff DB"):
            run(self.cat, self.manifest, str(self.root / "tariffs.db"),
                as_of=DAY, fetcher=response)


if __name__ == "__main__":
    unittest.main()
