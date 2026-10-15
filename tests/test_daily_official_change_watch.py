"""Daily change detection includes valid-as-of date changes without new DBT pin."""
import copy
import hashlib
import json
import unittest
from datetime import date
from pathlib import Path
from src.target_duty.daily_official_change_watch import classify,inspect,check_baseline

ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/"ops/xi-daily-canary-baseline-20261014.json").read_text())
GB={"state":"ready_for_full_data_replay","newest_official_version":"v4.0.1627"}
PIN="v4.0.1627"
CODES=BASE["canary_response_sha256"]


def run(day=date(2026,10,14),*, gb=None,sha=None,errors=None):
    return classify(BASE,official_gb=gb or GB,gb_base_version=PIN,
                    ni_response_sha=sha if sha is not None else dict(CODES),
                    source_errors=errors,on=day)

class DailyOfficialChangeTests(unittest.TestCase):
    def test_unchanged_six_codes_and_official_version_not_full_ni_proof(self):
        r=run()
        self.assertEqual(r["review_state"],"NO_SIGNALS_IN_CHECKED_SCOPE_ONLY")
        self.assertEqual(r["xi_canaries_checked"],6)
        self.assertFalse(r["xi_sample_covers_entire_source"])
        self.assertFalse(r["xi_full_source_changes_excluded"])
        self.assertFalse(r["release_permitted"])

    def test_real_eight_measures_expiring_oct_15_force_review_even_same_gb_version(self):
        r=run(date(2026,10,15))
        self.assertIn("NI_KNOWN_103_105_EFFECTIVE_END_BOUNDARIES",r["review_reasons"])
        self.assertEqual(r["known_ni_third_country_expirations_since_baseline"],
                         [{"date":"2026-10-15","candidate_measures":8}])
        self.assertFalse(r["gb_version_changed"])
        self.assertEqual(r["review_state"],"REVIEW_OR_FULL_REPLAY_REQUIRED")

    def test_late_or_skipped_run_does_not_skip_expiry_oct_31(self):
        r=run(date(2026,10,31))
        dates={e["date"]:e["candidate_measures"] for e in
               r["known_ni_third_country_expirations_since_baseline"]}
        self.assertEqual(dates["2026-10-15"],8)
        self.assertEqual(dates["2026-10-31"],47)
        self.assertIn("NI_WHOLE_SOURCE_BASELINE_TOO_OLD",r["review_reasons"])

    def test_official_new_gb_version_requires_full_replay(self):
        r=run(gb={"state":"ready_for_full_data_replay",
                  "newest_official_version":"v4.0.1628"})
        self.assertTrue(r["gb_version_changed"])
        self.assertIn("GB_OFFICIAL_VERSION_CHANGED_FULL_REPLAY_REQUIRED",r["review_reasons"])
        self.assertFalse(r["release_permitted"])

    def test_sample_response_byte_change_is_a_signal_not_certified_delta(self):
        changed=dict(CODES)
        changed["0101210000"]="a"*64
        r=run(sha=changed)
        self.assertEqual(r["xi_changed_raw_response_codes"],["0101210000"])
        self.assertIn("NI_CANARY_RAW_CHANGE_SCOPE_UNKNOWN",r["review_reasons"])
        self.assertFalse(r["ni_other_payable_verified"])

    def test_source_outage_or_unavailable_official_gb_never_means_no_change(self):
        codes=dict(CODES)
        del codes["0101210000"]
        r=run(sha=codes,errors={"0101210000":"HTTP 429"},
              gb={"state":"source_unavailable"})
        self.assertIn("OFFICIAL_SOURCE_FETCH_OR_SCHEMA_FAILURE",r["review_reasons"])
        self.assertEqual(r["xi_canaries_checked"],5)
        self.assertIn("GB",r["source_errors"])

    def test_expired_baseline_and_missing_expiry_ledger_rejected(self):
        r=run(date(2026,10,23))
        self.assertIn("NI_WHOLE_SOURCE_BASELINE_TOO_OLD",r["review_reasons"])
        copy_baseline=copy.deepcopy(BASE)
        del copy_baseline["known_103_105_end_dates"]
        with self.assertRaisesRegex(ValueError,"expiry ledger missing"):
            check_baseline(copy_baseline,date(2026,10,15))

    def test_inspect_rejects_wrong_public_commodity_body(self):
        def fetch(code, on):
            return json.dumps({"data":{"type":"commodity",
                       "attributes":{"goods_nomenclature_item_id":"9999999999"}}}).encode()
        r=inspect(BASE,on=date(2026,10,15),gb_base_version=PIN,
                  fetcher=fetch,gb_metadata=GB,sleep_seconds=0)
        self.assertEqual(r["xi_canaries_checked"],0)
        self.assertEqual(len(r["source_errors"]),6)
        self.assertEqual(r["review_state"],"REVIEW_OR_FULL_REPLAY_REQUIRED")

    def test_future_baseline_never_accepted(self):
        with self.assertRaisesRegex(ValueError,"future"):
            run(date(2026,10,13))

if __name__=="__main__":
    unittest.main()
