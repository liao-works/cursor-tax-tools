"""Official DBT newest-version discovery never falls back to stale known pins."""
import io
import json
import unittest
import urllib.error

from src.target_duty.dbt_version_discovery import (
    BASE,discover,parse_tables,parse_versions,version_key
)


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
                (tables or ["commodities-report","measures-on-declarable-commodities"])]})
        raise AssertionError("Unexpected metadata request "+req.full_url)
    return fake


class DBTDiscoveryTests(unittest.TestCase):
    def test_semantic_versions_and_table_preflight_not_rate_certification(self):
        result=discover(previous_pin="v4.0.1626",opener=inventory())
        self.assertEqual(result["newest_official_version"],"v4.0.1628")
        self.assertEqual(result["state"],"schema_preflight_required")
        self.assertTrue(result["newer_than_replayed"])
        self.assertTrue(result["required_tables_present"])
        self.assertFalse(result["schema_compatibility_proven"])
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
