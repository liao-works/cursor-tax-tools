"""Complete GB review ledger: all active leaves, never invent payable duty."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.target_duty.gb_source_ledger import build
from test_gb_review_diff import make_snapshot,row,ACTIVE,MISSING,CONTEXT


class GBSourceLedgerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.src=self.root/"gb-compact.db"
        self.csv=self.root/"complete.csv"
        self.report=self.root/"complete.json"
        make_snapshot(self.src,"v4.0.1627",{
            "0101210000":(ACTIVE,[row(rate="8% + 5 GBP / 100 kg",
                                     condition="certificate")]),
            "0207412010":(ACTIVE,[row(rate="0%",sid="22")]),
            "9930240000":(MISSING,[]),
            "9930270000":(CONTEXT,[row(rate="0%",geo="1080",sid="24")]),
        })

    def test_all_active_leaf_codes_explicit_unresolved_and_conditional(self):
        result=build(str(self.src),str(self.csv),str(self.report))
        self.assertTrue(result["source_coverage_verified"])
        self.assertTrue(result["exactly_one_row_per_active_leaf"])
        self.assertEqual(result["official_active_leaf_codes"],4)
        self.assertEqual(result["candidate_codes"],2)
        self.assertEqual(result["unresolved_codes"],2)
        self.assertEqual(result["complex_or_ambiguous_candidate_codes"],1)
        self.assertFalse(result["all_rates_payable_verified"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["ni_other_verified"])
        with self.csv.open(encoding="utf-8",newline="") as file:
            rows={x["code"]:x for x in csv.DictReader(file)}
        self.assertEqual(len(rows),4)
        complex_rate=rows["0101210000"]
        self.assertEqual(json.loads(complex_rate["duty_expressions_json"]),
                         ["8% + 5 GBP / 100 kg"])
        self.assertEqual(complex_rate["payable_rate"],"")
        self.assertEqual(complex_rate["missing_or_review_reason"],
                         "conditional_or_additional_code_requires_business_rules")
        self.assertEqual(rows["0207412010"]["payable_rate"],"")
        self.assertEqual(json.loads(rows["0207412010"]["duty_expressions_json"]),["0%"])
        self.assertEqual(json.loads(rows["9930270000"]["duty_expressions_json"]),[])
        self.assertEqual(rows["9930270000"]["non_gb_1080_context_count"],"1")
        self.assertIn("not_gb",rows["9930270000"]["missing_or_review_reason"])
        self.assertIn("not_zero_rate",rows["9930240000"]["missing_or_review_reason"])

    def test_corrupted_manifest_rejected_without_outputs(self):
        manifest=self.src.with_suffix(".json")
        original=json.loads(manifest.read_text())
        original["review_database_sha256"]="f"*64
        manifest.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            build(str(self.src),str(self.csv),str(self.report))
        self.assertFalse(self.csv.exists())
        self.assertFalse(self.report.exists())

    def test_refuses_source_overwrite(self):
        with self.assertRaisesRegex(ValueError,"overwrite"):
            build(str(self.src),str(self.src),str(self.report))


if __name__=="__main__":
    unittest.main()
