"""Chinese-origin UK/XI trade remedies keep company codes and conditions."""
import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from src.target_duty.china_trade_remedies import gb_remedies, xi_remedies, summary
from src.target_duty.desktop_release_bundle import (gb_default_rate, simple_rate, _gb_rate_options, _xi_rate_options, gb_china_rate, _china_applicable, _gb_rate_option_rows, _xi_rate_option_rows, xi_rate)

TODAY = date(2026, 10, 16)


def gb(kind="552", geo="CN", expr="42.3%", extra="A999", desc="All other exporters (residual amount)",
       conditions="", excluded="", start="2026-01-01", end="", code="7323990000"):
    return (kind,geo,expr,extra,conditions,excluded,start,end,json.dumps({
        "commodity__code":code,"measure__sid":"123",
        "measure__additional_code__description":desc,
        "measure__geographical_area__description":"China",
        "measure__footnotes":"CD946",
        "measure__regulation__id":"R0261/25",
        "measure__regulation__url":"https://www.legislation.gov.uk/eur/2025/261"}))


def xi(kind="554", geo="CN", expression="12%", start="2026-01-01", end=None, rels=None):
    return (kind,geo,expression,start,end,json.dumps({"type":"measure","id":"xyz","relationships":rels or {}}))


class ChinaTradeRemedyTests(unittest.TestCase):
    def test_gb_restricts_to_china_and_current_active_measures(self):
        records=gb_remedies([
            gb(),gb(kind="554",expr="18%",extra="",desc=""),
            gb(geo="US"),gb(geo="1011",expr="0%",excluded="CN|CA"),
            gb(end="2026-10-15"),gb(start="2027-01-01"),
            gb(kind="103")],TODAY)
        self.assertEqual(len(records),4)  # active, expired and future source rows are all retained
        self.assertEqual({r["effective_status"] for r in records},{"active","expired","future"})
        self.assertEqual(records[0]["additional_code"],"A999")
        self.assertEqual(records[0]["applicability"],"china_applicable")
        self.assertEqual(records[0]["regulation_id"],"R0261/25")
        self.assertEqual(records[0]["regulation_url"],"https://www.legislation.gov.uk/eur/2025/261")
        self.assertEqual(records[0]["footnotes"],"CD946")
        self.assertEqual(summary(records,"anti_dumping"),"42.3%（其他企业，A999）")
        self.assertEqual(summary(records,"countervailing"),"18%")

    def test_remedy_summaries_ignore_expired_future_and_unknown_rows(self):
        rows=[
            gb(kind="552",expr="42.3%",extra="A999",desc="Other exporters"),
            gb(kind="552",expr="90%",extra="A999",desc="Other exporters",end="2026-10-15"),
            gb(kind="552",expr="99%",extra="A999",desc="Other exporters",start="2027-01-01"),
            gb(kind="552",expr="77%",extra="A999",desc="Other exporters",start=""),
        ]
        measures=gb_remedies(rows,TODAY)
        self.assertEqual({x["effective_status"] for x in measures},
                         {"active","expired","future","unknown"})
        self.assertEqual(summary(measures,"anti_dumping"),"42.3%（其他企业，A999）")

    def test_competing_exporter_rates_not_mislabelled_universal(self):
        r=gb_remedies([
            gb(expr="18.1%",extra="A785",desc="Guangdong company"),
            gb(expr="42.3%",extra="A999",desc="All other overseas exporters"),
        ],TODAY)
        self.assertEqual(summary(r,"anti_dumping"),"42.3%（其他企业，A999）")
        self.assertEqual(len(r),2)  # The specialist 18.1% measure remains in detail.
        self.assertEqual(r[0]["enterprise_rates"][0]["enterprise"], "Guangdong company")
        self.assertEqual(r[0]["enterprise_rates"][0]["additional_code"], "A785")
        self.assertEqual(r[0]["enterprise_rates"][0]["expression"], "18.1%")
        self.assertEqual(r[1]["enterprise_rates"][0]["enterprise"], "其他企业（Other）")
        self.assertEqual(r[1]["enterprise_rates"][0]["expression"], "42.3%")
        r[1]["additional_description"]="Unknown producer"
        self.assertEqual(summary(r,"anti_dumping"),"按企业/条件适用（见详情）")


    def test_company_rate_tables_preserve_anti_dumping_and_countervailing_producers(self):
        records=gb_remedies([
            gb(kind="552",expr="18.1%",extra="A785",desc="Company Shenzhen Ltd"),
            gb(kind="552",expr="42.3%",extra="A999",desc="All other overseas exporters"),
            gb(kind="554",expr="6%",extra="B123",desc="Company Shenzhen Ltd"),
            gb(kind="554",expr="21%",extra="B999",desc="Other exporters"),
        ], TODAY)
        found={(r["type"],r["additional_code"]):r["enterprise_rates"][0] for r in records}
        self.assertEqual(found["anti_dumping","A785"]["enterprise"],"Company Shenzhen Ltd")
        self.assertEqual(found["anti_dumping","A785"]["expression"],"18.1%")
        self.assertEqual(found["anti_dumping","A999"]["expression"],"42.3%")
        self.assertTrue(found["anti_dumping","A999"]["other_enterprises"])
        self.assertEqual(found["countervailing","B123"]["expression"],"6%")
        self.assertEqual(found["countervailing","B999"]["expression"],"21%")
        self.assertEqual({item["selection_status"] for item in found.values()},
                         {"exact_official_expression"})

    def test_multiple_additional_codes_cannot_be_auto_paired_to_a_single_rate(self):
        from src.target_duty.china_trade_remedies import enterprise_rate_rows
        measure={
            "region":"XI","type":"anti_dumping","measure_type":"552",
            "effective_status":"active","applicability":"china_applicable",
            "expression":"13%","additional_code":"A123|B456",
            "additional_description":"Company A|Company B","conditions":"",
            "condition_rates":[]}
        rows=enterprise_rate_rows(measure)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["additional_code"],"A123|B456")
        self.assertEqual(rows[0]["selection_status"],"enterprise_or_condition_unresolved")
        self.assertFalse(rows[0]["additional_code_resolved"])

    def test_no_covered_company_or_official_expression_is_not_fabricated(self):
        records=gb_remedies([gb(expr="#NA",extra="B210",desc="Company B",conditions="D008")],TODAY)
        row=records[0]["enterprise_rates"][0]
        self.assertEqual(row["enterprise"],"Company B")
        self.assertEqual(row["expression"],"")
        self.assertEqual(row["selection_status"],"expression_unavailable")
        universal=gb_remedies([gb(expr="7%",extra="",desc="")],TODAY)[0]["enterprise_rates"][0]
        self.assertEqual(universal["enterprise"],"全部适用企业（All）")
        self.assertEqual(universal["expression"],"7%")

    def test_present_measure_without_numeric_expression_is_not_zero(self):
        r=gb_remedies([gb(kind="551",expr="#NA",extra="8A92",
                          desc="Huaxing Group",conditions="certificate:D008")],TODAY)
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]["expression"],"")
        self.assertEqual(summary(r,"anti_dumping"),"按企业/条件适用（见详情）")
        self.assertEqual(summary([], "anti_dumping"),"")

    def test_provisional_and_definitive_do_not_mix_in_summary(self):
        r=gb_remedies([
            gb(kind="551",expr="25%",extra="",desc=""),
            gb(kind="552",expr="45%",extra="",desc="")],TODAY)
        self.assertEqual(summary(r,"anti_dumping"),"45%")

    def test_ni_resolves_linked_duty_and_company_code(self):
        included={"included":[
            {"type":"duty_expression","id":"one","attributes":{"base":"13.25 %"}},
            {"type":"additional_code","id":"add-1","attributes":{
                "code":"B999","description":"All other overseas exporters (residual amount)"}},
        ]}
        rels={"duty_expression":{"data":{"type":"duty_expression","id":"one"}},
              "additional_code":{"data":{"type":"additional_code","id":"add-1"}}}
        rows=[xi(expression="",rels=rels),xi(geo="US",expression="99%")]
        out=xi_remedies(rows,TODAY,json.dumps(included).encode())
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["expression"],"13.25 %")
        self.assertEqual(out[0]["additional_code"],"B999")
        self.assertEqual(summary(out,"countervailing"),"13.25 %（其他企业，B999）")
        self.assertEqual(out[0]["enterprise_rates"][0]["enterprise"], "其他企业（Other）")
        self.assertEqual(out[0]["enterprise_rates"][0]["additional_code"], "B999")
        self.assertEqual(out[0]["enterprise_rates"][0]["expression"], "13.25 %")

    def test_xi_condition_duty_expressions_are_preserved_when_parent_expression_is_blank(self):
        doc={"included":[
            {"type":"duty_expression","id":"blank","attributes":{"base":"","formatted_base":"","verbose_duty":""}},
            {"type":"additional_code","id":"add-89ED","attributes":{"code":"89ED","description":"EcoCeres Group"}},
            {"type":"measure_condition_permutation_group","id":"group-A","attributes":{"condition_code":"A"},
             "relationships":{"measure_conditions":{"data":[
                 {"type":"measure_condition","id":"condition-10"},
                 {"type":"measure_condition","id":"condition-35"}]}}},
            {"type":"measure_condition","id":"condition-10","attributes":{
                "condition_code":"A","condition":"A: Presentation of an anti-dumping/countervailing document",
                "action_code":"01","action":"Apply the amount of the action",
                "document_code":"D008","certificate_description":"Invoice with a signed declaration",
                "duty_expression":"<span>10.00</span> %","requirement":"Invoice with signed declaration"}},
            {"type":"measure_condition","id":"condition-35","attributes":{
                "condition_code":"A","condition":"A: Presentation of an anti-dumping/countervailing document",
                "action_code":"01","action":"Apply the amount of the action",
                "duty_expression":"<span>35.60</span> %"}},
            {"type":"legal_act","id":"R2502610","attributes":{
                "regulation_code":"R0261/25","regulation_url":"https://www.legislation.gov.uk/eur/2025/261",
                "description":"Definitive anti-dumping measure"}},
            {"type":"footnote","id":"CD946","attributes":{"code":"CD946"}},
        ]}
        rels={"duty_expression":{"data":{"type":"duty_expression","id":"blank"}},
              "additional_code":{"data":{"type":"additional_code","id":"add-89ED"}},
              "measure_condition_permutation_groups":{"data":[{"type":"measure_condition_permutation_group","id":"group-A"}]},
              "measure_conditions":{"data":[{"type":"measure_condition","id":"condition-10"},
                                            {"type":"measure_condition","id":"condition-35"}]},
              "legal_acts":{"data":[{"type":"legal_act","id":"R2502610"}]},
              "footnotes":{"data":[{"type":"footnote","id":"CD946"}]},
              "geographical_area":{"data":[{"type":"geographical_area","id":"CN"}]}}
        found=xi_remedies([xi(kind="552",geo="CN",expression="",rels=rels)],TODAY,
                          json.dumps(doc).encode())
        self.assertEqual(len(found),1)
        row=found[0]
        self.assertEqual(row["expression"],"")
        self.assertEqual(row["expression_resolution"],"linked_but_empty")
        self.assertEqual([x["expression"] for x in row["condition_rates"]],["10.00 %","35.60 %"])
        self.assertEqual(row["condition_groups"][0]["condition_ids"],["condition-10","condition-35"])
        self.assertEqual(row["additional_code"],"89ED")
        self.assertEqual(row["regulation_id"],"R0261/25")
        self.assertEqual(row["footnotes"],"CD946")
        self.assertEqual([x["enterprise"] for x in row["enterprise_rates"]], ["EcoCeres Group", "EcoCeres Group"])
        self.assertEqual([x["expression"] for x in row["enterprise_rates"]], ["10.00 %", "35.60 %"])
        self.assertEqual([x["selection_status"] for x in row["enterprise_rates"]], ["requires_condition", "requires_condition"])
        self.assertEqual(row["enterprise_rates"][0]["certificate_code"], "D008")
        self.assertEqual(summary(found,"anti_dumping"),"按企业/条件适用（见详情）")

    def test_xi_unresolved_exclusion_is_preserved_but_not_counted_as_a_confirmed_rate(self):
        doc={"included":[
            {"type":"duty_expression","id":"d","attributes":{"base":"18 %"}},
        ]}
        rels={"duty_expression":{"data":{"type":"duty_expression","id":"d"}},
              "excluded_geographical_areas":{"data":[{"type":"geographical_area","id":"unknown-area"}]}}
        found=xi_remedies([xi(kind="552",geo="CN",expression="",rels=rels)],TODAY,
                          json.dumps(doc).encode())
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["applicability"],"unresolved")
        self.assertEqual(summary(found,"anti_dumping"),"")
        self.assertTrue(any(x["relationship"]=="excluded_geographical_areas"
                            for x in found[0]["unresolved_relationships"]))

    def test_xi_official_other_c999_is_an_exporter_residual_not_an_unknown_rate(self):
        # Real XI JSON:API records describe residual code C999 as simply "Other".
        doc={"included":[
            {"type":"duty_expression","id":"one","attributes":{"base":"35.60 %"}},
            {"type":"additional_code","id":"add","attributes":{
                "code":"C999","description":"Other"}},
        ]}
        links={"duty_expression":{"data":{"type":"duty_expression","id":"one"}},
               "additional_code":{"data":{"type":"additional_code","id":"add"}}}
        found=xi_remedies([xi(kind="552",expression="",rels=links)],TODAY,json.dumps(doc).encode())
        self.assertEqual(summary(found,"anti_dumping"),"35.60 %（其他企业，C999）")
        found[0]["additional_code"]="C123"
        self.assertEqual(summary(found,"anti_dumping"),"按企业/条件适用（见详情）")

    def test_xi_remedy_measures_in_official_china_groups_are_retained(self):
        payload=json.dumps({"included":[]}).encode()
        found=xi_remedies([xi(kind="552",geo="G001",expression="14%")],
                          TODAY,payload,{"G001"})
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["effective_status"],"active")
        self.assertEqual(summary(found,"anti_dumping"),"14%")

    def test_ni_country_exclusion_and_conditional_are_not_plain_rates(self):
        included={"included":[
            {"type":"geographical_area","id":"CN","attributes":{"code":"CN"}}
        ]}
        exclusion={"excluded_countries":{"data":[{"type":"geographical_area","id":"CN"}]}}
        conditional={"measure_conditions":{"data":[{"type":"measure_condition","id":"one"}]}}
        rows=[xi(geo="1011",rels=exclusion),xi("554","CN","8%",rels=conditional)]
        result=xi_remedies(rows,TODAY,json.dumps(included).encode())
        self.assertEqual(len(result),1)
        self.assertIn("条件",result[0]["conditions"])
        self.assertEqual(summary(result,"countervailing"),"按企业/条件适用（见详情）")

    def test_standard_rate_accepts_specific_and_compound_duty_expressions(self):
        self.assertEqual(gb_default_rate([("12.50 GBP / 100 kg", "", "", "")]), "12.50 GBP / 100 kg")
        self.assertEqual(simple_rate([("12.50 GBP / 100 kg + 8%", "", "", "")]), "12.50 GBP / 100 kg + 8%")
        self.assertEqual(gb_default_rate([("8%", "", "", ""), ("5%", "", "", "")]), "")

    def test_gb_conditional_rate_is_preserved_as_a_rate_option(self):
        rows=[("103","1011","8%","","","","","2026-01-01","",json.dumps({
            "measure__sid":"1","measure__type__description":"Third country duty",
            "measure__geographical_area__description":"ERGA OMNES"})),
              ("105","1011","0%","C123","","authorised-use-certificate","","2026-01-01","",json.dumps({
            "measure__sid":"2","measure__type__description":"Non preferential duty under authorised use",
            "measure__geographical_area__description":"ERGA OMNES"}))]
        options=_gb_rate_options(rows,TODAY)
        self.assertEqual(len(options),2)
        self.assertEqual(options[0]["applicability"],"default")
        self.assertTrue(options[1]["conditional"])
        self.assertTrue(options[1]["special_use"])
        self.assertEqual(options[1]["additional_code"],"C123")

    def test_xi_conditional_rate_resolves_linked_expression_and_condition(self):
        doc={"included":[
            {"type":"duty_expression","id":"d","attributes":{"base":"7.5 %"}},
            {"type":"measure_condition","id":"c","attributes":{"description":"certificate required"}},
            {"type":"additional_code","id":"a","attributes":{"code":"C123","description":"Authorised exporter"}},
            {"type":"geographical_area","id":"1011","attributes":{"description":"ERGA OMNES"}},
        ]}
        node={"type":"measure","id":"m","relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"d"}},
            "measure_conditions":{"data":[{"type":"measure_condition","id":"c"}]},
            "additional_code":{"data":{"type":"additional_code","id":"a"}},
            "geographical_area":{"data":[{"type":"geographical_area","id":"1011"}]},
        }}
        rows=[("103","1011","", "2026-01-01","",json.dumps(node))]
        options=_xi_rate_options(rows,json.dumps(doc),TODAY)
        self.assertEqual(options[0]["expression"],"7.5 %")
        self.assertEqual(options[0]["additional_code"],"C123")
        self.assertIn("certificate required",options[0]["conditions"])
        self.assertTrue(options[0]["conditional"])
        self.assertTrue(options[0]["china_applicable"])

    def test_xi_multiple_expressions_nested_conditions_and_unresolved_codes_are_preserved(self):
        doc={"included":[
            {"type":"duty_expression","id":"d1","attributes":{"base":"7.5 %","verbose_duty":"7.5 percent"}},
            {"type":"duty_expression","id":"d2","attributes":{"base":"12 GBP / 100 kg"}},
            {"type":"measure_condition_permutation_group","id":"g1","attributes":{},
             "relationships":{"measure_conditions":{"data":[{"type":"measure_condition","id":"c1"}]}}},
            {"type":"measure_condition","id":"c1","attributes":{"description":"Export licence required"}},
        ]}
        node={"type":"measure","id":"m1","relationships":{
            "duty_expression":{"data":[
                {"type":"duty_expression","id":"d1"},
                {"type":"duty_expression","id":"d2"}]},
            "measure_condition_permutation_groups":{"data":[
                {"type":"measure_condition_permutation_group","id":"g1"}]},
            "additional_code":{"data":{"type":"additional_code","id":"missing-code"}},
        }}
        options=_xi_rate_options(
            [("103","1011","", "2026-01-01","",json.dumps(node))],
            json.dumps(doc),TODAY)
        self.assertEqual(len(options),1)
        option=options[0]
        self.assertEqual(option["expression"],"")
        self.assertEqual(option["expression_resolution"],"multiple")
        self.assertEqual([x["expression"] for x in option["expression_options"]],
                         ["7.5 %","12 GBP / 100 kg"])
        self.assertEqual(option["additional_code_ids"],["missing-code"])
        self.assertTrue(option["conditional"])
        self.assertEqual(option["applicability"],"unresolved")
        self.assertIn("Export licence required",option["conditions"])
        self.assertTrue(any(x["relationship"]=="additional_code"
                            for x in option["unresolved_relationships"]))

    def test_projection_completeness_metadata_is_derived_from_counts(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/
                "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        self.assertNotIn('"gb_rate_option_projection_complete":True',source)
        self.assertNotIn('"xi_rate_option_projection_complete":True',source)
        self.assertIn('counts["gb_rate_source_rows"]==counts["gb_rate_projected_rows"]',source)
        self.assertIn('counts["xi_rate_source_rows"]==counts["xi_rate_projected_rows"]',source)

    def test_conditional_row_without_expression_is_still_retained(self):
        rows=[("103","1011","","","","certificate required","","2026-01-01","",json.dumps({
            "measure__sid":"3","measure__type__description":"Third country duty"}))]
        options=_gb_rate_options(rows,TODAY)
        self.assertEqual(len(options),1)
        self.assertFalse(options[0]["expression_available"])
        self.assertEqual(options[0]["conditions"],"certificate required")

    def test_gb_specific_and_compound_unconditional_duties_are_not_lost(self):
        self.assertEqual(gb_default_rate([("12.50 GBP / 100 kg", "", "", "")]),
                         "12.50 GBP / 100 kg")
        self.assertEqual(gb_default_rate([("12.50 GBP / 100 kg + 8%", "", "", "")]),
                         "12.50 GBP / 100 kg + 8%")
        self.assertEqual(gb_default_rate([("12.50 GBP / 100 kg", "A123", "", "")]), "")
        self.assertEqual(gb_default_rate([("12.50 GBP / 100 kg", "", "certificate", "")]), "")

    def test_rate_options_classify_active_expired_future_and_unknown_dates(self):
        from src.target_duty.desktop_release_bundle import _effective_status
        self.assertEqual(_effective_status("2026-01-01", "", TODAY), "active")
        self.assertEqual(_effective_status("2025-01-01", "2026-10-15", TODAY), "expired")
        self.assertEqual(_effective_status("2026-10-17", "", TODAY), "future")
        self.assertEqual(_effective_status("", "", TODAY), "unknown")
        self.assertEqual(_effective_status("bad-date", "", TODAY), "unknown")

    def test_xi_conditional_options_keep_condition_ids_and_effective_status(self):
        from src.target_duty.desktop_release_bundle import _xi_rate_options
        doc={"included":[
            {"type":"duty_expression","id":"d","attributes":{"base":"7.5 %"}},
            {"type":"measure_condition","id":"condition-7","attributes":{"description":"certificate required"}},
            {"type":"additional_code","id":"a","attributes":{"code":"C123","description":"Authorised exporter"}},
        ]}
        def row(start, end=None):
            node={"type":"measure","id":"m-"+start,"relationships":{
                "duty_expression":{"data":{"type":"duty_expression","id":"d"}},
                "measure_conditions":{"data":[{"type":"measure_condition","id":"condition-7"}]},
                "additional_code":{"data":{"type":"additional_code","id":"a"}},
            }}
            return ("103","1011","",start,end,json.dumps(node))
        options=_xi_rate_options([row("2026-01-01"),row("2025-01-01","2026-10-15"),
                                  row("2026-10-17")],json.dumps(doc),TODAY)
        self.assertEqual([x["effective_status"] for x in options],
                         ["active","expired","future"])
        self.assertEqual(options[0]["condition_ids"],["condition-7"])
        self.assertEqual(options[0]["additional_code_ids"],["a"])
        self.assertIn("certificate required",options[0]["conditions"])

    def test_china_specific_default_rate_precedes_erga_omnes(self):
        rows=[
            ("8%", "", "", "", "1011"),
            ("5%", "", "", "", "CN"),
        ]
        self.assertEqual(gb_china_rate(rows), "5%")
        self.assertEqual(gb_china_rate([rows[0], ("5%", "A123", "", "", "CN")]), "8%")
        self.assertEqual(gb_china_rate([("5%", "", "", "", "CN"),
                                        ("6%", "", "", "", "CN")]), "")
        self.assertEqual(gb_china_rate([("8%", "", "", "US|CA", "1011")]), "8%")
        self.assertEqual(gb_china_rate([("8%", "", "", "CHN|US", "1011")]), "")
        self.assertTrue(_china_applicable("CN", "SCN"))
        self.assertFalse(_china_applicable("1011", "US|CN"))
        self.assertFalse(_china_applicable("1011", "G001", {"G001"}))
        self.assertTrue(_china_applicable("G001", "", {"G001"}))
        self.assertEqual(gb_china_rate([("6%", "", "", "", "G001"),
                                        ("8%", "", "", "", "1011")], {"G001"}), "6%")
        self.assertEqual(gb_china_rate([("8%", "", "", "", "1011")], set()), "8%")

    def test_gb_rate_option_projection_keeps_all_date_states_and_other_measure_types(self):
        db=sqlite3.connect(":memory:")
        db.execute("""CREATE TABLE measures (
            region TEXT, code TEXT, measure_type TEXT, geography TEXT,
            expression TEXT, additional_code TEXT, conditions TEXT, excluded_areas TEXT,
            effective_start TEXT, effective_end TEXT, raw_json TEXT, ordinal INTEGER)""")
        rows=[
            ("103","1011","8%","2026-01-01",None),
            ("105","1011","0%","2025-01-01","2026-10-15"),
            ("106","1011","3%","2026-10-17",None),
            ("103","CN","",None,None),
            ("142","G001","2%","2026-01-01",None),
            ("142","US","1%","2026-01-01",None),
            ("552","CN","22%","2026-01-01",None),
        ]
        for i,(kind,geo,rate,start,end) in enumerate(rows):
            raw=json.dumps({"measure__sid":str(i+1),
                "measure__type__description":"official measure "+kind})
            db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("GB","0101210000",kind,geo,rate,"","","",start,end,raw,i))
        # China is explicitly excluded from this Erga Omnes measure. Keep it
        # in the option evidence but label it inapplicable instead of dropping it.
        db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("GB","0101210000","103","1011","7%","","","CN",
             "2026-01-01",None,json.dumps({"measure__sid":"excluded-cn"}),10))
        source=_gb_rate_option_rows(db,"0101210000",{"G001"})
        options=_gb_rate_options(source,TODAY,{"G001"})
        self.assertEqual(len(source),6)  # US is excluded; remedies stay in their own table.
        self.assertEqual(len(options),6)
        self.assertEqual({o["effective_status"] for o in options},
                         {"active","expired","future","unknown"})
        self.assertEqual(options[2]["measure_type"],"106")
        self.assertEqual(options[2]["applicability"],"other_measure")
        self.assertFalse(options[3]["expression_available"])
        self.assertEqual(options[4]["geography"],"G001")
        self.assertTrue(options[4]["china_applicable"])
        self.assertEqual(options[5]["source_id"],"excluded-cn")
        self.assertFalse(options[5]["china_applicable"])
        self.assertEqual(options[5]["excluded_areas"],"CN")
        db.close()

    def test_xi_rate_option_projection_keeps_all_date_states_and_other_measure_types(self):
        db=sqlite3.connect(":memory:")
        db.execute("""CREATE TABLE measures (
            code TEXT, measure_type TEXT, geography_id TEXT, duty_expression TEXT,
            effective_start TEXT, effective_end TEXT, raw_json TEXT, ordinal INTEGER)""")
        rows=[
            ("103","1011","8%","2026-01-01",None),
            ("105","1011","0%","2025-01-01","2026-10-15"),
            ("106","CN","3%","2026-10-17",None),
            ("552","CN","22%","2026-01-01",None),
        ]
        for i,(kind,geo,rate,start,end) in enumerate(rows):
            raw=json.dumps({"type":"measure","id":str(i+1),"relationships":{}})
            db.execute("INSERT INTO measures VALUES (?,?,?,?,?,?,?,?)",
                ("0101210000",kind,geo,rate,start,end,raw,i))
        source=_xi_rate_option_rows(db,"0101210000")
        options=_xi_rate_options(source,json.dumps({"included":[]}),TODAY)
        self.assertEqual(len(source),3)
        self.assertEqual(len(options),3)
        self.assertEqual([o["effective_status"] for o in options],
                         ["active","expired","future"])
        self.assertEqual(options[2]["applicability"],"other_measure")
        db.close()

    def test_xi_control_measure_linked_but_empty_is_not_unresolved(self):
        """CITES/organic/REACH controls have no duty: linked-but-empty is official."""
        measure={"type":"measure","id":"m1","relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"m1-duty_expression"}}}}
        body={"included":[
            measure,
            {"type":"duty_expression","id":"m1-duty_expression",
             "attributes":{"base":"","formatted_base":"","verbose_duty":""}},
        ]}
        row=("710","1011","","2026-01-01",None,json.dumps(measure))
        (option,)=_xi_rate_options([row],json.dumps(body),TODAY)
        self.assertEqual(option["expression_resolution"],"linked_but_empty")
        self.assertEqual(option["applicability"],"other_measure")
        self.assertEqual(option["unresolved_relationships"],[])

    def test_xi_control_measure_conditions_carry_the_official_amounts(self):
        """695-type additional duties hold 50%/0% on measure_conditions."""
        measure={"type":"measure","id":"4171349","relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"4171349-duty_expression"}},
            "measure_conditions":{"data":[
                {"type":"measure_condition","id":"2395198"},
                {"type":"measure_condition","id":"2395199"}]}}}
        body={"included":[
            measure,
            {"type":"duty_expression","id":"4171349-duty_expression",
             "attributes":{"base":"","formatted_base":"","verbose_duty":""}},
            {"type":"measure_condition","id":"2395198","attributes":{
                "condition_code":"B","action_code":"01",
                "duty_expression":"<span>50.00</span> %",
                "requirement":"Particular provisions: Products exported directly or indirectly from the Russian",
                "document_code":"Y049","certificate_description":"Russian-origin evidence"}},
            {"type":"measure_condition","id":"2395199","attributes":{
                "condition_code":"B","action_code":"01",
                "duty_expression":"<span>0.00</span> %","requirement":"None"}},
        ]}
        row=("695","1011","","2026-01-01",None,json.dumps(measure))
        (option,)=_xi_rate_options([row],json.dumps(body),TODAY)
        self.assertEqual(option["expression"],"")
        self.assertEqual(option["expression_resolution"],"linked_but_empty")
        self.assertEqual(option["applicability"],"other_measure")
        self.assertEqual(option["unresolved_relationships"],[])
        rates={r["condition_id"]:r for r in option["condition_rates"]}
        self.assertEqual(rates["2395198"]["expression"],"50.00 %")
        self.assertEqual(rates["2395198"]["certificate_code"],"Y049")
        self.assertEqual(rates["2395199"]["expression"],"0.00 %")

    def test_xi_103_with_linked_but_empty_measure_expression_stays_unresolved(self):
        """An unconditional third-country duty row without any amount is unresolved."""
        measure={"type":"measure","id":"m1","relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"m1-duty_expression"}}}}
        body={"included":[
            measure,
            {"type":"duty_expression","id":"m1-duty_expression",
             "attributes":{"base":"","formatted_base":"","verbose_duty":""}},
        ]}
        row=("103","1011","","2026-01-01",None,json.dumps(measure))
        (option,)=_xi_rate_options([row],json.dumps(body),TODAY)
        self.assertEqual(option["expression_resolution"],"linked_but_empty")
        self.assertEqual(option["applicability"],"unresolved")

    def test_xi_103_with_conditional_amounts_is_conditional_not_unresolved(self):
        """1516209821 XI erga-omnes: measure expression empty, conditions carry rates."""
        measure={"type":"measure","id":"m1","relationships":{
            "duty_expression":{"data":{"type":"duty_expression","id":"m1-duty_expression"}},
            "measure_conditions":{"data":[{"type":"measure_condition","id":"c1"}]}}}
        body={"included":[
            measure,
            {"type":"duty_expression","id":"m1-duty_expression",
             "attributes":{"base":"","formatted_base":"","verbose_duty":""}},
            {"type":"measure_condition","id":"c1","attributes":{
                "condition_code":"B","action_code":"01","duty_expression":"0.00 %"}},
        ]}
        row=("103","1011","","2026-01-01",None,json.dumps(measure))
        (option,)=_xi_rate_options([row],json.dumps(body),TODAY)
        self.assertTrue(option["conditional"])
        self.assertEqual(option["applicability"],"conditional")
        self.assertEqual(option["condition_rates"][0]["expression"],"0.00 %")
        self.assertEqual(option["unresolved_relationships"],[])

    def test_xi_china_default_uses_cn_specific_rate_and_country_exclusions(self):
        day=TODAY
        def row(geo, rate, start="2026-01-01", end=None, rels=None):
            return ("103",geo,rate,start,end,json.dumps({"relationships":rels or {}}))
        doc={"included":[
            {"type":"geographical_area","id":"US","attributes":{"code":"US","description":"United States"}},
            {"type":"geographical_area","id":"CN","attributes":{"code":"CN","description":"China"}},
        ]}
        self.assertEqual(xi_rate([row("1011","8%"),row("CN","5%")],day,
                                 json.dumps(doc).encode()),"5%")
        us_exclusion={"excluded_countries":{"data":[{"type":"geographical_area","id":"US"}]}}
        self.assertEqual(xi_rate([row("1011","8%",rels=us_exclusion)],day,
                                 json.dumps(doc).encode()),"8%")
        self.assertEqual(xi_rate([row("G001","4%"),row("1011","8%")],day,
                                 json.dumps(doc).encode(),{"G001"}),"4%")
        self.assertEqual(xi_rate([row("G001","4%")],day,
                                 json.dumps(doc).encode(),set()),"")
        cn_exclusion={"excluded_countries":{"data":[{"type":"geographical_area","id":"CN"}]}}
        self.assertEqual(xi_rate([row("1011","8%",rels=cn_exclusion)],day,
                                 json.dumps(doc).encode()),"")
        group_doc={"included":[
            {"type":"geographical_area","id":"G001","attributes":{"code":"G001","description":"China group"}}
        ]}
        group_exclusion={"excluded_geographical_areas":{"data":[{"type":"geographical_area","id":"G001"}]}}
        self.assertEqual(xi_rate([row("1011","8%",rels=group_exclusion)],day,
                                 json.dumps(group_doc).encode(),{"G001"},{"G001":"China group"}),"")
        conditional={"measure_conditions":{"data":[{"type":"measure_condition","id":"c"}]}}
        self.assertEqual(xi_rate([row("1011","8%"),row("CN","5%",rels=conditional)],day,
                                 json.dumps(doc).encode()),"8%")
        unresolved={"excluded_countries":{"data":[{"type":"geographical_area","id":"unknown"}]}}
        self.assertEqual(xi_rate([row("1011","8%",rels=unresolved)],day,
                                 json.dumps(doc).encode()),"")

    def test_desktop_schema_wires_all_four_columns_and_detail(self):
        s=(Path(__file__).resolve().parents[1]/
           "src"/"target_duty"/"desktop_release_bundle.py").read_text()
        for token in ("ni_anti_dumping_rate TEXT","ni_countervailing_rate TEXT",
                      "trade_remedy_details TEXT","gb_remedies(","xi_remedies(",
                      "gb_antidumping","ni_countervailing"):
            self.assertIn(token,s)


if __name__=="__main__":
    unittest.main()
