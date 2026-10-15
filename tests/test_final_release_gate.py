"""Operational source completeness can NEVER alone authorize tariff updates."""
import copy
import unittest

from src.target_duty.final_release_gate import decide

BASE={
    "kind":"gb-ni-2026-10-15-final-operational-readiness-audit-v1",
    "release_decision":"BLOCKED_DO_NOT_DEPLOY",
    "draft_pr_must_remain_draft":True,
    "dimensions":{
        "data_source_completeness":{
            "status":"PASS_SOURCE_ONLY_NOT_PAYABLE",
            "certified_payable_NI_other_rates":0,
            "gb_without_direct_candidates":366,
            "ni_codes_without_candidates":98,
            "ni_unknown_additional_code_relations":16970},
        "collection_timeliness":{
            "status":"BLOCKED_NO_FULL_CURRENT_DAY_REFRESH_AND_OLD_LATEST_DATA"},
        "data_acquisition_stability":{
            "status":"PARTIALLY_PROVEN_NO_REPEATED_SUCCESS_SLA"},
        "update_installation_stability":{
            "status":"BLOCKED_UNSAFE_PUBLISH_AND_CRASH_RECOVERY_UNVERIFIED"},
    }
}

class OperationalGateTests(unittest.TestCase):
    def test_actual_source_only_complete_remains_release_blocked(self):
        r=decide(BASE)
        self.assertFalse(r["release_permitted"])
        self.assertGreaterEqual(len(r["blockers"]),8)
        self.assertEqual(r["readiness"],"BLOCKED")

    def test_missing_dimension_fails_closed(self):
        q=copy.deepcopy(BASE)
        del q["dimensions"]["update_installation_stability"]
        r=decide(q)
        self.assertIn("update_installation_stability: missing evidence",r["blockers"])
        self.assertFalse(r["release_permitted"])

    def test_incorrect_source_or_stale_version_never_approved(self):
        for column in ("data_source_completeness","collection_timeliness"):
            with self.subTest(column=column):
                q=copy.deepcopy(BASE)
                q["dimensions"][column]["status"]="FAIL_SOURCE_DATA"
                self.assertFalse(decide(q)["release_permitted"])

    def test_even_all_green_and_manual_approval_do_not_trigger_publishing(self):
        q=copy.deepcopy(BASE)
        q["release_decision"]="PASS_AFTER_EXPLICIT_OPERATOR_APPROVAL"
        q["draft_pr_must_remain_draft"]=False
        for col in q["dimensions"]:
            q["dimensions"][col]["status"]={
                "data_source_completeness":"PASS_SOURCE_ONLY_NOT_PAYABLE",
                "collection_timeliness":"PASS_CURRENT_FULL_SNAPSHOT",
                "data_acquisition_stability":"PASS_REPEATED_RECOVERABLE",
                "update_installation_stability":"PASS_ATOMIC_ROLLBACK_AND_RELEASE",
            }[col]
        q["dimensions"]["data_source_completeness"].update({
            "certified_payable_NI_other_rates":16728,
            "gb_without_direct_candidates":0,
            "ni_codes_without_candidates":0,
            "ni_unknown_additional_code_relations":0,
        })
        result=decide(q)
        self.assertFalse(result["release_permitted"])
        self.assertEqual(result["readiness"],"STILL_REQUIRES_SEPARATE_SIGNED_OPERATOR_APPROVAL")

    def test_tampered_kind_rejected(self):
        q=copy.deepcopy(BASE)
        q["kind"]="production-release-permitted"
        with self.assertRaisesRegex(ValueError,"Invalid"):
            decide(q)

    def test_no_unverified_default_zero(self):
        for field in ("gb_without_direct_candidates","ni_codes_without_candidates",
                      "ni_unknown_additional_code_relations"):
            q=copy.deepcopy(BASE)
            q["dimensions"]["data_source_completeness"][field]=None
            with self.subTest(field=field):
                self.assertFalse(decide(q)["release_permitted"])

if __name__=="__main__":
    unittest.main()
