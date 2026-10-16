"""Keep the GitHub Actions surface focused on real collection, update and release."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / ".github" / "workflows"
EXPECTED = {
    "build.yml",
    "daily-official-source-watch.yml",
    "full-gb-xi-foundation.yml",
    "gb-ni-exact-full-source-diff.yml",
    "official-source-delta-response.yml",
    "production-tariff-freshness-watch.yml",
    "weekly-gb-full-source.yml",
    "xi-known-expiry-refetch.yml",
    "xi-public-full-measures.yml",
}

class CoreActionsInventory(unittest.TestCase):
    def test_exact_operational_and_one_ci_workflow_set(self):
        files = {p.name for p in ROOT.iterdir() if p.is_file()}
        self.assertEqual(files, EXPECTED)
        self.assertIn("pull_request:", (ROOT / "full-gb-xi-foundation.yml").read_text())
        self.assertNotIn("pull_request:", (ROOT / "build.yml").read_text())

    def test_xi_collection_and_automated_source_chain_remain(self):
        xi = (ROOT / "xi-public-full-measures.yml").read_text()
        self.assertIn("max-parallel: 8", xi)
        self.assertIn("--workers 16", xi)
        self.assertIn("Second pass - retry ONLY failed or missing XI responses", xi)
        for name in ("weekly-gb-full-source.yml", "xi-public-full-measures.yml",
                     "daily-official-source-watch.yml",
                     "production-tariff-freshness-watch.yml"):
            self.assertIn("schedule:", (ROOT / name).read_text())
        self.assertIn("workflow_run:", (ROOT / "gb-ni-exact-full-source-diff.yml").read_text())
        self.assertIn("workflow_run:", (ROOT / "official-source-delta-response.yml").read_text())


if __name__ == "__main__":
    unittest.main()
