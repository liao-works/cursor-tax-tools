"""Live XI date change canary compares legal candidate sets, never pays duty."""
import json
import unittest
from datetime import date
from src.target_duty.xi_live_date_canary import compare

CODE="0101299000"
A=date(2026,10,14)
B=date(2026,10,15)


def payload(code=CODE, percent="11.50 %", typ="103", included=True):
    rel=[{"type":"measure","id":"m1"}] if included else []
    data={"data":{"type":"commodity","id":code,
                  "attributes":{"goods_nomenclature_item_id":code},
                  "relationships":{"import_measures":{"data":rel}}},
          "included":[]}
    if included:
        data["included"]=[
            {"type":"measure","id":"m1",
             "attributes":{"effective_start_date":"2020-01-01",
                           "effective_end_date":None},
             "relationships":{
                 "measure_type":{"data":{"type":"measure_type","id":typ}},
                 "geographical_area":{"data":{"type":"geographical_area","id":"1011"}},
                 "duty_expression":{"data":{"type":"duty_expression","id":"e1"}},
                 "measure_conditions":{"data":[]},"excluded_countries":{"data":[]},
                 "measure_components":{"data":[]},"resolved_measure_components":{"data":[]},
                 "measure_condition_permutation_groups":{"data":[]}}},
            {"type":"measure_type","id":typ,"attributes":{}},
            {"type":"geographical_area","id":"1011","attributes":{}},
            {"type":"duty_expression","id":"e1",
             "attributes":{"base":percent,"verbose_duty":percent}}]
    return json.dumps(data).encode()


class PublicDateCanaryTests(unittest.TestCase):
    def test_candidate_rate_change_detected_but_never_payable(self):
        r=compare(payload(),payload(percent="12.00 %"),CODE,earlier=A,newer=B)
        self.assertTrue(r["candidate_measures_changed"])
        self.assertEqual(r["changed_measure_ids"],["m1"])
        self.assertFalse(r["duty_rate_certified"])
        self.assertFalse(r["full_xi_freshness_verified"])

    def test_unchanged_semantic_candidate(self):
        r=compare(payload(),payload(),CODE,earlier=A,newer=B)
        self.assertFalse(r["candidate_measures_changed"])
        self.assertEqual(r["candidate_count_after"],1)

    def test_empty_source_not_zero_duty(self):
        r=compare(payload(),payload(included=False),CODE,earlier=A,newer=B)
        self.assertEqual(r["removed_measure_ids"],["m1"])
        self.assertFalse(r["duty_rate_certified"])

    def test_invalid_request_and_backdated_date_rejected(self):
        with self.assertRaisesRegex(ValueError,"backdate"):
            compare(payload(),payload(),CODE,earlier=B,newer=A)
        with self.assertRaises(ValueError):
            compare(payload(),payload(code="0101299999"),CODE,earlier=A,newer=B)


if __name__=="__main__":
    unittest.main()
