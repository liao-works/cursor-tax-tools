"""Do not call a stale, torn, corrupt, wrong-pin DB release 'fresh'."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from src.target_duty.production_freshness_watch import validate,select

NOW=datetime(2026,10,15,12,0,tzinfo=timezone.utc)
STAMP="2026-10-15T11:00:00Z"
VERSION="data-38567997207"


def fixture():
    with tempfile.TemporaryDirectory() as root:
        path=Path(root)/"test.db"
        with sqlite3.connect(path) as db:
            db.executescript("""
            CREATE TABLE tariffs(
             code TEXT PRIMARY KEY, rate TEXT, north_ireland_rate TEXT,
             description TEXT, url TEXT);
            INSERT INTO tariffs VALUES ('0101210000','5%','', 'sample','/gb');
            """)
        db_bytes=path.read_bytes()
    m={"version":VERSION,"record_count":1,"file_size":len(db_bytes),
       "file_hash":"sha256:"+hashlib.sha256(db_bytes).hexdigest(),
       "timestamp":"2026-10-15T11:00:00Z"}
    meta=json.dumps(m).encode()
    def a(name,content,stamp=STAMP):
        return {"name":name,"size":len(content),"updated_at":stamp}
    latest={"tag_name":"latest-data","assets":[
        a("tariffs.db",db_bytes),a("metadata.json",meta)]}
    pinned={"tag_name":VERSION,"assets":[
        a("tariffs.db",db_bytes),a("metadata.json",meta)]}
    return latest,meta,db_bytes,pinned,db_bytes


class ProductionFreshnessTests(unittest.TestCase):
    def test_fresh_immutable_sha_and_sqlite_can_pass_watch_but_not_ni_semantics(self):
        data=fixture()
        r=validate(*data,now=NOW)
        self.assertTrue(r["fresh"])
        self.assertTrue(r["db_and_immutable_match"])
        self.assertFalse(r["ni_other_payable_verified"])
        self.assertFalse(r["production_eligible_from_this_check"])

    def test_stale_latest_release_fails_freshness(self):
        latest,meta,db,pinned,pindb=fixture()
        for a in latest["assets"]:
            a["updated_at"]="2026-10-01T11:00:00Z"
        r=validate(latest,meta,db,pinned,pindb,now=NOW)
        self.assertFalse(r["fresh"])
        self.assertGreater(r["actual_age_hours"],300)

    def test_mismatched_live_and_immutable_db_rejected(self):
        latest,meta,db,pinned,pindb=fixture()
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            validate(latest,meta,db,pinned,pindb+b"x",now=NOW)

    def test_torn_two_asset_update_rejected(self):
        latest,meta,db,pinned,pindb=fixture()
        latest["assets"][1]["updated_at"]="2026-10-15T10:00:00Z"
        with self.assertRaisesRegex(ValueError,"too far apart"):
            validate(latest,meta,db,pinned,pindb,now=NOW)

    def test_unexpected_tag_or_metadata_version_rejected(self):
        latest,meta,db,pinned,pindb=fixture()
        pinned["tag_name"]="data-unrelated"
        with self.assertRaisesRegex(ValueError,"does not match"):
            validate(latest,meta,db,pinned,pindb,now=NOW)

    def test_missing_database_asset_is_failure(self):
        latest,meta,db,pinned,pindb=fixture()
        latest["assets"]=latest["assets"][:1]
        with self.assertRaisesRegex(ValueError,"Missing database"):
            validate(latest,meta,db,pinned,pindb,now=NOW)

    def test_sqlite_corruption_is_failure_even_if_manifest_hash_matches(self):
        latest,meta,db,pinned,pindb=fixture()
        bad=b"not a valid sqlite database"
        m=json.loads(meta)
        m["file_size"]=len(bad)
        m["file_hash"]="sha256:"+hashlib.sha256(bad).hexdigest()
        meta=json.dumps(m).encode()
        latest["assets"][0]["size"]=len(bad)
        latest["assets"][1]["size"]=len(meta)
        pinned["assets"][0]["size"]=len(bad)
        pinned["assets"][1]["size"]=len(meta)
        with self.assertRaises(sqlite3.DatabaseError):
            validate(latest,meta,bad,pinned,bad,now=NOW)

    def test_wrong_record_count_rejected(self):
        latest,meta,db,pinned,pindb=fixture()
        m=json.loads(meta)
        m["record_count"]=2
        meta=json.dumps(m).encode()
        latest["assets"][1]["size"]=len(meta)
        pinned["assets"][1]["size"]=len(meta)
        with self.assertRaisesRegex(ValueError,"record_count"):
            validate(latest,meta,db,pinned,pindb,now=NOW)


if __name__=="__main__":
    unittest.main()
