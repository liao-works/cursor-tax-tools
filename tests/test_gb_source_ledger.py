"""Complete GB review ledger: all active leaves, never invent payable duty."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.target_duty.gb_source_ledger import build,lookup,_has_qualifier,_simple_percentage
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
            "0207412010":(ACTIVE,[row(rate="0%",sid="22",condition="")]),
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

    def test_exact_code_lookup_retains_complex_raw_evidence_and_unavailable(self):
        r=lookup(str(self.src),"0101210000")
        self.assertEqual(r["commodity_code"],"0101210000")
        self.assertEqual(r["version"],"v4.0.1627")
        self.assertEqual(r["active_103_1011_count"],1)
        self.assertEqual(r["official_candidate_evidence"][0]["expression"],
                         "8% + 5 GBP / 100 kg")
        self.assertEqual(r["official_candidate_evidence"][0]["conditions"],"certificate")
        self.assertIsNone(r["payable_rate"])
        self.assertFalse(r["production_eligible"])
        missing=lookup(str(self.src),"9930270000")
        self.assertEqual(missing["non_gb_1080_context_rows"],1)
        self.assertEqual(missing["active_103_1011_count"],0)
        self.assertIsNone(missing["payable_rate"])
        not_leaf=lookup(str(self.src),"0101210001")
        self.assertEqual(not_leaf["status"],
                         "not_in_current_official_active_leaf_catalogue")
        self.assertIsNone(not_leaf["payable_rate"])
        with self.assertRaisesRegex(ValueError,"exactly ten digits"):
            lookup(str(self.src),"0101")

    def test_parse_empty_json_qualifiers_and_compound_expressions(self):
        for x in (None,"","[]","[ ]","{}","{ }","null","None","#NA"):
            self.assertFalse(_has_qualifier(x),repr(x))
        for x in ('[{"condition":"certificate"}]',"broken", '[invalid',"ABCD", "0"):
            self.assertTrue(_has_qualifier(x),repr(x))
        self.assertTrue(_simple_percentage("0%"))
        self.assertTrue(_simple_percentage("8.50 %"))
        self.assertFalse(_simple_percentage("5% + 10 GBP/100 kg"))
        self.assertFalse(_simple_percentage("12.4 GBP / 100 kg"))
        self.assertFalse(_simple_percentage(None))

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
