"""Data from accepted snapshots must never invent ambiguous or stale rates."""
import unittest
from datetime import date
from src.target_duty.desktop_release_bundle import simple_rate, gb_default_rate, gb_china_rate, _gb_rate_options, xi_rate, _default_rate_gap

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

    def test_every_blank_default_rate_gets_a_reasoned_gap_record(self):
        base = {
            "measure_type": "103", "china_applicable": True,
            "effective_status": "active", "conditional": False,
            "expression_available": True, "expression": "8%",
            "geography": "CN", "source_id": "measure-1",
        }
        self.assertIsNone(_default_rate_gap("GB", "0101210000", "8%", [base], [base]))
        gap = _default_rate_gap("GB", "0101210000", "", [], [])
        self.assertEqual(gap["reason"], "no_103_source_rows")
        conditional = dict(base, conditional=True, expression="3%")
        gap = _default_rate_gap("GB", "0101210000", "", [conditional], [conditional])
        self.assertEqual(gap["reason"], "active_103_requires_conditions_or_additional_code")
        missing = dict(base, expression="", expression_available=False)
        gap = _default_rate_gap("XI", "0101210000", "", [missing], [missing])
        self.assertEqual(gap["reason"], "selected_103_measure_missing_expression")
        conflict = [base, dict(base, source_id="measure-2", expression="9%")]
        gap = _default_rate_gap("GB", "0101210000", "", conflict, conflict)
        self.assertEqual(gap["reason"], "conflicting_unconditional_103_rates_at_selected_scope")
        self.assertEqual(gap["code"], "0101210000")
        self.assertEqual(gap["active_unconditional_103_option_count"], 2)
        multiple=dict(base, expression="", expression_available=True,
                      expression_resolution="multiple", expression_count=2,
                      expression_options=[{"expression":"7%"},{"expression":"12 GBP/100 kg"}])
        gap=_default_rate_gap("XI","0101210000","",[multiple],[multiple])
        self.assertEqual(gap["reason"],"selected_103_measure_has_multiple_duty_expressions")
        self.assertEqual(gap["options"][0]["expression_options"],multiple["expression_options"])
        unresolved=dict(base,expression_resolution="unresolved_link")
        gap=_default_rate_gap("XI","0101210000","",[unresolved],[unresolved])
        self.assertEqual(gap["reason"],"selected_103_measure_has_unresolved_duty_expression")

    def test_metadata_accounts_for_active_conditional_rates_and_expressions(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/
                "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        # Metadata must make conditional source coverage measurable by region,
        # status, measure type, expression presence and China applicability.
        for key in (
            '"active_conditional_options"',
            '"active_conditional_with_expression"',
            '"active_conditional_without_expression"',
            '"active_china_conditional_options"',
            '"rate_option_audit_counts"',
            '"conditional_rate_policy"',
            '":type:"',
            '":conditional:"',
            '":expression:"',
            '":china_applicable:"',
            '":applicability:"',
        ):
            self.assertIn(key, source)
        self.assertIn('gb_rate_source_rows"] != counts["gb_rate_projected_rows"]', source)
        self.assertIn('xi_rate_source_rows"] != counts["xi_rate_projected_rows"]', source)

    def test_release_builder_replays_same_version_geo_reference_tables(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/
                "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        workflow=(Path(__file__).resolve().parents[1]/".github"/"workflows"/
                  "gb-ni-exact-full-source-diff.yml").read_text()
        self.assertIn('area_payload=geo_areas_payload',source)
        self.assertIn('descriptions_payload=geo_area_descriptions_payload',source)
        self.assertIn('"dbt-official-geo-area-memberships-v2"',source)
        self.assertIn('geo_meta.get("geo_areas_source_sha256")',source)
        self.assertIn('geo_meta.get("geo_area_descriptions_source_sha256")',source)
        self.assertIn('--geo-areas gb/gb-weekly-geo-areas.csv',workflow)
        self.assertIn('--geo-area-descriptions gb/gb-weekly-geo-area-descriptions.csv',workflow)

    def test_metadata_separates_default_duty_and_source_option_coverage(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/
                "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        self.assertIn('"default_duty_coverage"',source)
        self.assertIn('"rate_option_coverage"',source)
        self.assertIn('"codes_without_default_duty"',source)
        self.assertIn('"missing_expression_options"',source)
        self.assertIn('"projection_complete"',source)
        self.assertIn("Includes percentage, specific and compound duties",source)
        self.assertIn("Verified default-duty coverage",source)
        self.assertIn('"trade_remedy_coverage"',source)
        self.assertIn('"condition_rate_components_collected":False',source)
        self.assertIn('"condition_rate_links_preserved":True',source)
        self.assertIn('"total_default_duty_entries"',source)
        self.assertIn('"gb_available_default_duty_codes"',source)
        self.assertIn('"xi_available_default_duty_codes"',source)
        self.assertIn("expected_gap_count",source)
        self.assertIn('"audit":',source)

    def test_gb_channel_islands_rate_does_not_replace_china_default_and_authorised_use_is_retained(self):
        import json
        from datetime import date
        # The only active type-103 rate is specific to the Channel Islands,
        # so it must not become a China/third-country default.
        channel_islands=[("0%","#NA","#NA","#NA","1080")]
        self.assertEqual(gb_china_rate(channel_islands,set()),"")
        authorised_use=("105","1011","0%","#NA","#NA",
                        "condition:B,certificate:N990,action:27|condition:B,action:08",
                        "#NA","2021-01-01","#NA",
                        {"measure__type__description":"Non preferential duty under authorised use",
                         "measure__sid":"20000001"})
        option=_gb_rate_options([authorised_use],date(2026,10,17),set())[0]
        self.assertEqual(option["expression"],"0%")
        self.assertTrue(option["china_applicable"])
        self.assertTrue(option["conditional"])
        self.assertEqual(option["applicability"],"authorised_use")

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
        self.assertIn("measure_type='103'",source)
        self.assertIn('if kind != "103" or not _china_applicable(geo, "", groups)',source)
        self.assertIn("xi_geographical_areas_report_path",source)
        self.assertIn("measure_type NOT IN ('551','552','553','554')",source)
        self.assertIn("def _gb_rate_option_rows",source)
        self.assertIn("def _xi_rate_option_rows",source)

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
