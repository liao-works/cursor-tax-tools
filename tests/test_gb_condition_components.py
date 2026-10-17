"""Offline GB condition-component join, revision disambiguation and wiring tests."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.target_duty.gb_condition_components import (
    TABLES, _format_component, _format_expression, _pick,
    _condition_signature, _expected_measure_sids, build_condition_source,
)
from src.target_duty.china_trade_remedies import gb_remedies


def format_component_test_rows():
    return {
        "pct": {"duty_expression_id": "1", "duty_amount": "35.60", "monetary_unit_code": "",
                "measurement_unit_code": "", "measurement_unit_qualifier_code": ""},
        "specific": {"duty_expression_id": "1", "duty_amount": "9.50", "monetary_unit_code": "GBP",
                     "measurement_unit_code": "100 kg", "measurement_unit_qualifier_code": ""},
        "blank": {"duty_expression_id": "1", "duty_amount": "", "duty_expression_description": "% or amount"},
    }


class GBConditionComponentFormattingTests(unittest.TestCase):
    def test_condition_component_percentage_is_not_dropped(self):
        self.assertEqual(_format_component(format_component_test_rows()["pct"]), "35.60 %")

    def test_specific_component_preserves_currency_and_unit(self):
        self.assertEqual(_format_component(format_component_test_rows()["specific"]), "9.50 GBP / 100 kg")

    def test_empty_component_amount_is_never_a_fake_rate(self):
        self.assertEqual(_format_component(format_component_test_rows()["blank"]), "")
        self.assertEqual(_format_expression([{"duty_expression_id": "1", "duty_amount": "35.60"},
                                             {"duty_expression_id": "4", "duty_amount": ""}]), "")

    def test_unknown_expression_operator_not_invented(self):
        component = {"duty_expression_id": "999", "duty_amount": "25.00",
                     "duty_expression_description": "special-duty-operator",
                     "monetary_unit_code": "", "measurement_unit_code": ""}
        self.assertIn("special-duty-operator", _format_component(component))
        self.assertNotEqual(_format_component(component), "25.00 %")

    def test_exact_source_field_preferred_and_ambiguous_suffix_rejected(self):
        headers = ["measure_condition__measure_sid", "measure_condition__sid", "sid"]
        self.assertEqual(_pick(headers, ["measure_condition__measure_sid"], label="condition sid"),
                         "measure_condition__measure_sid")
        self.assertEqual(_pick(headers, ["sid"], label="sid"), "sid")
        with self.assertRaises(ValueError):
            _pick(["table_a__target_name", "table_b__target_name"],
                  ["target_name"], label="ambiguous")

    def test_condition_signature_parses_export_text(self):
        text = "condition:A,certificate:D008,action:01|condition:A,action:01"
        self.assertEqual(_condition_signature(text), [("D008", "01"), ("", "01")])
        self.assertEqual(_condition_signature(""), [])

    def test_download_table_returns_metadata_and_writes_csv(self):
        import io
        from src.target_duty import gb_condition_components as module

        class FakeResponse:
            headers = {"Content-Length": "42"}

            def __init__(self, payload):
                self._buffer = io.BytesIO(payload)

            def geturl(self):
                return ("https://data.api.trade.gov.uk/v1/datasets/uk-tariff-2021-01-01/"
                        "versions/v4.0.1629/tables/certificate-types/data?format=csv&download")

            def read(self, size):
                return self._buffer.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        payload = b'"trackedmodel_ptr_id","sid"\r\n"65","D"\r\n'
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "certificate-types.csv"
            with patch.object(module.urllib.request, "urlopen",
                              return_value=FakeResponse(payload)):
                meta, rows = module.download_table("v4.0.1629", "certificate-types", str(target))
            self.assertIsNone(rows)
            self.assertEqual(meta["row_count"], 1)
            self.assertEqual(meta["columns"], ["trackedmodel_ptr_id", "sid"])
            self.assertEqual(meta["sha256"], __import__("hashlib").sha256(payload).hexdigest())
            self.assertTrue(target.exists())


def make_gb_db(path, rows):
    db = sqlite3.connect(path)
    try:
        db.executescript("CREATE TABLE measures(measure_type TEXT,conditions TEXT,raw_json TEXT)")
        db.executemany("INSERT INTO measures VALUES (?,?,?)", rows)
        db.commit()
    finally:
        db.close()


class GBConditionJoinTests(unittest.TestCase):
    def _run_join(self, work, gb_rows, conditions, components, tracked, transactions,
                  extra_tables=None):
        fixtures = {
            "measures": {"headers": ["trackedmodel_ptr_id", "sid"],
                         "rows": [["MR1", "20272576"], ["MR2", "20272576"]]},
            "measure-conditions": {
                "headers": ["trackedmodel_ptr_id", "sid", "component_sequence_number", "duty_amount",
                            "action_id", "condition_code_id", "dependent_measure_id",
                            "required_certificate_id"],
                "rows": conditions},
            "measure-condition-components": {
                "headers": ["trackedmodel_ptr_id", "condition_id", "duty_expression_id", "duty_amount",
                            "monetary_unit_id"],
                "rows": components},
            "measure-duty-expressions": {
                "headers": ["trackedmodel_ptr_id", "sid", "description"],
                "rows": [["32", "1", "% or amount"]]},
            "measure-monetary-units": {"headers": ["trackedmodel_ptr_id", "code", "abbreviation"], "rows": []},
            "measure-measurements": {"headers": ["trackedmodel_ptr_id"], "rows": []},
            "measure-measurements-units": {"headers": ["trackedmodel_ptr_id", "code", "abbreviation"], "rows": []},
            "measure-measurement-qualifiers": {"headers": ["trackedmodel_ptr_id", "code"], "rows": []},
            "measure-condition-codes": {
                "headers": ["trackedmodel_ptr_id", "code", "description"],
                "rows": [["56", "A", "Presentation of an anti-dumping/countervailing document"]]},
            "measure-actions": {
                "headers": ["trackedmodel_ptr_id", "code", "description"],
                "rows": [["783", "01", "Apply the amount of the action"]]},
            "certificates": {
                "headers": ["trackedmodel_ptr_id", "sid", "certificate_type_id"],
                "rows": [["64625", "008", "65"]]},
            "certificate-types": {
                "headers": ["trackedmodel_ptr_id", "sid", "description"],
                "rows": [["65", "D", "Anti-dumping/countervailing document"]]},
            "common-tracked-models": {
                "headers": ["id", "update_type", "polymorphic_ctype_id", "transaction_id", "version_group_id"],
                "rows": tracked},
            "common-transactions": {
                "headers": ["id", "created_at", "updated_at", "import_transaction_id", "order",
                            "composite_key", "workbasket_id", "partition"],
                "rows": transactions},
        }
        if extra_tables:
            fixtures.update(extra_tables)
        for table, spec in fixtures.items():
            path = work / (table + ".csv")
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(spec["headers"])
                writer.writerows(spec["rows"])

        def simulated_download(version, table, destination):
            spec = fixtures[table]
            data = (",".join(spec["headers"]) + "\r\n" +
                    "\r\n".join(",".join(str(cell) for cell in row) for row in spec["rows"]))
            destination = Path(destination)
            destination.write_text(data, encoding="utf-8")
            return ({"table": table, "version": version, "sha256": "f" * 64,
                     "bytes": len(data), "row_count": len(spec["rows"]),
                     "columns": spec["headers"], "url": "test", "final_url": "test"}, None)

        return simulated_download

    def _standard_fixtures(self, with_components=True):
        gb_rows = [
            ("552", "condition:A,certificate:D008,action:01|condition:A,action:01",
             '{"measure__sid":"20272576"}'),
        ]
        conditions = [
            # older measure revision MR1 (transaction T1 order 100)
            ["C1", "CS1", "1", "#NA", "783", "56", "MR1", "64625"],
            ["C2", "CS2", "2", "#NA", "783", "56", "MR1", "#NA"],
            # latest measure revision MR2 (transaction T2 order 200), amounts dropped
            ["C3", "CS1", "1", "#NA", "783", "56", "MR2", "64625"],
            ["C4", "CS2", "2", "#NA", "783", "56", "MR2", "#NA"],
        ]
        components = [["K1", "C1", "32", "14.79", "#NA"], ["K2", "C2", "32", "54.64", "#NA"]] \
            if with_components else []
        tracked = [
            ["C1", "1", "39", "T1", "G1"], ["C2", "1", "39", "T1", "G2"],
            ["C3", "1", "39", "T2", "G1"], ["C4", "1", "39", "T2", "G2"],
        ]
        transactions = [["T1", "2025-11-19", "2025-11-19", "#NA", "100", "k1", "1", "2"],
                        ["T2", "2025-11-24", "2025-11-24", "#NA", "200", "k2", "1", "2"]]
        return gb_rows, conditions, components, tracked, transactions

    def test_measure_ids_and_signatures_taken_from_source(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "measures.db"
            make_gb_db(p, [
                ("552", "condition:A,certificate:D008,action:01", '{"measure__sid":"123"}'),
                ("554", "", '{"measure__sid":"456"}'),
                ("103", "condition:A", '{"measure__sid":"789"}'),
            ])
            all_sids, condition_sids, signatures = _expected_measure_sids(str(p))
        self.assertEqual(all_sids, {"123", "456"})
        self.assertEqual(condition_sids, {"123"})
        self.assertEqual(signatures, {"123": (("D008", "01"),)})

    def test_amount_bearing_older_revision_wins_over_blank_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            gb_rows, conditions, components, tracked, transactions = self._standard_fixtures()
            download = self._run_join(work, gb_rows, conditions, components, tracked, transactions)
            gb_db = work / "measures.db"
            make_gb_db(gb_db, gb_rows)
            output = work / "audit.json"
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=download):
                source = build_condition_source("v4.0.1629", str(gb_db), str(output), str(work / "in"))
            rates = source["condition_rates_by_measure_sid"]["20272576"]
            by_cert = {entry["certificate_code"]: entry for entry in rates}
            self.assertEqual(by_cert["D008"]["duty_expression"], "14.79 %")
            self.assertEqual(by_cert[""]["duty_expression"], "54.64 %")
            self.assertTrue(by_cert["D008"]["amount_from_older_revision"])
            self.assertFalse(by_cert["D008"]["latest_revision_has_amount"])
            self.assertTrue(by_cert["D008"]["resolved"])
            coverage = source["coverage"]
            self.assertEqual(coverage["condition_entities"], 2)
            self.assertEqual(coverage["amount_from_older_revision"], 2)
            self.assertEqual(coverage["signature_mismatch_measure_sids"], 0)
            self.assertEqual(coverage["missing_expected_measure_sids"], [])
            self.assertTrue(output.exists())

    def test_blank_amounts_stay_blank_and_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            gb_rows, conditions, _, tracked, transactions = self._standard_fixtures()
            download = self._run_join(work, gb_rows, conditions, [], tracked, transactions)
            gb_db = work / "measures.db"
            make_gb_db(gb_db, gb_rows)
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=download):
                source = build_condition_source("v4.0.1629", str(gb_db), str(work / "audit.json"),
                                                str(work / "in"))
            rates = source["condition_rates_by_measure_sid"]["20272576"]
            self.assertTrue(rates)
            for entry in rates:
                self.assertEqual(entry["duty_expression"], "")
                self.assertFalse(entry["resolved"])
            coverage = source["coverage"]
            self.assertEqual(coverage["condition_entities_without_amount"], 2)
            self.assertEqual(coverage["condition_entities_with_amount"], 0)

    def test_deleted_revisions_never_revive_amounts(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            gb_rows, conditions, components, tracked, transactions = self._standard_fixtures()
            tracked = [["C1", "2", "39", "T1", "G1"], ["C2", "2", "39", "T1", "G2"],
                       ["C3", "1", "39", "T2", "G1"], ["C4", "1", "39", "T2", "G2"]]
            download = self._run_join(work, gb_rows, conditions, components, tracked, transactions)
            gb_db = work / "measures.db"
            make_gb_db(gb_db, gb_rows)
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=download):
                source = build_condition_source("v4.0.1629", str(gb_db), str(work / "audit.json"),
                                                str(work / "in"))
            for entry in source["condition_rates_by_measure_sid"]["20272576"]:
                self.assertEqual(entry["duty_expression"], "")

    def test_missing_expected_measure_sid_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            gb_rows, conditions, components, tracked, transactions = self._standard_fixtures()
            gb_rows.append(("552", "condition:A,certificate:D008,action:01",
                            '{"measure__sid":"99999999"}'))
            download = self._run_join(work, gb_rows, conditions, components, tracked, transactions)
            gb_db = work / "measures.db"
            make_gb_db(gb_db, gb_rows)
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=download):
                with self.assertRaises(ValueError) as ctx:
                    build_condition_source("v4.0.1629", str(gb_db), str(work / "audit.json"),
                                           str(work / "in"))
            self.assertIn("99999999", str(ctx.exception))

    def test_certificate_signature_mismatch_is_flagged(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            gb_rows, conditions, components, tracked, transactions = self._standard_fixtures()
            conditions = [["C1", "CS1", "1", "#NA", "783", "56", "MR1", "#NA"],
                          ["C2", "CS2", "2", "#NA", "783", "56", "MR1", "#NA"]]
            download = self._run_join(work, gb_rows, conditions, components, tracked, transactions)
            gb_db = work / "measures.db"
            make_gb_db(gb_db, gb_rows)
            with patch("src.target_duty.gb_condition_components.download_table",
                       side_effect=download):
                source = build_condition_source("v4.0.1629", str(gb_db), str(work / "audit.json"),
                                                str(work / "in"))
            coverage = source["coverage"]
            self.assertEqual(coverage["signature_mismatch_measure_sids"], 1)
            self.assertTrue(coverage["condition_signature_mismatches"][0]["measure_sid"] == "20272576")
            for entry in source["condition_rates_by_measure_sid"]["20272576"]:
                self.assertTrue(entry.get("signature_mismatch"))


class GBRemedyConditionWiringTests(unittest.TestCase):
    def _measure(self, conditions_text="condition:A,certificate:D008,action:01"):
        import json as _json
        return ("552", "CN", "", "8A43", conditions_text, "", "2025-11-25", "",
                _json.dumps({"measure__sid": "20272576",
                             "measure__additional_code__description": "Zhuoyue Group",
                             "measure__geographical_area__description": "China"}))

    def test_condition_rates_flow_into_enterprise_rows(self):
        condition_source = {
            "kind": "gb-condition-components-v2",
            "condition_rates_by_measure_sid": {
                "20272576": [
                    {"condition_id": "CS1", "condition_code": "A", "condition": "Presentation",
                     "action_code": "01", "action": "Apply the amount of the action",
                     "certificate_code": "D008", "certificate_description": "Invoice",
                     "requirement": "Invoice", "duty_expression": "14.79 %",
                     "amount_from_older_revision": True, "resolved": True,
                     "unresolved_relationships": [], "components": []},
                    {"condition_id": "CS2", "condition_code": "A", "condition": "Presentation",
                     "action_code": "01", "action": "Apply the amount of the action",
                     "certificate_code": "", "certificate_description": "",
                     "requirement": "No document provided", "duty_expression": "54.64 %",
                     "amount_from_older_revision": True, "resolved": True,
                     "unresolved_relationships": [], "components": []},
                ]
            },
        }
        records = gb_remedies([self._measure()], __import__("datetime").date(2026, 10, 17),
                              condition_source=condition_source)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["expression"], "")
        self.assertEqual([r["expression"] for r in record["condition_rates"]],
                         ["14.79 %", "54.64 %"])
        enterprise = record["enterprise_rates"]
        conditional = [row for row in enterprise if row["expression_origin"] == "condition"]
        self.assertEqual(sorted(row["expression"] for row in conditional),
                         ["14.79 %", "54.64 %"])
        for row in conditional:
            self.assertEqual(row["selection_status"], "requires_condition")
            self.assertEqual(row["additional_code"], "8A43")
        self.assertEqual(conditional[0]["certificate_code"], "D008")

    def test_measure_without_join_entry_is_marked_unresolved(self):
        records = gb_remedies([self._measure()], __import__("datetime").date(2026, 10, 17),
                              condition_source={"kind": "gb-condition-components-v2",
                                                "condition_rates_by_measure_sid": {}})
        self.assertEqual(records[0]["unresolved_relationships"],
                         [{"relationship": "gb_condition_join", "id": "20272576"}])
        self.assertEqual(records[0]["condition_rates"], [])
        self.assertEqual(records[0]["enterprise_rates"][0]["selection_status"],
                         "expression_unavailable")

    def test_without_condition_source_projection_is_unchanged(self):
        records = gb_remedies([self._measure()], __import__("datetime").date(2026, 10, 17))
        self.assertEqual(records[0]["condition_rates"], [])
        self.assertEqual(records[0]["condition_details"], [])
        self.assertEqual(records[0]["unresolved_relationships"], [])


if __name__ == "__main__":
    unittest.main()
