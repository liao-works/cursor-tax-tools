"""P0/P1 staging: daily signal checks, scheduled weekly full source, no publisher."""
import unittest
from pathlib import Path
import re

WORKFLOWS=Path(__file__).resolve().parents[1]/".github"/"workflows"

class StagedReadOnlySourceTests(unittest.TestCase):
    def test_daily_source_has_clock_and_no_production_writer(self):
        w=(WORKFLOWS/"daily-official-source-watch.yml").read_text()
        self.assertIn("cron: '30 0 * * *'",w)
        self.assertIn("contents: read",w)
        self.assertIn("xi-daily-watch-baseline-",w)
        self.assertIn("--gb-baseline-date",w)
        self.assertIn("source_receipts",w)
        self.assertNotIn("softprops/action-gh-release",w)
        self.assertNotIn("contents: write",w)

    def test_weekly_full_sources_are_scheduled_read_only(self):
        for name, cron in (
            ("weekly-gb-full-source.yml", "0 1 * * 0"),
            ("xi-public-full-measures.yml", "0 2 * * *"),
        ):
            with self.subTest(name=name):
                w=(WORKFLOWS/name).read_text()
                self.assertIn("workflow_dispatch:",w)
                self.assertIn("schedule:",w)
                self.assertIn("cron: '"+cron+"'",w)
                self.assertIn("contents: read",w)
                self.assertNotIn("contents: write",w)
                self.assertNotIn("--allow-partial",w)
                self.assertNotIn("softprops/action-gh-release",w)
                self.assertNotRegex(w,r"\bgh\s+release\s+(?:create|edit|upload)\b")
                self.assertNotRegex(w,r"(?m)^\s*tag_name:\s*latest-data")

    def test_gb_pinned_complete_original_csv_and_immutable_metadata(self):
        w=(WORKFLOWS/"weekly-gb-full-source.yml").read_text()
        for required in ("--require-available","gb-weekly-original-measures.csv",
                         "gb-weekly-measures.json","gb-weekly-catalogue.db",
                         'sha(source)==download["sha256"]==report["source_csv_sha256"]',
                         'report["production_eligible"] is False',
                         "gb-daily-watch-pin-"):
            self.assertIn(required,w)

    def test_xi_all_sixteen_current_day_shards_and_raw_acceptance(self):
        w=(WORKFLOWS/"xi-public-full-measures.yml").read_text()
        for required in ("worker: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]", "max-parallel: 8", "--workers 16","xi_public_full_capture",
                         "xi_public_gap_ledger","xi_merge","xi_daily_baseline",
                         "xi-daily-watch-baseline-","public_full_raw_source_coverage",
                         'a["ni_other_verified"]'):
            self.assertIn(required,w)
        self.assertEqual(w.count('os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"'),2)
        self.assertIn('--as-of "$(date -u +%F)"',w)
        self.assertIn('[[ "$COLLECT" != success || "$MERGE" != success',w)

    def test_frozen_source_must_remain_explicitly_untrusted(self):
        src=(Path(__file__).resolve().parents[1]/"ops"/"xi-daily-canary-baseline-20261014.json").read_text()
        import json
        baseline=json.loads(src)
        self.assertFalse(baseline["ni_other_verified"])
        self.assertFalse(baseline["production_eligible"])
        self.assertGreaterEqual(baseline["original_codes_verified"],16000)

if __name__=="__main__":
    unittest.main()
