"""XI original JSON:API duty_expression relationship projection, fail closed."""
import json
import unittest
from datetime import date

from src.target_duty.xi_duty_projection import project

DAY=date(2026,10,14)
CODE="0101299000"


def original(*,base="11.50 %",verbose="11.50%",typ="103",geo="1011",
             conditions=False, excluded=False, expression=True,
             start="2012-01-01T00:00:00.000Z", end=None,
             missing_reference=False, inline=""):
    relations={
        "measure_type":{"data":{"type":"measure_type","id":typ}},
        "geographical_area":{"data":{"type":"geographical_area","id":geo}},
        "duty_expression":{"data":{"type":"duty_expression","id":"duty1"}},
        "measure_components":{"data":[{"type":"measure_component","id":"comp1"}]},
        "resolved_measure_components":{"data":[]},
        "measure_conditions":{"data":[{"type":"measure_condition","id":"cond1"}] if conditions else []},
        "measure_condition_permutation_groups":{"data":[]},
        "excluded_countries":{"data":[{"type":"geographical_area","id":"EX"}] if excluded else []}}
    if not expression:
        relations["duty_expression"]={"data":[]}
    measure={"type":"measure","id":"m1",
             "attributes":{"effective_start_date":start,"effective_end_date":end,
                           "resolved_duty_expression":inline,"origin":"eu"},
             "relationships":relations}
    incl=[
        measure,
        {"type":"measure_type","id":typ,"attributes":{}},
        {"type":"geographical_area","id":geo,"attributes":{}},
        {"type":"measure_component","id":"comp1","attributes":{"duty_amount":11.5}},
    ]
    if expression and not missing_reference:
        incl.append({"type":"duty_expression","id":"duty1",
                     "attributes":{"base":base,"verbose_duty":verbose}})
    if conditions:
        incl.append({"type":"measure_condition","id":"cond1",
                     "attributes":{"condition_code":"B","document_code":"C074"}})
    if excluded:
        incl.append({"type":"geographical_area","id":"EX","attributes":{}})
    return json.dumps({
        "data":{"type":"commodity","id":CODE,
                "attributes":{"goods_nomenclature_item_id":CODE},
                "relationships":{"import_measures":{"data":[{"type":"measure","id":"m1"}]}}},
        "included":incl}).encode("utf-8")


class NIExpressionProjectionTest(unittest.TestCase):
    def test_official_reference_base_recovers_missing_inline(self):
        result=project(original(),CODE,DAY)
        self.assertEqual(len(result),1)
        row=result[0]
        self.assertEqual(row["candidate_expression"],"11.50 %")
        self.assertEqual(row["stored_inline_expression"],"")
        self.assertEqual(row["review_status"],"simple_percent_candidate_unverified")
        self.assertEqual(row["ni_other_payable_rate"],"")
        self.assertEqual(row["production_eligible"],"false")
        self.assertEqual(row["source_origin"],"eu")

    def test_real_zero_percent_remains_unverified_candidate(self):
        row=project(original(base="0.00 %",verbose="0.00%"),CODE,DAY)[0]
        self.assertEqual(row["candidate_expression"],"0.00 %")
        self.assertEqual(row["ni_other_payable_rate"],"")

    def test_expression_fallback_and_compound_status(self):
        row=project(original(base="",verbose="6.50%"),CODE,DAY)[0]
        self.assertEqual(row["candidate_expression"],"6.50%")
        row=project(original(base="5% + 10 EUR / 100 kg"),CODE,DAY)[0]
        self.assertEqual(row["review_status"],"compound_or_specific_candidate_review")
        row=project(original(base="",verbose="",inline=""),CODE,DAY)[0]
        self.assertEqual(row["review_status"],"missing_expression_review")

    def test_conditions_and_exclusions_block_simple_rate(self):
        for kwargs in ({"conditions":True},{"excluded":True}):
            row=project(original(**kwargs),CODE,DAY)[0]
            self.assertEqual(row["review_status"],"conditional_candidate_review")
            self.assertEqual(row["ni_other_payable_rate"],"")

    def test_105_or_special_geography_never_auto_becomes_other(self):
        row=project(original(typ="105",geo="RU"),CODE,DAY)[0]
        self.assertEqual(row["review_status"],"specific_geography_review")
        self.assertEqual(row["measure_type"],"105")

    def test_unresolved_expression_relation_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"Missing related duty_expression"):
            project(original(missing_reference=True),CODE,DAY)

    def test_expired_future_and_malformed_source_date_fail_closed(self):
        for kwargs in (
            {"end":"2026-10-13T00:00:00.000Z"},
            {"start":"2026-10-15T00:00:00.000Z"},
            {"start":"wrong-date"}):
            with self.assertRaisesRegex(ValueError,"(Non-active|Malformed)"):
                project(original(**kwargs),CODE,DAY)

    def test_non_third_country_measures_not_returned(self):
        self.assertEqual(project(original(typ="142"),CODE,DAY),[])


if __name__=="__main__":
    unittest.main()
