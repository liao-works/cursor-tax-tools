"""Data from accepted snapshots must never invent ambiguous or stale rates."""
import unittest
from datetime import date
from src.target_duty.desktop_release_bundle import simple_rate, gb_default_rate, xi_rate

class DesktopReleaseBundleTests(unittest.TestCase):
    def test_production_release_job_only_runs_after_exact_diff_and_has_write_permission(self):
        from pathlib import Path
        wf=(Path(__file__).resolve().parents[1]/".github"/"workflows"/"gb-ni-exact-full-source-diff.yml").read_text()
        prefix,publisher=wf.split("  publish-desktop-data:",1)
        self.assertNotIn("contents: write",prefix)
        self.assertIn("contents: write",publisher)
        self.assertIn("github.event_name == 'workflow_run'",publisher)
        self.assertIn("needs: exact-code-diff",publisher)
        self.assertIn("gh release create",publisher)
        self.assertIn("gh release upload latest-data",publisher)
        self.assertIn("metadata.json",publisher)
        self.assertIn("full_source_delta_scope",prefix)
        xi=(Path(__file__).resolve().parents[1]/".github"/"workflows"/"xi-public-full-measures.yml").read_text()
        self.assertIn("cron: '0 2 * * *'",xi)
        self.assertIn("max-parallel: 8",xi)

    def test_gb_unique_simple_rate(self):
        self.assertEqual(simple_rate([("6%","","","")]),"6%")
        self.assertEqual(simple_rate([("0.00 %","","","")]),"0.00 %")
        self.assertEqual(simple_rate([]),"")
        self.assertEqual(simple_rate([("6%","","",""),("12%","","","")]),"")
        self.assertEqual(simple_rate([("6%","","certificate","")]),"")
        self.assertEqual(simple_rate([("6%","1234","","")]),"")
        self.assertEqual(simple_rate([("6%","","","GB")]),"")
        self.assertEqual(simple_rate([("£12/100kg","","","")]),"£12/100kg")

    def test_gb_null_sentinels_and_specific_additional_codes(self):
        self.assertEqual(gb_default_rate([("6.00 %","#NA","N/A","None")]),"6.00 %")
        self.assertEqual(gb_default_rate([("0%","null","[]","")]),"0%")
        self.assertEqual(gb_default_rate([
            ("6%","#NA","#NA","#NA"),
            ("2%","1234","#NA","#NA"),
        ]),"6%")
        self.assertEqual(gb_default_rate([
            ("6%","#NA","#NA","#NA"),
            ("8%","N/A","[]","None"),
        ]),"")
        self.assertEqual(gb_default_rate([("6%","1234","","")]),"")
        self.assertEqual(gb_default_rate([("6%","","certificate","")]),"")
        self.assertEqual(gb_default_rate([("EUR 12/100kg","","","")]),"EUR 12/100kg")

    def test_xi_stale_conditional_specific_and_compound_never_auto_populate(self):
        day=date(2026,10,16)
        def row(rate="5%",typ="103",geo="1011",start="2026-01-01",
                end=None,rels=None):
            import json
            return (typ,geo,rate,start,end,json.dumps({"relationships":rels or {}}))
        self.assertEqual(xi_rate([row()],day),"5%")
        self.assertEqual(xi_rate([row(typ="105")],day),"")
        self.assertEqual(xi_rate([row(start="2027-01-01")],day),"")
        self.assertEqual(xi_rate([row(end="2026-10-15")],day),"")
        self.assertEqual(xi_rate([row(geo="GB")],day),"")
        self.assertEqual(xi_rate([row(rate="10% + EUR 8 / 100 kg")],day),"10% + EUR 8 / 100 kg")
        self.assertEqual(xi_rate([row(rels={
            "measure_conditions":{"data":[{"id":"1","type":"measure_condition"}]}})],day),"")
        self.assertEqual(xi_rate([
            row(rate="6%",typ="103"),
            row(rate="0%",typ="103",rels={
                "measure_conditions":{"data":[{"id":"1","type":"measure_condition"}]}})
        ],day),"6%")
        self.assertEqual(xi_rate([row(),row(rate="8%",typ="105")],day),"5%")

    def test_normal_china_third_country_rate_never_takes_authorised_use_zero(self):
        from pathlib import Path
        import json
        day=date(2026,10,16)
        ordinary=("103","1011","10%","2026-01-01",None,
                  json.dumps({"relationships":{}}))
        authorised=("105","1011","0%","2026-01-01",None,
                    json.dumps({"relationships":{
                        "measure_conditions":{"data":[{"type":"measure_condition","id":"N990"}]}}}))
        channel_islands=("103","1080","0%","2026-01-01",None,
                         json.dumps({"relationships":{}}))
        self.assertEqual(xi_rate([ordinary,authorised,channel_islands],day),"10%")
        self.assertEqual(xi_rate([authorised,channel_islands],day),"")
        self.assertEqual(xi_rate([("105","1011","0%","2026-01-01",None,
                                  json.dumps({"relationships":{}}))],day),"")
        # 103 is only the broad Chinese/general third-country rate here.
        # 105/authorised use may be displayed separately later.
        source=(Path(__file__).resolve().parents[1]/
                "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        self.assertIn("measure_type = '103'",source)
        self.assertIn('if kind != "103" or geo!="1011"',source)
        self.assertIn("measure_type IN ('103','105')",source)

    def test_hmrc_official_linked_duty_expression_is_replayed(self):
        import json
        from datetime import date
        measure={
          "relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"rate-1"}},
            "measure_conditions":{"data":[]},
            "additional_code":{"data":None},
          }
        }
        raw_doc={"included":[
            {"type":"duty_expression","id":"rate-1","attributes":{"base":"7.20 %"}}
        ]}
        row=("103","1011","", "2026-01-01",None,json.dumps(measure))
        self.assertEqual(xi_rate([row],date(2026,10,16),
                                 json.dumps(raw_doc).encode()),"7.20 %")
        self.assertEqual(xi_rate([row],date(2026,10,16)),"")
        measure["relationships"]["measure_conditions"]["data"]=[
            {"type":"measure_condition","id":"condition-1"}]
        restricted=("103","1011","","2026-01-01",None,json.dumps(measure))
        self.assertEqual(xi_rate([restricted],date(2026,10,16),
                                 json.dumps(raw_doc).encode()),"")

    def test_invalid_source_dates_cannot_be_published(self):
        import json
        self.assertEqual(xi_rate([("103","1011","6%",None,None,
                                   json.dumps({"relationships":{}}))],
                                 date(2026,10,16)),"")


if __name__=="__main__":
    unittest.main()
