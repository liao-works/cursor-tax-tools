import unittest
from src.target_duty.xi_probe import summarize_response, fetch_commodity


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
                     "measure_conditions": {"data": []}}},
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
        self.assertEqual(report["counts"]["non_target_measure"], 1)

    def test_missing_includes_not_claimed_as_rate(self):
        result = summarize_response({"data": {"relationships": {
            "import_measures": {"data": [{"id": "absent", "type": "measure"}]}}}},
            "0101210000")
        self.assertIsNone(result["rate"])
        self.assertEqual(result["counts"]["unresolved_import_measure"], 1)

    def test_bad_code_rejected_before_network(self):
        from datetime import date
        with self.assertRaises(ValueError):
            fetch_commodity("../x", date.today(), "a", "b")


if __name__ == "__main__":
    unittest.main()
