"""Prevent archive-credential dependencies from blocking raw XI collection.

The managed HMRC workflow MUST be executable without AWS or signing
secrets when archive_mode=none. Source completeness is separately gated;
none/signed/offsite never establishes legally payable NI Other.
"""
import unittest
from pathlib import Path

WORKFLOW=Path(".github/workflows/xi-managed-coverage.yml")
EXPANDED=Path(".github/workflows/xi-public-five-chapter-probe.yml")

class SourceFirstWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=WORKFLOW.read_text(encoding="utf-8")
        cls.expanded=EXPANDED.read_text(encoding="utf-8")

    def test_managed_collection_not_blocked_by_unconfigured_cloud(self):
        source=self.source
        self.assertIn("archive_mode:",source)
        self.assertIn("default: 'none'",source)
        self.assertIn("options: ['none', 'signed', 'offsite']",source)
        self.assertIn("if: ${{ inputs.archive_mode == 'signed' || inputs.archive_mode == 'offsite' }}",source)
        self.assertEqual(source.count("if: ${{ inputs.archive_mode == 'offsite' }}"),2)
        for stage in (
            "Enumerate all official managed XI chapters",
            "Plan next bounded XI shard",
            "Download fixed bounded XI shard",
            "Merge and replay verify original + current checkpoint",
            "Verify merged raw source scope",
            "Reconcile every XI chapter",
            "Seal immutable previous-to-current XI source checkpoint continuity",
        ):
            self.assertIn(stage,source)
        sign=source.index("      - name: Sign and verify managed")
        capture=source.index("      - name: Seal immutable previous-to-current XI")
        self.assertLess(capture,sign)
        self.assertLess(sign,source.index("Archive immutable original catalogue"))
        self.assertIn("if: always()",source)
        self.assertIn("xi-merged-shadow.db",source)
        self.assertIn("xi-chapter-coverage-receipt.json",source)

    def test_strict_source_results_are_not_business_approval(self):
        source=self.source
        self.assertIn("production_eligible",source)
        self.assertIn("ni_other_verified",source)
        self.assertIn("source errors",source)
        self.assertIn("SOURCE_COLLECTION_COMPLETE",source)
        self.assertIn("previous/xi-checkpoint-chain.json",source)

    def test_public_five_chapter_probe_is_bounded_and_never_production(self):
        source=self.expanded
        self.assertIn("--public-canary --max-chapters 5",source)
        self.assertIn("--limit 20 --min-interval 2.0",source)
        self.assertIn("continue-on-error: true",source)
        self.assertIn("Fail if a source endpoint was unavailable",source)
        self.assertIn("r['failed_responses']!=0",source)
        self.assertIn("result['managed_full_source_coverage_pass'] is False",source)
        self.assertNotIn("HMRC_CLIENT_SECRET",source)
        self.assertNotIn("XI_S3_",source)

if __name__=="__main__":
    unittest.main()
