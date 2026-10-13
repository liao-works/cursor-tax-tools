import copy
import unittest
from src.target_duty.xi_review_gate import evaluate, render_markdown


def example(code, comparison="ni_format_equivalent"):
    return {
        "code": code, "verdict": "candidate_review", "xi_candidate": "0%",
        "legacy_ni_rate": "0.00%", "comparison": comparison,
        "source": "HMRC-XI-v2", "effective_date_checked": "2026-10-13",
        "response_sha256": "b" * 64,
        "source_url": "https://www.trade-tariff.service.gov.uk/xi/commodities/" + code,
        "candidate_measures": [{
            "measure_type": "103", "geographical_area_id": "1011",
            "duty_expression_details": [{"base": "0%"}]
        }]
    }


class XIReviewGateTests(unittest.TestCase):
    def payload(self):
        codes = [f"{chapter:02d}00000000" for chapter in range(1, 13)]
        return {"kind": "xi-third-country-duty-review-only",
                "production_eligible": False, "as_of": "2026-10-13",
                "coverage": len(codes), "entries": [example(c) for c in codes]}

    def test_complete_evidence_still_never_production(self):
        result = evaluate(self.payload())
        self.assertTrue(result["evidence_complete"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["other_semantics_verified"])
        self.assertEqual(result["manual_review_count"], 0)
        self.assertIn("Production eligible: NO", render_markdown(result))

    def test_disparate_rate_is_a_manual_review_item(self):
        p = self.payload()
        p["entries"][0]["comparison"] = "ni_new_complex_duty"
        result = evaluate(p)
        self.assertEqual(result["manual_review_count"], 1)
        self.assertEqual(result["manual_review_items"][0]["code"], "0100000000")

    def test_non_original_date_or_missing_provenance_rejected(self):
        for field, value in (("effective_date_checked", "2025-01-01"),
                             ("response_sha256", ""), ("source", "HTML")):
            p = self.payload()
            p["entries"][0][field] = value
            with self.assertRaises(ValueError):
                evaluate(p)

    def test_bad_code_or_production_eligible_rejected(self):
        p = self.payload()
        p["entries"][0]["code"] = "01000"
        with self.assertRaises(ValueError):
            evaluate(p)
        p = self.payload()
        p["production_eligible"] = True
        with self.assertRaises(ValueError):
            evaluate(p)

    def test_missing_coverage_is_never_complete(self):
        p = self.payload()
        p["entries"] = p["entries"][:3]
        p["coverage"] = 3
        self.assertEqual(evaluate(p)["status"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
