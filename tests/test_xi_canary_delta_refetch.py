"""Live source-only canary changes are bounded, independently replayed, never published."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.target_duty.xi_canary_delta_refetch import review


def payload(code, day):
    return json.dumps({
        "data":{"type":"commodity","id":code,
            "attributes":{"goods_nomenclature_item_id":code},
            "relationships":{"import_measures":{"data":[]}}},
        "included":[]}).encode()


class DailyCanaryDeltaRefetchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.signal=self.root/"daily-signal.json"
        self.db=self.root/"source.db"
        self.report=self.root/"result.json"
        self.base={
            "kind":"gb-ni-daily-official-change-signal-v1",
            "checked_as_of":"2026-10-16",
            "release_permitted":False,
            "production_database_mutated":False,
            "xi_sample_covers_entire_source":False,
            "xi_changed_raw_response_codes":["0805102210"],
            "source_errors":{"GB":"Official DBT schema unavailable"}
        }

    def run_review(self, changed=None, fetcher=payload):
        data=copy.deepcopy(self.base)
        if changed is not None:
            data["xi_changed_raw_response_codes"]=changed
        self.signal.write_text(json.dumps(data),encoding="utf-8")
        return review(str(self.signal),str(self.db),str(self.report),fetcher=fetcher)

    def test_realistic_single_changed_code_replayed_and_still_not_certified(self):
        r=self.run_review()
        self.assertTrue(r["public_changed_canaries_complete"])
        self.assertEqual(r["verified_responses"],1)
        self.assertEqual(r["failed_responses"],0)
        self.assertFalse(r["production_eligible"])
        self.assertFalse(r["ni_other_payable_verified"])
        self.assertFalse(r["all_XI_source_changes_captured"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(conn.execute("SELECT code FROM raw_responses").fetchone()[0],
                             "0805102210")

    def test_no_changed_canaries_never_mistaken_for_whole_source(self):
        r=self.run_review(changed=[])
        self.assertEqual(r["verified_responses"],0)
        self.assertFalse(r["all_XI_source_changes_captured"])

    def test_source_outage_retains_failed_receipt_without_accepted_db(self):
        def offline(code, day):
            raise OSError("HTTP 429")
        r=self.run_review(fetcher=offline)
        self.assertEqual(r["failed_responses"],1)
        self.assertFalse(r["public_changed_canaries_complete"])
        self.assertFalse(self.db.exists())
        self.assertTrue(self.report.exists())

    def test_wrong_code_or_non_source_signal_is_rejected(self):
        self.signal.write_text(json.dumps({**self.base,
            "xi_changed_raw_response_codes":["../../bad"]}))
        with self.assertRaisesRegex(ValueError,"Invalid bounded"):
            review(str(self.signal),str(self.db),str(self.report),fetcher=payload)
        self.signal.write_text(json.dumps({**self.base,
            "source_errors":{"0805102210":"HTTP 500"}}))
        with self.assertRaisesRegex(ValueError,"Missing/failed XI"):
            review(str(self.signal),str(self.db),str(self.report),fetcher=payload)
        self.signal.write_text(json.dumps({**self.base,"release_permitted":True}))
        with self.assertRaisesRegex(ValueError,"Untrusted daily"):
            review(str(self.signal),str(self.db),str(self.report),fetcher=payload)


if __name__=="__main__":
    unittest.main()
