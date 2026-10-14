"""Official DBT newest-version discovery never falls back to stale known pins."""
import io
import json
import unittest
import urllib.error

from src.target_duty.dbt_version_discovery import (
    BASE,discover,inspect_csvw,parse_tables,parse_versions,version_key
)
from src.target_duty.catalog import FIELDS as COMMODITY_FIELDS
from src.target_duty.gap_measures import REQUIRED as MEASURE_FIELDS


class FakeHTTP:
    status=200
    def __init__(self,url,payload):
        self.url=url
        self.payload=json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def geturl(self):
        return self.url
    def read(self,length):
        return self.payload[:length]


def inventory(*, newest="v4.0.1628", tables=None):
    def fake(req,timeout):
        assert timeout==35
        if req.full_url.endswith("/versions?format=json"):
            # Deliberately unsorted to prevent trusting API response order.
            return FakeHTTP(req.full_url,{"versions":[
                {"id":"v4.0.1626"},{"id":newest},{"id":"v4.0.999"}]})
        if req.full_url.endswith("/versions/"+newest+"/tables?format=json"):
            return FakeHTTP(req.full_url,{"tables":[{"id":x} for x in
                (tables if tables is not None else ["commodities-report","measures-on-declarable-commodities"])]})
        if req.full_url.endswith("/versions/"+newest+"/metadata?format=csvw"):
            return FakeHTTP(req.full_url,{"tables":[
                {"id":"commodities-report","tableSchema":{"columns":[
                    {"name":s} for s in COMMODITY_FIELDS]}},
                {"id":"measures-on-declarable-commodities","tableSchema":{"columns":[
                    {"name":s} for s in sorted(MEASURE_FIELDS)]}},
            ]})
        raise AssertionError("Unexpected metadata request "+req.full_url)
    return fake


