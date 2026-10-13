import unittest
from unittest.mock import patch
from src.target_duty.xi_probe import summarize_response, fetch_commodity, fetch_public_commodity, candidate_rate, measure_active


class XIProbeTest(unittest.TestCase):
    def test_only_reports_measure_103_no_auto_verification(self):
        payload = {
            "data": {"relationships": {"import_measures": {"data": [
                {"id": "a", "type": "measure"}, {"id": "b", "type": "measure"}]}}},
            "included": [
                {"id": "a", "type": "measure",
                 "attributes": {"resolved_duty_expression": "4%"},
                 "relationships": {
                     "measure_type": {"data": {"id": "103", "type": "measure_type"}},
                     "geographical_area": {"data": {"id": "1011", "type": "geographical_area"}},
                     "measure_conditions": {"data": []},
                     "duty_expression": {"data": {"id": "duty-a", "type": "duty_expression"}}}},
                {"id": "duty-a", "type": "duty_expression",
                 "attributes": {"base": "4%"}},
                {"id": "b", "type": "measure",
                 "attributes": {"resolved_duty_expression": "0%"},
                 "relationships": {
                     "measure_type": {"data": {"id": "142", "type": "measure_type"}}}}
            ]
        }
        report = summarize_response(payload, "0101210000")
        self.assertEqual(report["status"], "review_required")
        self.assertIsNone(report["rate"])
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["candidates"][0]["duty_expression"], "4%")
        self.assertEqual(report["candidates"][0]["duty_expression_details"], [{"base": "4%"}])
        self.assertEqual(report["counts"]["non_target_measure"], 1)
        self.assertEqual(report["measure_types_observed"]["103"], 1)
        self.assertEqual(report["measure_types_observed"]["142"], 1)

    def test_candidate_uses_actual_duty_expression_relationship(self):
        base = {"measure_type": "103", "geographical_area_id": "1011",
                "has_conditions": False, "additional_code_present": False,
                "excluded_countries_present": False,
                "duty_expression_details": [{"base": "10.20 % + 93.10 EUR / 100 kg"}]}
        self.assertEqual(candidate_rate([base]), "10.20 % + 93.10 EUR / 100 kg")
        self.assertIsNone(candidate_rate([{**base, "has_conditions": True}]))
        self.assertIsNone(candidate_rate([base, base]))
        self.assertIsNone(candidate_rate([{**base, "geographical_area_id": "GB"}]))

    def test_future_and_expired_measures_cannot_be_current_candidates(self):
        from datetime import date
        item = {"measure_type": "103", "geographical_area_id": "1011",
                "has_conditions": False, "additional_code_present": False,
                "effective_start_date": "2026-01-01T00:00:00.000Z",
                "effective_end_date": "2026-12-31T00:00:00.000Z",
                "duty_expression_details": [{"base": "11.50 %"}]}
        self.assertEqual(candidate_rate([item], date(2026, 10, 13)), "11.50 %")
        self.assertIsNone(candidate_rate([item], date(2027, 1, 1)))
        self.assertIsNone(candidate_rate([item], date(2025, 12, 31)))
        self.assertIsNone(candidate_rate([{**item, "effective_start_date": "bad"}], date(2026, 10, 13)))
        self.assertFalse(measure_active({**item, "effective_start_date": None}, date(2026, 10, 13)))

    def test_missing_includes_not_claimed_as_rate(self):
        result = summarize_response({"data": {"relationships": {
            "import_measures": {"data": [{"id": "absent", "type": "measure"}]}}}},
            "0101210000")
        self.assertIsNone(result["rate"])
        self.assertEqual(result["counts"]["unresolved_import_measure"], 1)

    def test_public_probe_accepts_valid_code(self):
        import json
        from datetime import date
        from io import BytesIO
        with patch("urllib.request.urlopen", return_value=BytesIO(
            json.dumps({"data": {"id": "x"}, "included": []}).encode()
        )) as mocked:
            response = fetch_public_commodity("0101210000", date(2026, 10, 13))
            self.assertIn("data", response)
            request = mocked.call_args.args[0]
            self.assertIn("/xi/api/commodities/0101210000?", request.full_url)

    def test_bad_code_rejected_before_network(self):
        from datetime import date
        with self.assertRaises(ValueError):
            fetch_commodity("../x", date.today(), "a", "b")
        with self.assertRaises(ValueError):
            fetch_public_commodity("../x", date.today())


if __name__ == "__main__":
    unittest.main()
