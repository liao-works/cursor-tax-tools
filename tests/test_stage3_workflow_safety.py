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
        self.assertNotIn("contents: write",w.split("  publish-desktop-data:")[0])
        self.assertIn("needs: exact-code-diff",w)
        self.assertIn("github.event_name == 'workflow_run'",w)
        self.assertNotIn("--allow-partial",w)

    def test_release_publishes_gap_audit_and_refreshes_metadata_without_database_change(self):
        w=(WF/"gb-ni-exact-full-source-diff.yml").read_text()
        publisher=w.split("  publish-desktop-data:",1)[1]
        self.assertIn("desktop/rate-gap-audit.json",publisher)
        self.assertIn("PREVIOUS_META_FINGERPRINT",publisher)
        self.assertIn("NEW_META_FINGERPRINT",publisher)
        self.assertIn("gh release upload latest-data desktop/metadata.json desktop/rate-gap-audit.json --clobber",publisher)
        self.assertIn("unambiguous active China-applicable type 103 duties",publisher)
        self.assertIn("rate-gap-audit.json",publisher)

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

    def test_core_manual_source_launchers_retain_default_branch_targets(self):
        for name in ("weekly-gb-full-source.yml", "xi-public-full-measures.yml"):
            with self.subTest(workflow=name):
                body=(WF/name).read_text()
                self.assertIn("branches: [feat/parallel-scraper]",body)
                self.assertIn("workflow_dispatch:",body)
                self.assertIn("contents: read",body)
                self.assertNotIn("contents: write",body)
        self.assertIn("ops/manual-xi-full-run.txt",
                      (WF/"xi-public-full-measures.yml").read_text())

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
