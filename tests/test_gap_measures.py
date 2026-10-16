import csv
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.target_duty.catalog import FIELDS as COMMODITY_FIELDS, build
from src.target_duty.gap_measures import (
    FIELDS, audit, classify, effective_state, gap_codes, parse_csv, source_url
)

TODAY = date(2026, 10, 14)


def rows_csv(rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def row(code, *, sid="123", measure_type="103", area="1011",
        expression="10% + 77 GBP / 100 kg", start="2026-01-01", end="#NA"):
    return {
        "commodity__code": code,
        "measure__sid": sid,
        "measure__type__id": measure_type,
        "measure__geographical_area__id": area,
        "measure__duty_expression": expression,
        "measure__additional_code__code": "#NA",
        "measure__conditions": "#NA",
        "measure__excluded_geographical_areas__ids": "#NA",
        "measure__effective_start_date": start,
        "measure__effective_end_date": end,
        "measure__type__description": "Third country duty",
        "measure__quota__order_number": "#NA",
        "measure__footnotes": "#NA",
        "measure__regulation__id": "#NA",
    }


class GapMeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.catalog, self.focused, self.output = (
            d / "catalogue.db", d / "focused.db", d / "evidence.json")
        raw = d / "catalogue.csv"
        with raw.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=COMMODITY_FIELDS)
            writer.writeheader()
            for i, code in enumerate(("0101210000", "0207412010", "0301110000")):
                writer.writerow({
                    "commodity__sid": str(i + 1), "commodity__code": code,
                    "commodity__suffix": "80", "commodity__description": "Example",
                    "commodity__validity_start": "2020-01-01",
                    "commodity__validity_end": "#NA", "parent__sid": "#NA",
                    "parent__code": "#NA", "parent__suffix": "#NA"})
        build(str(raw), str(self.catalog), as_of=TODAY,
              source_version="v4.0.1626", min_active=1)
        db = sqlite3.connect(self.focused)
        db.execute("CREATE TABLE tariffs(code TEXT PRIMARY KEY)")
        db.execute("INSERT INTO tariffs VALUES ('0101210000')")
        db.commit()
        db.close()

    def test_pinned_source_url_and_complete_columns(self):
        result = urlparse(source_url("v4.0.1626", "0207412010"))
        self.assertIn("measures-on-declarable-commodities", result.path)
        params = parse_qs(result.query, keep_blank_values=True)
        self.assertEqual(params["commodity__code"], ["0207412010"])
        self.assertEqual(params["_columns"], list(FIELDS))
        self.assertNotIn("measure__type__id", params)
        with self.assertRaises(ValueError):
            source_url("latest", "0207412010")
        with self.assertRaises(ValueError):
            source_url("v4.0.1626", "123' OR 1=1")

    def test_parse_and_classification_fail_closed(self):
        code = "0207412010"
        input_rows = [row(code, sid="10", measure_type="105", area="GB",
                          expression="", start="2020-01-01"),
                      row(code, sid="11", measure_type="103", area="1011",
                          start="2027-01-01")]
        parsed = parse_csv(rows_csv(input_rows), code)
        self.assertEqual(classify(parsed, TODAY), "active_other_measure_types")
        self.assertEqual(effective_state(parsed[1], TODAY), "inactive")
        self.assertEqual(classify([row(code)], TODAY),
                         "active_103_1011_conflicts_with_focused")
        self.assertEqual(classify([row(code, area="EU")], TODAY),
                         "active_103_other_geography")
        self.assertEqual(classify([row(code, start="BAD")], TODAY),
                         "unresolved_effective_date")
        self.assertEqual(classify([row(code, start="2027-01-01")], TODAY),
                         "historical_measures_only")
        self.assertEqual(classify([], TODAY), "no_measures_in_official_export")
        with self.assertRaisesRegex(ValueError, "Off-target"):
            parse_csv(rows_csv([row("1234567890")]), code)
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_csv(b"commodity__code,measure__sid\n", code)
        with self.assertRaisesRegex(ValueError, "measure SID"):
            parse_csv(rows_csv([row(code, sid="#NA")]), code)

    def test_preserves_every_measure_and_duty_expression(self):
        missing = gap_codes(str(self.catalog), str(self.focused))
        self.assertEqual(missing, ["0207412010", "0301110000"])
        calls = []
        def mock_fetch(code, version):
            calls.append((code, version))
            if code == "0207412010":
                a = row(code, sid="11", measure_type="103", expression="15% + 25 GBP / 100 kg")
                a["measure__conditions"] = "Certificate required"
                a["measure__additional_code__code"] = "A123"
                b = row(code, sid="12", measure_type="551", expression="36 GBP / 100 kg")
                return rows_csv([a, b])
            return rows_csv([])
        result = audit(str(self.catalog), str(self.focused), str(self.output),
                       version="v4.0.1626", as_of=TODAY, limit=5,
                       workers=2, fetcher=mock_fetch)
        self.assertEqual(result["candidate_gap_codes"], 2)
        self.assertEqual(result["attempted_codes"], 2)
        self.assertTrue(result["complete"])
        self.assertEqual(result["retained_measure_rows"], 2)
        self.assertEqual(result["classification"]["active_103_1011_conflicts_with_focused"], 1)
        self.assertEqual(result["classification"]["no_measures_in_official_export"], 1)
        self.assertEqual(len(calls), 2)
        db = sqlite3.connect(self.output.with_suffix(".db"))
        try:
            data = db.execute("SELECT expression, raw_json FROM measures ORDER BY ordinal").fetchall()
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0][0], "15% + 25 GBP / 100 kg")
            self.assertEqual(json.loads(data[0][1])["measure__additional_code__code"], "A123")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM source_requests").fetchone()[0], 2)
        finally:
            db.close()
        self.assertEqual(json.loads(self.output.read_text())["production_eligible"], False)

    def test_errors_are_recorded_not_misclassified_as_no_measures(self):
        def flaky(code, version):
            if code == "0207412010":
                raise RuntimeError("HTTP 503")
            return rows_csv([])
        report = audit(str(self.catalog), str(self.focused), str(self.output),
                       version="v4.0.1626", as_of=TODAY,
                       fetcher=flaky)
        self.assertFalse(report["complete"])
        self.assertEqual(report["source_error_codes"], 1)
        db = sqlite3.connect(self.output.with_suffix(".db"))
        try:
            self.assertEqual(db.execute(
                "SELECT status FROM source_requests WHERE code='0207412010'"
            ).fetchone()[0], "source_error")
        finally:
            db.close()

    def test_bounded_partial_is_not_complete(self):
        report = audit(str(self.catalog), str(self.focused), str(self.output),
                       version="v4.0.1626", as_of=TODAY, limit=1,
                       fetcher=lambda c, v: rows_csv([]))
        self.assertEqual(report["attempted_codes"], 1)
        self.assertEqual(report["candidate_gap_codes"], 2)
        self.assertFalse(report["complete"])
        with self.assertRaises(ValueError):
            audit(str(self.catalog), str(self.focused), str(self.output),
                  version="latest", as_of=TODAY, fetcher=lambda c, v: rows_csv([]))


if __name__ == "__main__":
    unittest.main()