class DBTDiscoveryTests(unittest.TestCase):
    def test_semantic_versions_and_table_preflight_not_rate_certification(self):
        result=discover(previous_pin="v4.0.1626",opener=inventory())
        self.assertEqual(result["newest_official_version"],"v4.0.1628")
        self.assertEqual(result["state"],"ready_for_full_data_replay")
        self.assertTrue(result["schema_compatibility_proven"])
        self.assertTrue(result["newer_than_replayed"])
        self.assertTrue(result["required_tables_present"])
        self.assertTrue(result["schema_compatibility_proven"])
        self.assertFalse(result["full_data_replayed_for_newest"])
        self.assertFalse(result["freshness_proven"])
        self.assertFalse(result["publish_eligible"])

    def test_missing_required_full_table_never_picks_older_compatible_version(self):
        result=discover(previous_pin="v4.0.1626",opener=inventory(
            tables=["commodities","measures-on-declarable-commodities"]))
        self.assertEqual(result["newest_official_version"],"v4.0.1628")
        self.assertEqual(result["state"],"unsupported_newest_tables")
        self.assertEqual(result["missing_required_tables"],["commodities-report"])
        self.assertFalse(result["publish_eligible"])

    def test_official_csvw_missing_measure_expression_blocks_refresh(self):
        columns=[{"name":s} for s in sorted(MEASURE_FIELDS-{"measure__duty_expression"})]
        doc={"tables":[
            {"id":"commodities-report","tableSchema":{"columns":[
                {"name":n} for n in COMMODITY_FIELDS]}},
            {"id":"measures-on-declarable-commodities","tableSchema":{"columns":columns}},
        ]}
        out=inspect_csvw(doc)
        self.assertFalse(out["source_column_schema_compatible"])
        self.assertEqual(out["required_columns_missing"]
                         ["measures-on-declarable-commodities"],
                         ["measure__duty_expression"])
        with self.assertRaisesRegex(ValueError,"Duplicated"):
            inspect_csvw({"tables":[{"id":"commodities-report",
                                     "tableSchema":{"columns":[{"name":"a"},{"name":"a"}]}}]})

    def test_real_official_csvw_url_format_and_ods_duplicates(self):
        doc={"tables":[
            {"url":"tables/commodities-report/data?format=csv&download",
             "tableSchema":{"columns":[{"name":x} for x in COMMODITY_FIELDS]}},
            {"url":"tables/commodities-report/data?format=ods&download",
             "tableSchema":{"columns":[{"name":x} for x in COMMODITY_FIELDS]}},
            {"url":"tables/measures-on-declarable-commodities/data?format=csv&download",
             "tableSchema":{"columns":[{"name":x} for x in MEASURE_FIELDS]}},
            {"url":"reports/measures-on-declarable-commodities/data?format=ods&download",
             "tableSchema":{"columns":[{"name":x} for x in MEASURE_FIELDS]}},
        ]}
        x=inspect_csvw(doc)
        self.assertTrue(x["source_column_schema_compatible"])
        self.assertEqual(x["official_tables_with_metadata"],2)
        self.assertEqual(x["required_columns_missing"]
                         ["measures-on-declarable-commodities"],[])

    def test_version_inventory_failure_is_explicit(self):
        calls=[]
        def missing(req,timeout):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url,404,"missing",{},None)
        result=discover(previous_pin="v4.0.1626",opener=missing)
        self.assertEqual(len(calls),1)
        self.assertEqual(result["state"],"source_unavailable")
        self.assertEqual(result["version_discovered"],False)
        self.assertIsNone(result["newest_official_version"])
        self.assertIn("404",result["error"])

    def test_registered_newest_but_unpublished_tables_blocks_latest_without_fallback(self):
        def incomplete(req,timeout):
            if req.full_url.endswith("/versions?format=json"):
                return FakeHTTP(req.full_url,{"versions":[
                    {"id":"v4.0.1626"},{"id":"v4.0.1627"}]})
            if req.full_url.endswith("/versions/v4.0.1627/tables?format=json"):
                return FakeHTTP(req.full_url,{"tables":[]})
            if req.full_url.endswith("/versions/v4.0.1626/tables?format=json"):
                return FakeHTTP(req.full_url,{"tables":[
                    {"id":"commodities-report"},
                    {"id":"measures-on-declarable-commodities"}]})
            raise AssertionError(req.full_url)
        result=discover(previous_pin="v4.0.1626",opener=incomplete)
        self.assertEqual(result["newest_official_version"],"v4.0.1627")
        self.assertEqual(result["state"],"newest_version_registered_without_tables")
        self.assertTrue(result["newest_version_no_tables"])
        self.assertEqual(result["table_inventory_diagnostic"]["tables_length"],0)
        self.assertIn("commodities-report",result["older_pinned_tables_for_diagnostics"])
        self.assertFalse(result["required_tables_present"])
        self.assertFalse(result["freshness_proven"])
        self.assertFalse(result["publish_eligible"])

    def test_broken_table_inventory_does_not_mark_version_usable(self):
        def broken(req,timeout):
            if req.full_url.endswith("/versions?format=json"):
                return FakeHTTP(req.full_url,{"versions":[{"id":"v4.0.1628"}]})
            raise urllib.error.URLError("offline")
        result=discover(previous_pin="v4.0.1626",opener=broken)
        self.assertEqual(result["state"],"table_inventory_unavailable")
        self.assertTrue(result["version_discovered"])
        self.assertFalse(result["required_tables_present"])
        self.assertFalse(result["publish_eligible"])

    def test_unordered_lexicographic_numbers_sort_as_semver(self):
        self.assertEqual(parse_versions({"versions":[
            {"id":"v4.0.99"},{"id":"v4.0.100"},{"id":"v3.99.999"}]}),
            ["v4.0.100","v4.0.99","v3.99.999"])
        self.assertEqual(version_key("v4.0.100"),(4,0,100))
        with self.assertRaises(ValueError):
            version_key("latest")

    def test_duplicate_and_bad_metadata_rejected(self):
        for bad in (
            {"versions":[{"id":"v4.0.1"},{"id":"v4.0.1"}]},
            {"versions":[{"id":"latest"}]},
            {"versions":[]},
            {"versions":[{"id":"v4.0.1-rc1"}]},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_versions(bad)
        with self.assertRaises(ValueError):
            parse_tables({"tables":[{"id":"commodities"},{"id":"commodities"}]})
        with self.assertRaises(ValueError):
            parse_tables({"tables":[{"id":"../evil"}]})

    def test_cross_origin_redirect_rejected(self):
        class Cross(FakeHTTP):
            def geturl(self):
                return "https://fake.example.com/versions?format=json"
        def fake(req,timeout):
            return Cross(req.full_url,{"versions":[{"id":"v4.0.1628"}]})
        result=discover(previous_pin="v4.0.1626",opener=fake)
        self.assertEqual(result["state"],"source_unavailable")
        self.assertFalse(result["freshness_proven"])
        self.assertIn("another origin",result["error"])


if __name__ == "__main__":
    unittest.main()
