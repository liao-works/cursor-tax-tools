"""Fail-closed source-only XI applicability audit regression."""
import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.xi_applicability_review import analyze, sha

DAY=date(2026,10,14)
TODAY=date(2026,10,15)


def measure(code, mid, typ="103", geo="1011", expression="3.00 %", **overrides):
    row=dict(code=code,measure_id=str(mid),measure_type=typ,geography_id=geo,
             source_origin="eu",effective_start="2020-01-01",effective_end="",
             source_expression_base=expression,source_expression_verbose=expression,
             stored_inline_expression="",candidate_expression=expression,
             measure_component_count="1",resolved_component_count="0",
             condition_count="0",condition_group_count="0",excluded_country_count="0",
             review_status="simple_percent_candidate_unverified",
             ni_other_payable_rate="",production_eligible="false")
    row.update(overrides)
    return row


class XIApplicabilityReviewTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.candidate=self.root/"candidate.csv"
        self.projection=self.root/"projection.json"
        self.gaps=self.root/"gaps.csv"
        self.source=self.root/"source.json"
        self.out=self.root/"review.csv"
        self.outreport=self.root/"review.json"
        self.codes=[f"{i:02d}01000000" for i in range(1,8)]
        c=self.codes
        self.rows=[
            measure(c[0],"1"),
            measure(c[1],"2"),measure(c[1],"3",typ="105"),
            measure(c[3],"4",expression="5.00 % + 20 EUR / 100 kg",
                    condition_count="2",effective_end="2026-10-15"),
            measure(c[4],"5"),measure(c[4],"6"),
            measure(c[5],"7",geo="RU",expression="50.00 %"),
            measure(c[6],"8",typ="105")]
        self.write_proofs()

    def write_csv(self,path,rows):
        with path.open("w",encoding="utf-8",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_proofs(self):
        self.write_csv(self.candidate,self.rows)
        self.projection.write_text(json.dumps({
            "kind":"xi-public-third-country-source-projection-v1",
            "as_of":DAY.isoformat(),"catalogue_sha256":"a"*64,
            "expected_codes":len(self.codes),"candidate_measures":len(self.rows),
            "candidate_codes":len({r["code"] for r in self.rows}),
            "raw_source_complete":True,"source_failures":0,"missing_responses":0,
            "source_csv_sha256":sha(str(self.candidate)),
            "production_eligible":False,"ni_other_verified":False}))
        counts={code:sum(r["code"]==code for r in self.rows) for code in self.codes}
        self.write_csv(self.gaps,[{
            "commodity_code":code,"chapter":code[:2],
            "source_response_status":"ok","source_response_sha256":"b"*64,
            "source_error":"","raw_import_measure_count":"10",
            "third_country_103_105_candidates":str(counts[code]),
            "review_state":"candidate_requires_origin_and_conditions_review"
        } for code in self.codes])
        self.source.write_text(json.dumps({
            "kind":"xi-public-full-independent-gap-ledger-v1",
            "as_of":DAY.isoformat(),"catalogue_sha256":"a"*64,
            "expected_commodity_codes":len(self.codes),
            "public_source_complete":True,"source_errors":0,"missing_responses":0,
            "production_eligible":False,"ni_other_verified":False,
            "all_codes_csv_sha256":sha(str(self.gaps))}))

    def audit(self,review_date=TODAY):
        return analyze(str(self.candidate),str(self.projection),
                       str(self.gaps),str(self.source),
                       str(self.out),str(self.outreport),reviewed_on=review_date)

    def test_real_103_105_multiple_geo_conditional_compound_and_expiry(self):
        r=self.audit()
        self.assertEqual(r["official_source_codes"],7)
        self.assertEqual(r["source_import_measure_rows"],70)
        self.assertEqual(r["third_country_candidate_measures"],8)
        self.assertEqual(r["codes_with_candidates"],6)
        self.assertEqual(r["codes_without_candidates"],1)
        self.assertEqual(r["measures_ending_between_snapshot_and_review"],1)
        self.assertFalse(r["source_snapshot_current_as_of_review_date"])
        self.assertEqual(r["certified_payable_rates"],0)
        self.assertFalse(r["ni_other_verified"])
        with self.out.open(newline="") as f:
            rows={r["code"]:r for r in csv.DictReader(f)}
        c=self.codes
        self.assertEqual(rows[c[0]]["source_review_priority"],"simple_source_candidate_origin_review_only")
        self.assertEqual(rows[c[1]]["source_review_priority"],"type_105_competes")
        self.assertIn("no_103_105_measure",rows[c[2]]["all_review_reasons"])
        self.assertIn("certificates_and_measure_conditions",rows[c[3]]["all_review_reasons"])
        self.assertIn("compound_or_specific_expression",rows[c[3]]["all_review_reasons"])
        self.assertIn("measure_ends_by_review_date",rows[c[3]]["all_review_reasons"])
        self.assertEqual(rows[c[4]]["source_review_priority"],"multiple_generic_103_baselines")
        self.assertIn("specific_country_territory_measures",rows[c[5]]["all_review_reasons"])
        self.assertIn("no_generic_103_baseline",rows[c[6]]["all_review_reasons"])
        for row in rows.values():
            self.assertEqual(row["ni_other_payable_rate"],"")
            self.assertEqual(row["production_eligible"],"false")
            self.assertEqual(row["country_of_origin_specified"],"false")

    def test_source_csv_sha_mutation_fails(self):
        self.candidate.write_bytes(self.candidate.read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            self.audit()

    def test_coverage_csv_sha_mutation_fails(self):
        self.gaps.write_bytes(self.gaps.read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            self.audit()

    def test_illegal_production_rate_is_rejected(self):
        self.rows[0]["ni_other_payable_rate"]="0%"
        self.write_proofs()
        with self.assertRaisesRegex(ValueError,"illegally labelled"):
            self.audit()

    def test_count_mismatch_fails_even_when_sha_is_replaced(self):
        with self.gaps.open(newline="") as f:
            rows=list(csv.DictReader(f))
        rows[0]["third_country_103_105_candidates"]="0"
        self.write_csv(self.gaps,rows)
        obj=json.loads(self.source.read_text())
        obj["all_codes_csv_sha256"]=sha(str(self.gaps))
        self.source.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError,"count differs"):
            self.audit()

    def test_review_cannot_precede_frozen_snapshot(self):
        with self.assertRaisesRegex(ValueError,"earlier"):
            self.audit(date(2026,10,13))

    def test_historical_ended_record_must_not_pass(self):
        self.rows[0]["effective_end"]="2026-10-13"
        self.write_proofs()
        with self.assertRaisesRegex(ValueError,"not active"):
            self.audit()

    def test_no_real_origin_or_ukims_is_always_non_actionable(self):
        r=self.audit()
        self.assertTrue(r["origin_and_trader_at_risk_status_not_supplied"])
        self.assertTrue(r["additional_code_details_not_projected"])
        self.assertFalse(r["production_eligible"])


if __name__=="__main__":
    unittest.main()
