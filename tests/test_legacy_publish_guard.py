"""CI protection for 3 legacy concurrent latest-data writers."""
import unittest
from pathlib import Path

from src.target_duty.legacy_publish_guard import WORKFLOWS, inspect, verify

ROOT=Path(__file__).resolve().parents[1]/".github"/"workflows"


class LegacyPublisherIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources={name:(ROOT/name).read_text(encoding="utf-8") for name in WORKFLOWS}

    def test_all_three_workflows_are_review_only(self):
        report=inspect(str(ROOT))
        self.assertTrue(report["safe_review_only"],report)
        self.assertEqual(report["workflows_checked"],3)
        self.assertTrue(all(x["may_publish_latest_data"] is False for x in report["results"]))

    def test_restore_concurrent_monthly_cron_fails(self):
        name=WORKFLOWS[0]
        content=self.sources[name].replace("  workflow_dispatch:","  schedule:\n    - cron: '0 4 1 * *'\n  workflow_dispatch:")
        self.assertFalse(verify(name,content)["safe_review_only"])

    def test_restore_gh_release_or_mutation_permission_fails(self):
        name=WORKFLOWS[1]
        content=self.sources[name]
        a=verify(name,content+"\n      - uses: softprops/action-gh-release@v1\n")
        self.assertFalse(a["safe_review_only"])
        b=verify(name,content.replace("contents: read","contents: write"))
        self.assertFalse(b["safe_review_only"])

    def test_partial_merge_reenable_fails(self):
        name=WORKFLOWS[2]
        content=self.sources[name]+"\n          --allow-partial \\\n"
        self.assertFalse(verify(name,content)["safe_review_only"])

    def test_exhausted_retry_success_mask_fails(self):
        name=WORKFLOWS[2]
        content=self.sources[name].replace("exit 1  # 报告错误，禁止将失败当作成功","exit 0  # 吞掉超时")
        result=verify(name,content)
        self.assertFalse(result["safe_review_only"],result)
        self.assertTrue(any("Timed-out" in s for s in result["violations"]))

    def test_absence_of_a_workflow_never_passes(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            report=inspect(temp)
            self.assertFalse(report["safe_review_only"])
            self.assertEqual(report["workflows_checked"],3)

if __name__ == "__main__":
    unittest.main()
