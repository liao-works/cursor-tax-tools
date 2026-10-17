"""Offline GB normalized measure-condition join regression tests."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.target_duty.gb_condition_components import (
    TABLES, _format_component, _format_expression, _pick,
    build_condition_source, _expected_measure_sids,
)


class GBConditionComponentsTests(unittest.TestCase):
    def test_condition_component_percentage_is_not_dropped(self):
        component={"duty_expression_id":"1","duty_amount":"35.60","monetary_unit_code":"",
                   "measurement_unit_code":"","measurement_unit_qualifier_code":""}
        self.assertEqual(_format_component(component),"35.60 %")

    def test_specific_component_preserves_currency_and_unit(self):
        component={"duty_expression_id":"1","duty_amount":"9.50","monetary_unit_code":"GBP",
                   "measurement_unit_code":"100 kg","measurement_unit_qualifier_code":""}
        self.assertEqual(_format_component(component),"9.50 GBP / 100 kg")

    def test_unknown_expression_operator_not_invented(self):
        component={"duty_expression_id":"999","duty_amount":"25.00",
                   "duty_expression_description":"special-duty-operator",
                   "monetary_unit_code":"","measurement_unit_code":""}
        self.assertIn("special-duty-operator",_format_component(component))
        self.assertNotEqual(_format_component(component),"25.00 %")

    def test_exact_source_field_preferred_and_ambiguous_suffix_rejected(self):
        headers=["measure_condition__measure_sid", "measure_condition__sid","sid"]
        self.assertEqual(_pick(headers,["measure_condition__measure_sid"],label="condition sid"),
                         "measure_condition__measure_sid")
        self.assertEqual(_pick(headers,["sid"],label="sid"),"sid")
        with self.assertRaises(ValueError):
            _pick(["table_a__target_name","table_b__target_name"],
                  ["target_name"],label="ambiguous")

    def test_measure_ids_taken_from_source_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"measures.db"
            db=sqlite3.connect(p)
            try:
                db.executescript("""
                CREATE TABLE measures(measure_type TEXT,conditions TEXT,raw_json TEXT);
                INSERT INTO measures VALUES('552','A:D008','{"measure__sid":"123"}');
                INSERT INTO measures VALUES('554','','{"measure__sid":"456"}');
                INSERT INTO measures VALUES('103','A:D001','{"measure__sid":"789"}');
                """)
                db.commit()
            finally:
                db.close()
            all_sids,condition_sids=_expected_measure_sids(str(p))
        self.assertEqual(all_sids,{"123","456"})
        self.assertEqual(condition_sids,{"123"})

    def test_complete_join_matches_known_official_measure_and_condition(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            gb_db=p/"measures.db"
            db=sqlite3.connect(gb_db)
            try:
                db.executescript("""
                CREATE TABLE measures(measure_type TEXT,conditions TEXT,raw_json TEXT);
                INSERT INTO measures VALUES('552','condition:A,certificate:D008,action:01',
                  '{"measure__sid":"20272576"}');
                """)
                db.commit()
            finally:
                db.close()

            fixtures={
                "measure-conditions":[
                    {"measure_condition__trackedmodel_ptr_id":"condition-1",
                     "measure_condition__sid":"condition-1",
                     "measure_condition__measure_sid":"20272576",
                     "measure_condition__condition_code":"A",
                     "measure_condition__action_code":"01",
                     "required_certificate_id":"64625",
                     "measure_condition__component_sequence_number":"1"}
                ],
                "measure-condition-components":[
                    {"measure_condition_component__condition_id":"condition-1",
                     "measure_condition_component__duty_expression_id":"1",
                     "measure_condition_component__duty_amount":"35.60"}
                ],
                "certificates":[{"trackedmodel_ptr_id":"64625","sid":"008","certificate_type_id":"65"}],
                "certificate-types":[{"trackedmodel_ptr_id":"65","sid":"D","description":"Commercial invoice"}],
                "measure-duty-expressions":[{"measure_duty_expression__sid":"1",
                    "measure_duty_expression__code":"1","measure_duty_expression__description":"Percentage"}],
            }
            def simulated_download(version,table,destination):
                rows=fixtures.get(table,[])
                headers=list(rows[0]) if rows else ["code"]
                return {"table":table,"version":version,"sha256":"f"*64,
                        "bytes":123,"row_count":len(rows),"columns":headers},rows
            output=p/"audit.json"
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=simulated_download):
                report=build_condition_source("v4.0.1629",str(gb_db),str(output),str(p/"input"))
            self.assertEqual(report["coverage"]["missing_expected_measure_sids"],[])
            self.assertEqual(report["condition_rates_by_measure_sid"]["20272576"][0]["duty_expression"],
                             "35.60 %")
            self.assertEqual(report["condition_rates_by_measure_sid"]["20272576"][0]["certificate_code"],
                             "D008")
            self.assertTrue(output.exists())


if __name__=="__main__":
    unittest.main()
