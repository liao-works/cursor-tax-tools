"""Desktop checks PUBLISHED updates on startup/daily, not raw source changes."""
import unittest
from pathlib import Path
from src.clients.data_update_notifier import (
    CHECK_INTERVAL_MS, RETRY_INTERVAL_MS, DEFAULT_METADATA_URL, OLD_METADATA_URL,
    assess_published_update, check_delay_ms, configured_metadata_url,
    release_notification_key,
)

ROOT=Path(__file__).resolve().parents[1]
REMOTE={"version":"data-20261016","file_hash":"sha256:"+"a"*64,
        "record_count":16729,"changes_summary":{"total_updates":0,
        "min_change_threshold":10}}
OLD={"version":"data-20261001"}
LOCAL={"exists":True,"file_hash":"sha256:"+"b"*64,"record_count":16728}

class DesktopDailyDataUpdateTests(unittest.TestCase):
    def test_new_release_not_suppressed_when_change_count_is_zero(self):
        needed,reason,detail=assess_published_update(REMOTE,LOCAL,OLD)
        self.assertTrue(needed)
        self.assertIn("data-20261016",reason)
        self.assertEqual(detail["priority"],"high")

    def test_identical_hash_is_current_despite_different_version_or_timestamp(self):
        same={**LOCAL,"file_hash":REMOTE["file_hash"],"record_count":16000}
        result=assess_published_update(REMOTE,same,OLD)
        self.assertFalse(result[0])

    def test_no_local_db_prompts_install(self):
        self.assertTrue(assess_published_update(REMOTE,{"exists":False},None)[0])

    def test_missing_trusted_hash_and_version_fail_without_fake_notice(self):
        for candidate in ({**REMOTE,"file_hash":None},
                          {**REMOTE,"file_hash":"bad"},
                          {**REMOTE,"version":None}):
            with self.subTest(candidate=candidate),self.assertRaises(ValueError):
                assess_published_update(candidate,LOCAL,OLD)
        with self.assertRaises(ValueError):
            release_notification_key({"file_hash":None})

    def test_repeat_notices_key_by_published_file_digest(self):
        self.assertEqual(release_notification_key(REMOTE),REMOTE["file_hash"])
        self.assertEqual(release_notification_key({**REMOTE,"version":"renamed"}),
                         release_notification_key(REMOTE))
        self.assertNotEqual(release_notification_key(REMOTE),
                            release_notification_key({**REMOTE,
                                "file_hash":"sha256:"+"c"*64}))

    def test_daily_and_retry_timing(self):
        self.assertEqual(CHECK_INTERVAL_MS,86_400_000)
        self.assertEqual(RETRY_INTERVAL_MS,3_600_000)
        self.assertEqual(check_delay_ms(succeeded=True),CHECK_INTERVAL_MS)
        self.assertEqual(check_delay_ms(succeeded=False),RETRY_INTERVAL_MS)

    def test_retired_url_migration_preserves_custom_settings(self):
        self.assertEqual(configured_metadata_url(OLD_METADATA_URL),DEFAULT_METADATA_URL)
        self.assertEqual(configured_metadata_url(""),DEFAULT_METADATA_URL)
        self.assertEqual(configured_metadata_url("https://example.test/data.json"),
                         "https://example.test/data.json")


if __name__=="__main__":
    unittest.main()
