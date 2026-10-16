"""An approved full-source diff or NI date refetch must never publish."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WF=ROOT/".github"/"workflows"


class Stage3WorkflowSafetyTests(unittest.TestCase):
    def test_two_accepted_full_source_runs_required_and_manual_only(self):
        w=(WF/"gb-ni-exact-full-source-diff.yml").read_text()
        for needle in ("workflow_dispatch:","before_run_id","after_run_id",
                       "weekly-gb-full-source-","xi-public-full-acceptance-",
                       "full_source_delta_scope","contents: read","actions: read"):
            self.assertIn(needle,w)
        self.assertNotIn("schedule:",w)
        self.assertNotIn("softprops/action-gh-release",w)
        self.assertNotIn("contents: write",w)
        self.assertNotIn("--allow-partial",w)

    def test_exact_ni_expirations_from_full_conditions_only(self):
        w=(WF/"xi-known-expiry-refetch.yml").read_text()
        for needle in ("workflow_dispatch:","original_conditions_run_id",
                       "xi-original-condition-evidence-","xi_date_impact_queue",
                       "xi_targeted_delta_capture","source_success_complete",
                       "contents: read","actions: read"):
            self.assertIn(needle,w)
        self.assertNotIn("schedule:",w)
        self.assertNotIn("contents: write",w)
        self.assertNotIn("softprops/action-gh-release",w)
        self.assertNotIn("tariffs.db",w)
        self.assertNotIn("latest-data",w)

    def test_on_demand_source_markers_are_runnable_after_merge(self):
        # Source-only operator markers must target the actual default branch;
        # review PR branches are not persistent production launch points.
        for name in (
            "dbt-newest-replay-shadow.yml",
            "xi-condition-source-review.yml",
            "xi-date-impact-queue.yml",
            "xi-derived-expression-review.yml",
            "xi-duty-applicability-review.yml",
            "xi-full-managed-one-shot.yml",
            "xi-live-date-canary.yml",
            "xi-targeted-delta-refetch.yml",
        ):
            with self.subTest(workflow=name):
                w=(WF/name).read_text()
                self.assertIn("branches: [feat/parallel-scraper]",w)
                self.assertIn("workflow_dispatch:",w)
                self.assertIn("contents: read",w)
                self.assertNotIn("contents: write",w)
                self.assertNotIn("softprops/action-gh-release",w)

    def test_source_modules_are_review_only(self):
        for path in ("full_source_delta_scope.py","xi_date_impact_queue.py",
                     "xi_targeted_delta_capture.py"):
            with self.subTest(path=path):
                s=(ROOT/"src"/"target_duty"/path).read_text()
                self.assertIn("production_eligible",s)
                self.assertNotIn("gh release upload",s)
                self.assertNotIn("softprops/action-gh-release",s)


if __name__=="__main__":
    unittest.main()
