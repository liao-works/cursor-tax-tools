"""NI official JSON:API relationship evidence and fail-closed scenario tests."""
import json
import unittest
from datetime import date

from src.target_duty.xi_condition_evidence import details, scenario

DAY=date(2026,10,14)
CODE="0713101000"


def payload(*, no_additional_rel=True, excluded=True, unresolved_nested=False,
            type_id="103", with_condition=True):
    measure={"type":"measure","id":"4071346",
             "attributes":{"effective_start_date":"2024-07-01T00:00:00.000Z",
                           "effective_end_date":None,"resolved_duty_expression":"",
                           "origin":"eu"},
             "relationships":{
                 "measure_type":{"data":{"type":"measure_type","id":type_id}},
                 "geographical_area":{"data":{"type":"geographical_area","id":"1011"}},
                 "duty_expression":{"data":{"type":"duty_expression","id":"de1"}},
                 "measure_components":{"data":[]},
                 "resolved_measure_components":{"data":[]},
                 "measure_conditions":{"data":[{"type":"measure_condition","id":"co1"}] if with_condition else []},
                 "measure_condition_permutation_groups":{"data":[{"type":"measure_condition_permutation_group","id":"pg1"}] if with_condition else []},
                 "excluded_countries":{"data":[{"type":"geographical_area","id":"RU"}] if excluded else []},
                 "legal_acts":{"data":[{"type":"legal_act","id":"law1"}]},
                 "footnotes":{"data":[{"type":"footnote","id":"ft1"}]}}}
    included=[
      measure,
      {"type":"measure_type","id":type_id,"attributes":{}},
      {"type":"geographical_area","id":"1011","attributes":{
          "geographical_area_id":"1011","description":"ERGA OMNES"},
       "relationships":{"children_geographical_areas":{"data":[
           {"type":"geographical_area","id":"BY"},
           {"type":"geographical_area","id":"RU"}]}}},
      {"type":"duty_expression","id":"de1","attributes":{"base":"11.50 %"}},
      {"type":"legal_act","id":"law1","attributes":{"regulation_code":"R1652/24",
                                                      "regulation_url":"https://eur-lex.europa.eu"}},
      {"type":"footnote","id":"ft1","attributes":{"description":"Russian federation special customs terms"}},
    ]
    if excluded:
        included.append({"type":"geographical_area","id":"RU",
                         "attributes":{"geographical_area_id":"RU","description":"Russia"}})
    if with_condition:
        included.extend([
          {"type":"measure_condition","id":"co1",
           "attributes":{"document_code":"Y155","condition_code":"B",
                         "action_code":"01","certificate_description":"Exports from Russia"}},
          {"type":"measure_condition_permutation_group","id":"pg1",
           "attributes":{"condition_code":"B"},
           "relationships":{"permutations":{"data":[{"type":"measure_condition_permutation","id":"per1"}]}}},
        ])
        if not unresolved_nested:
            included.append({"type":"measure_condition_permutation","id":"per1",
                             "relationships":{"measure_conditions":{"data":[
                                 {"type":"measure_condition","id":"co1"}]}}})
    if not no_additional_rel:
        measure["relationships"]["additional_code"]={"data":[]}
    doc={"data":{"type":"commodity","id":CODE,
                 "attributes":{"goods_nomenclature_item_id":CODE},
                 "relationships":{"import_measures":{"data":[
                     {"type":"measure","id":"4071346"}]}}},
         "included":included}
    return json.dumps(doc).encode()


