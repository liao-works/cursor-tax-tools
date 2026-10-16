"""End-to-end source refresh controls must not promote original data to tariffs."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/".github"/"workflows"

class SourceRefreshClosureTests(unittest.TestCase):
    def test_daily_change_initiates_source_only_targeted_replay(self):
        daily=(ROOT/"daily-official-source-watch.yml").read_text()
        reactive=(ROOT/"official-source-delta-response.yml").read_text()
        self.assertIn("daily-official-source-signals-",daily)
        self.assertIn("workflow_run:",reactive)
        self.assertIn("Daily official GB/NI source-change",reactive)
        self.assertIn("actions: write",reactive)
        self.assertIn("contents: read",reactive)
        self.assertIn("run[\"head_branch\"]!=\"feat/parallel-scraper\"",reactive)
        self.assertIn("xi_canary_delta_refetch",reactive)
        self.assertIn("xi_changed == 'true'",reactive)
        self.assertIn("gb_official_schema_ready",reactive)
        self.assertIn("gb_refresh == 'true'",reactive)
        self.assertIn("gh workflow run weekly-gb-full-source.yml",reactive)
        self.assertNotIn("contents: write",reactive)
        self.assertNotIn("softprops/action-gh-release",reactive)
        self.assertNotIn("gh release upload",reactive)

    def test_full_source_accepted_snapshots_trigger_exact_diff(self):
        diff=(ROOT/"gb-ni-exact-full-source-diff.yml").read_text()
        self.assertIn("workflow_run:",diff)
        self.assertIn("Weekly GB full official source",diff)
        self.assertIn("XI public full raw measure evidence",diff)
        self.assertIn("head_repository",diff)
        self.assertIn('triggering["conclusion"]!="success"',diff)
        self.assertIn("xi-public-full-acceptance",diff)
        self.assertIn("weekly-gb-full-source",diff)
        self.assertIn("full_source_delta_scope",diff)
        self.assertIn("exact-changed-codes.csv",diff)
        self.assertIn('steps.pair.outputs.has_pair == \'true\'',diff)
        self.assertNotIn("contents: write",diff)
        self.assertNotIn("softprops/action-gh-release",diff)

    def test_sixteen_shards_remain_rate_limited_and_recoverable(self):
        xi=(ROOT/"xi-public-full-measures.yml").read_text()
        self.assertIn("max-parallel: 8",xi)
        self.assertIn("worker: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]",xi)
        self.assertIn("--workers 16",xi)
        self.assertIn("len(paths)!=16",xi)
        self.assertIn("previous.issubset(assigned)",xi)
        self.assertIn("Second pass - retry ONLY failed or missing XI responses",xi)
        self.assertIn("if: steps.crawl.outcome == 'failure'",xi)
        self.assertIn("RETRY: ${{ steps.retry.outcome }}",xi)
        self.assertIn('"$CRAWL" != success && "$RETRY" != success',xi)
        self.assertIn('config.get("catalogue_sha256")==frozen["catalogue_sha256"]',xi)
        self.assertIn("if: always()",xi)
        self.assertIn("retention-days: 30",xi)
        self.assertNotIn("contents: write",xi)


if __name__=="__main__":
    unittest.main()
