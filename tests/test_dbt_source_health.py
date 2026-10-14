"""Latest alias errors must be exposed, never silently relabel pinned old data."""
import io
import unittest
import urllib.error
from src.target_duty.dbt_source_health import probe

class FakeHTTP:
    def __init__(self,url,status=200):
        self.url=url
        self.status=status
    def geturl(self):
        return self.url
    def __enter__(self):
        return self
    def __exit__(self,*_):
        pass

class LatestSourceHealthTests(unittest.TestCase):
    def test_latest_alias_resolves_the_replayed_exact_pin(self):
        def success(req,timeout):
            self.assertIn("/versions/latest/",req.full_url)
            self.assertEqual(req.headers.get("Range"),"bytes=0-128")
            return FakeHTTP(req.full_url.replace("/latest/","/v4.0.1626/"))
        result=probe("v4.0.1626",opener=success)
        self.assertTrue(result["latest_alias_usable"])
        self.assertTrue(result["freshness_proven"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["should_publish"])

    def test_latest_404_does_not_fall_back_to_pinned(self):
        calls=[]
        def unavailable(req,timeout):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url,404,"missing",{},None)
        result=probe("v4.0.1626",opener=unavailable)
        self.assertEqual(len(calls),1)
        self.assertEqual(result["http_status"],404)
        self.assertFalse(result["freshness_proven"])
        self.assertFalse(result["latest_alias_usable"])
        self.assertIsNone(result["resolved_version"])
        self.assertIn("404",result["error"])
        self.assertFalse(result["should_publish"])

    def test_later_official_version_requires_new_replay(self):
        result=probe("v4.0.1626",opener=lambda req,timeout:
            FakeHTTP(req.full_url.replace("/latest/","/v4.0.1627/")))
        self.assertTrue(result["latest_alias_usable"])
        self.assertEqual(result["resolved_version"],"v4.0.1627")
        self.assertFalse(result["freshness_proven"])

    def test_alias_not_immutable_does_not_pass(self):
        result=probe("v4.0.1626",opener=lambda req,timeout:
            FakeHTTP(req.full_url))
        self.assertFalse(result["freshness_proven"])
        self.assertFalse(result["latest_alias_usable"])

    def test_bad_input_and_network_failure(self):
        with self.assertRaisesRegex(ValueError,"immutable"):
            probe("latest")
        result=probe("v4.0.1626",opener=lambda req,timeout:
            (_ for _ in ()).throw(urllib.error.URLError("network")))
        self.assertFalse(result["freshness_proven"])
        self.assertIn("network",result["error"])


if __name__=="__main__":
    unittest.main()