class OfficialRawDetailsTest(unittest.TestCase):
    def test_condition_certificate_permutation_legal_and_geo_preserved(self):
        raw=payload()
        records=details(raw,CODE,DAY)
        self.assertEqual(len(records),1)
        r=records[0]
        self.assertEqual(r["candidate_expression"],"11.50 %")
        self.assertEqual(r["additional_code_state"],"not_exposed")
        self.assertEqual(json.loads(r["condition_evidence_json"])[0]["attributes"]["document_code"],"Y155")
        self.assertEqual(json.loads(r["condition_permutations_json"])[0]["permutations"][0]
                         ["measure_condition_refs"][0]["id"],"co1")
        self.assertEqual(json.loads(r["excluded_geographies_json"])[0]["id"],"RU")
        self.assertEqual(json.loads(r["legal_acts_json"])[0]["attributes"]["regulation_code"],"R1652/24")
        self.assertTrue(len(r["original_response_sha256"])==64)
        self.assertEqual(r["ni_other_payable_rate"],"")
        self.assertEqual(r["production_eligible"],"false")

    def test_unknown_additional_code_distinct_from_confirmed_none(self):
        row=details(payload(),CODE,DAY)[0]
        row2=details(payload(no_additional_rel=False),CODE,DAY)[0]
        self.assertEqual(row["additional_code_state"],"not_exposed")
        self.assertEqual(row2["additional_code_state"],"source_explicit_none")

    def test_unresolved_nested_permutation_is_visible_review_gap(self):
        row=details(payload(unresolved_nested=True),CODE,DAY)[0]
        missing=json.loads(row["missing_nested_references_json"])
        self.assertEqual(missing,["permutations:measure_condition_permutation:per1"])
        result=scenario(row,origin="BY",movement="GB_TO_NI",
                        at_risk=True,ukims_authorised=False)
        self.assertIn("unresolved_official_nested_references",result["reason_codes"])
        self.assertFalse(result["production_eligible"])

    def test_country_exclusion_rejects_only_that_measure_never_sets_rate(self):
        row=details(payload(),CODE,DAY)[0]
        ru=scenario(row,origin="RU",movement="OTHER_TO_NI",
                    at_risk=True,ukims_authorised=None)
        self.assertTrue(ru["excluded_from_this_measures_geography"])
        self.assertEqual(ru["decision"],"excluded_measure_not_a_rate")
        self.assertIsNone(ru["payable_rate"])
        by=scenario(row,origin="BY",movement="OTHER_TO_NI",
                    at_risk=True,ukims_authorised=None)
        self.assertFalse(by["excluded_from_this_measures_geography"])
        self.assertEqual(by["decision"],"needs_legal_and_trader_context")

    def test_missing_trader_and_origin_context_never_infers_zero(self):
        row=details(payload(),CODE,DAY)[0]
        a=scenario(row,origin=None,movement=None,at_risk=None,ukims_authorised=None)
        self.assertIn("origin_country_required",a["reason_codes"])
        self.assertIn("at_risk_status_required",a["reason_codes"])
        self.assertIn("ukims_authorisation_unknown",a["reason_codes"])
        self.assertIsNone(a["payable_rate"])
        with self.assertRaises(ValueError):
            scenario(row,origin="OTHER",movement="GB_TO_NI",
                     at_risk=False,ukims_authorised=True)

    def test_conditions_even_on_simple_percent_still_not_payable(self):
        record=details(payload(excluded=False),CODE,DAY)[0]
        self.assertEqual(record["candidate_expression"],"11.50 %")
        res=scenario(record,origin="FR",movement="GB_TO_NI",at_risk=False,
                     ukims_authorised=True)
        self.assertIn("measure_conditions_require_document_verification",res["reason_codes"])
        self.assertIn("duty_route_and_preference_legally_unverified",res["reason_codes"])
        self.assertEqual(res["decision"],"needs_legal_and_trader_context")
        self.assertIsNone(res["payable_rate"])

    def test_invalid_raw_measure_link_fails_and_non_targets_skipped(self):
        doc=json.loads(payload())
        doc["included"]=[x for x in doc["included"] if x.get("id")!="4071346"]
        with self.assertRaises(ValueError):
            details(json.dumps(doc).encode(),CODE,DAY)
        self.assertEqual(details(payload(type_id="142"),CODE,DAY),[])


if __name__=="__main__":
    unittest.main()
