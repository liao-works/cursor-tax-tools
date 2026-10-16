"""Scheduled full-source GB/XI coverage contract; failure injection.

Weekly source refresh runs regardless of whether a daily incremental change
was detected. These workflows are SHADOW evidence only, never legal payable
tariff output or production DB install.
"""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]/".github"/"workflows"
GB="weekly-gb-full-source.yml"
XI="xi-public-full-measures.yml"

def inspect(gb: str, xi: str) -> list[str]:
    errors=[]
    gb_cron = re.search(r"(?m)^\s*- cron: '([^']+)'",gb)
    xi_cron = re.search(r"(?m)^\s*- cron: '([^']+)'",xi)
    if not gb_cron or gb_cron.group(1)!="0 1 * * 0":
        errors.append("GB weekly Sunday 01:00 UTC full source schedule missing")
    if not xi_cron or xi_cron.group(1)!="0 2 * * 0":
        errors.append("XI weekly Sunday 02:00 UTC full source schedule missing")
    for name, body in ((GB,gb),(XI,xi)):
        if re.search(r"(?m)^\s*contents:\s*write\s*$",body):
            errors.append(name+": elevated contents write permission")
        if not re.search(r"(?m)^\s*contents:\s*read\s*$",body):
            errors.append(name+": missing read permission")
        if "softprops/action-gh-release" in body or "--allow-partial" in body:
            errors.append(name+": production publishing/partial merging not permitted")
        if "workflow_dispatch:" not in body:
            errors.append(name+": missing manual source-only rerun")
        if "retention-days: 30" not in body:
            errors.append(name+": source audit retention absent")
        if "latest-data" in re.sub(r"(?m)^\s*#.*$", "",body) and re.search(
                r"(?m)^\s+tag_name:\s*latest-data",body):
            errors.append(name+": production release tag present")
    if "src.target_duty.dbt_version_discovery" not in gb or "--require-available" not in gb:
        errors.append("GB official latest version/schema discovery missing")
    if "--version \"$(cat gb-weekly-pin.txt)\"" not in gb:
        errors.append("GB full catalogue+measures are not pinned to one version")
    if "gb-weekly-original-measures.csv" not in gb or "gb-weekly-catalogue.db" not in gb:
        errors.append("GB original full measure CSV and full catalogue absent")
    if "sha(source)==download[\"sha256\"]==report[\"source_csv_sha256\"]" not in gb:
        errors.append("GB complete original source SHA parity missing")
    if 'db.execute("SELECT COUNT(*) FROM measures")' not in gb:
        errors.append("GB full expanded-measure count acceptance missing")
    if "report[\"production_eligible\"] is False" not in gb:
        errors.append("GB source-only no-payable-release safety missing")
    if "gb-daily-watch-pin-" not in gb or "gb-weekly-as-of.txt" not in gb:
        errors.append("GB weekly compact daily source pin artifact missing")
    if "xi_catalogue --as-of \"$(date -u +%F)\" --public-full-audit" not in xi:
        errors.append("XI scheduled catalogue must use current date")
    if xi.count('os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"')!=2:
        errors.append("XI scheduled source must NOT reuse prior run catalogues/shards")
    if "matrix:" not in xi or "worker: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]" not in xi or "max-parallel: 8" not in xi or "--workers 16" not in xi:
        errors.append("XI full-source sixteen-shard bounded concurrency coverage missing")
    if "xi_public_gap_ledger" not in xi or "xi_merge" not in xi:
        errors.append("XI independent gap audit/merge missing")
    if ("xi-daily-watch-baseline-" not in xi
            or "src.target_duty.xi_daily_baseline" not in xi
            or 'BASELINE: ${{ steps.daily_baseline.outcome }}' not in xi):
        errors.append("XI weekly compact accepted baseline artifact and fail gate missing")
    if "public_full_raw_source_coverage" not in xi or 'a["missing"]' in xi and 'a["missing"] == 0' not in xi:
        errors.append("XI full raw response zero-gap acceptance missing")
    if 'not a["ni_other_verified"]' not in xi and 'not a["ni_other_verified"] and not a["production_eligible"]' not in xi:
        errors.append("XI source not protected from payable rate certification")
    return errors


class WeeklyFullSourceSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gb=(ROOT/GB).read_text(encoding="utf-8")
        cls.xi=(ROOT/XI).read_text(encoding="utf-8")

    def test_weekly_source_schedules_and_full_replay_are_safe(self):
        self.assertEqual(inspect(self.gb,self.xi),[])

    def test_one_operator_marker_triggers_both_read_only_full_sources(self):
        for source in (self.gb, self.xi):
            self.assertIn("push:", source)
            self.assertIn("branches: [feat/parallel-scraper]", source)
            self.assertIn("ops/manual-full-source-run.txt", source)
            self.assertIn("contents: read", source)
            self.assertNotIn("contents: write", source)
            self.assertNotIn("softprops/action-gh-release", source)

    def test_gb_wrong_cron_or_unpinned_replay_detected(self):
        modified=self.gb.replace("0 1 * * 0","0 1 1 * *")
        self.assertTrue(any("schedule" in x for x in inspect(modified,self.xi)))
        modified=self.gb.replace("--version \"$(cat gb-weekly-pin.txt)\"","--version latest")
        self.assertTrue(any("pinned" in x for x in inspect(modified,self.xi)))

    def test_xi_stale_resume_and_missing_partition_detected(self):
        modified=self.xi.replace('os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"','True')
        self.assertTrue(any("reuse" in x for x in inspect(self.gb,modified)))
        modified=self.xi.replace("worker: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]","worker: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]")
        self.assertTrue(any("sixteen-shard" in x for x in inspect(self.gb,modified)))

    def test_cannot_restore_release_upload_write_access_or_partial_merge(self):
        assert inspect(self.gb,self.xi)==[]
        modified=self.gb+"\n      - uses: softprops/action-gh-release@v1\n"
        self.assertTrue(inspect(modified,self.xi))
        modified=self.xi.replace("contents: read","contents: write")
        self.assertTrue(inspect(self.gb,modified))
        modified=self.gb+"\n        --allow-partial\n"
        self.assertTrue(inspect(modified,self.xi))

    def test_missing_original_source_integrity_verification_detected(self):
        modified=self.gb.replace('sha(source)==download["sha256"]==report["source_csv_sha256"]',
                                 "sha(source)==report[\"source_csv_sha256\"]")
        self.assertTrue(any("SHA" in x for x in inspect(modified,self.xi)))


if __name__=="__main__":
    unittest.main()
